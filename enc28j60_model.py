"""
A model of the ENC28J60 on the SPI pins, for the programs that talk to it on
the model of the system (system_model.py: `m.spi_dev = ENC28J60()`): the SPI
slave in mode 0 (the data is taken at the rising edge of SCLK, MISO changes at
the falling edge), the four banks of the control registers, the buffer memory (RBM, WBM with the
auto-increment of the pointers), the system reset,
ESTAT.CLKRDY and the PHY registers behind the MII interface.

`phid1`, `phid2` are the values of the PHY registers 2 and 3; `present` False
is a board with no chip, where MISO reads `idle_miso`. CLKRDY comes up after
`clkrdy_reads` reads of ESTAT, MISTAT.BUSY goes down after `busy_reads` reads
of MISTAT.
"""

# the registers (bank, address) that are MAC or MII registers: a read of one
# of them has a dummy byte after the command
MAC_MII = {
    2: {0x00, 0x02, 0x03, 0x04, 0x06, 0x07, 0x08, 0x09, 0x0a, 0x0b,
        0x12, 0x14, 0x16, 0x17, 0x18, 0x19},
    3: {0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x0a},
}
ESTAT, ECON2, ECON1 = 0x1d, 0x1e, 0x1f
MICMD, MIREGADR, MIRDL, MIRDH, MISTAT = 0x12, 0x14, 0x18, 0x19, 0x0a
EREVID = 0x12
ERDPTL, ERDPTH, EWRPTL, EWRPTH = 0x00, 0x01, 0x02, 0x03   # bank 0
BUF_SIZE = 0x2000
EIE, EIR = 0x1b, 0x1c
ETXSTL, ETXNDL, ERXSTL, ERXNDL, ERXRDPTL = 0x04, 0x06, 0x08, 0x0a, 0x0c
EPKTCNT = 0x19
PKTIF, TXIF, RXERIF = 0x40, 0x08, 0x01
INTIE, PKTIE, TXIE, RXERIE = 0x80, 0x40, 0x08, 0x01
RXEN, TXRTS = 0x04, 0x08
PKTDEC = 0x40


class ENC28J60:
    def __init__(
        self, *, present=True, phid1=0x0083, phid2=0x1400, revid=6,
        clkrdy_reads=3, busy_reads=4, idle_miso=0,
    ):
        self.present = present
        self.phy = {2: phid1, 3: phid2}
        self.revid = revid
        self.clkrdy_reads = clkrdy_reads
        self.busy_reads = busy_reads
        self.idle_miso = idle_miso
        self.int = 1
        self.miso = idle_miso
        self.cs_n, self.sclk = 1, 0
        self.log = []               # the commands that were completed
        self.tx_frames = []         # the frames that were sent (TXRTS)
        self.rx_dropped = 0         # the frames that did not fit
        self.buf = bytearray(BUF_SIZE)
        self.reset()
        self.clkrdy = 1             # a power-up is not a reset: ready

    def reset(self):
        self.regs = {(b, a): 0 for b in range(4) for a in range(32)}
        self.regs[(0, ECON1)] = 0
        self.regs[(0, ECON2)] = 0x80
        self.regs[(0, ERXNDL)] = 0xff
        self.regs[(0, ERXNDL + 1)] = 0x1f
        self.rx_wr = 0              # where the next received frame goes
        self.clkrdy = 0
        self.int = 1
        self.estat_reads = 0
        self.busy = 0
        self.mirdata = 0

    @property
    def bank(self):
        return self.regs[(0, ECON1)] & 3

    def read_reg(self, a):
        bank = 0 if a >= 0x1b else self.bank
        if a == ESTAT:
            self.estat_reads += 1
            if self.estat_reads > self.clkrdy_reads:
                self.clkrdy = 1
            return self.clkrdy
        if a == EIR:
            v = self.regs[(0, EIR)] & ~PKTIF
            return v | (PKTIF if self.regs[(1, EPKTCNT)] else 0)
        if bank == 3 and a == MISTAT:
            if self.busy > 0:
                self.busy -= 1
            return 1 if self.busy else 0
        if bank == 3 and a == EREVID:
            return self.revid
        if bank == 2 and a == MIRDL:
            return self.mirdata & 0xff
        if bank == 2 and a == MIRDH:
            return self.mirdata >> 8
        return self.regs[(bank, a)]

    def write_reg(self, a, v, mask=0xff, op='wcr'):
        bank = 0 if a >= 0x1b else self.bank
        old = self.regs[(bank, a)]
        if op == 'bfs':
            v = old | v
        elif op == 'bfc':
            v = old & ~v
        if a == ECON2 and op == 'bfs' and v & PKTDEC:
            # PKTDEC does not stay: it takes a packet off the count
            v &= ~PKTDEC
            if self.regs[(1, EPKTCNT)]:
                self.regs[(1, EPKTCNT)] -= 1
        self.regs[(bank, a)] = v & 0xff
        if a == ECON1 and v & TXRTS:
            self.transmit()
        if bank == 0 and a in (ERXSTL, ERXSTL + 1):
            self.rx_wr = self.pointer(ERXSTL)
        self.update_int()
        if bank == 2 and a == MICMD and v & 1:
            # MIIRD: the PHY register is in MIRD after the busy time
            self.mirdata = self.phy.get(self.regs[(2, MIREGADR)] & 0x1f, 0)
            self.busy = self.busy_reads

    def update_int(self):
        """INT is low while an enabled flag is up (EIE.INTIE is the master)."""
        eir = self.read_reg_raw_eir()
        eie = self.regs[(0, EIE)]
        up = eir & eie & (PKTIE | TXIE | RXERIE | 0x02 | 0x10 | 0x20)
        self.int = 0 if (eie & INTIE and up) else 1

    def read_reg_raw_eir(self):
        return self.regs[(0, EIR)] | (PKTIF if self.regs[(1, EPKTCNT)] else 0)

    def transmit(self):
        """ECON1.TXRTS: the frame in ETXST..ETXND (the first byte is the
        per-packet control byte) goes out; TXRTS drops and TXIF comes up."""
        st = self.pointer(ETXSTL)
        nd = self.pointer(ETXNDL)
        frame = bytes(self.buf[st + 1:nd + 1])
        self.tx_frames.append(frame)
        self.regs[(0, ECON1)] &= ~TXRTS
        self.regs[(0, EIR)] |= TXIF
        self.log.append(('tx', len(frame)))

    def inject(self, frame):
        """A frame arrives from the wire (the CRC is added here)."""
        if not self.regs[(0, ECON1)] & RXEN or self.regs[(1, EPKTCNT)] >= 255:
            return False
        st = self.pointer(ERXSTL)
        nd = self.pointer(ERXNDL)
        size = nd - st + 1
        data = bytes(frame) + b'\0\0\0\0'
        total = 6 + len(data)
        total += total & 1
        rd = self.pointer(ERXRDPTL)
        free = (rd - self.rx_wr) % size       # the free bytes of the ring
        if total > free:
            self.regs[(0, EIR)] |= RXERIF
            self.rx_dropped += 1
            self.update_int()
            return False
        nxt = st + (self.rx_wr - st + total) % size
        hdr = bytes([nxt & 0xff, nxt >> 8, len(data) & 0xff, len(data) >> 8,
                     0x80, 0x00]) + data
        hdr += b'\0' * (total - len(hdr))
        p = self.rx_wr
        for b in hdr:
            self.buf[p] = b
            p += 1
            if p > nd:
                p = st
        self.rx_wr = nxt
        self.regs[(1, EPKTCNT)] += 1
        self.update_int()
        return True

    def pointer(self, lo):
        return self.regs[(0, lo)] | self.regs[(0, lo + 1)] << 8

    def set_pointer(self, lo, v):
        self.regs[(0, lo)] = v & 0xff
        self.regs[(0, lo + 1)] = v >> 8 & 0x1f

    def rbm(self):
        p = self.pointer(ERDPTL) % BUF_SIZE
        # in the ring of the receiver the pointer wraps from ERXND to ERXST
        nxt = (p + 1) % BUF_SIZE
        if p == self.pointer(ERXNDL):
            nxt = self.pointer(ERXSTL)
        self.set_pointer(ERDPTL, nxt)
        return self.buf[p]

    def wbm(self, byte):
        p = self.pointer(EWRPTL) % BUF_SIZE
        self.buf[p] = byte
        self.set_pointer(EWRPTL, (p + 1) % BUF_SIZE)

    def command(self, byte):
        """The command byte is complete: -> (kind, address)."""
        op, a = byte >> 5, byte & 0x1f
        if byte == 0xff:
            self.reset()
            self.log.append('reset')
            return 'reset', 0
        if a == 0x1a and op in (1, 3):
            return {1: 'rbm', 3: 'wbm'}[op], a
        return {0: 'rcr', 2: 'wcr', 4: 'bfs', 5: 'bfc'}.get(op, 'other'), a

    # ---- the pins ----

    def pins(self, cs_n, sclk, mosi):
        if not self.present:
            self.cs_n, self.sclk = cs_n, sclk
            return
        if cs_n:
            self.miso = self.idle_miso
            self.nbit = 0
            self.kind = None
        elif self.cs_n:
            # the chip is selected
            self.nbit = 0
            self.shin = 0
            self.nbyte = 0
            self.kind = None
            self.out = 0
        if not cs_n:
            if sclk and not self.sclk:
                self.shin = (self.shin << 1 | mosi) & 0xff
                self.nbit += 1
                if self.nbit == 8:
                    self.nbit = 0
                    self.byte_done(self.shin)
                    self.nbyte += 1
            elif not sclk and self.sclk:
                # the falling edge: the next bit of the answer
                self.miso = (self.out >> (7 - self.nbit)) & 1
        self.cs_n, self.sclk = cs_n, sclk

    def byte_done(self, byte):
        if self.nbyte == 0:
            self.kind, self.addr = self.command(byte)
            self.out = 0
            if self.kind == 'rbm':
                self.out = self.rbm()
            if self.kind == 'rcr':
                bank = 0 if self.addr >= 0x1b else self.bank
                if self.addr in MAC_MII.get(bank, ()):
                    self.dummy = True
                else:
                    self.dummy = False
                    self.out = self.read_reg(self.addr)
            return
        if self.kind == 'rbm':
            self.out = self.rbm()
        elif self.kind == 'wbm':
            self.wbm(byte)
        elif self.kind == 'rcr':
            if self.dummy and self.nbyte == 1:
                self.dummy = False
                self.out = self.read_reg(self.addr)
            else:
                self.out = 0
        elif self.kind in ('wcr', 'bfs', 'bfc') and self.nbyte == 1:
            self.write_reg(self.addr, byte, op=self.kind)
            self.log.append((self.kind, self.addr, byte))
        # the output of the next byte; the first bit shows at the falling edge
