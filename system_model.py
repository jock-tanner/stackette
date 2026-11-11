"""
The whole machine in Python, for running kernels without the board: the
reference interpreter of cpu_model.py (Ref) plus what the System of system.py
has around the CPU: the system and the user context, TRAP, RETI, interrupts and
faults, the MMU of four spaces with a map for each context, the physical memory
(SDRAM, flash, peripherals), the context device, the interrupt controller, the
timer and the UART transmitter. No Amaranth in here.

The CPU is not cycle accurate: an instruction takes CYCLES clocks of the timer.
A request to a page with no memory is a fault; the instruction is undone (the
registers and the memory writes it made) and the system context runs with the
cause in ID, like cpu.py does.

    python system_model.py flash.bin [--steps N] [--uart-in TEXT]
"""
import argparse
import sys

from cpu_model import Ref, junk, sext

CODE, RSTACK, PSTACK, DATA = range(4)
FLASH_PAGE = 0x2000
FLASH_PAGES = 0x100
PERIPH_PAGE = 0x8000
SDRAM_PAGES = 0x2000
CYCLES = 5

ID_IRQ = 1 << 6
ID_TRAP = 2 << 6
ID_FAULT = 3 << 6
FAULT_ILLEGAL, FAULT_MEM, FAULT_STACK, FAULT_WB, FAULT_DIV = range(5)

REGS = ('ip', 'rsp', 'pfp', 'mbp', 'midx', 'sw', 'psp')


class Fault(Exception):
    def __init__(self, code, addr=0):
        super().__init__(code, addr)
        self.code = code
        self.addr = addr


def default_page(index):
    page, space = index & 15, index >> 4
    if page == 0:
        return FLASH_PAGE
    if space == DATA and page == 15:
        return PERIPH_PAGE
    return page


class SystemModel(Ref):
    def __init__(self, flash=b'', cycles=CYCLES):
        super().__init__()
        self.cycles = cycles
        self.flash = bytes(flash)
        self.ram = {}
        self.out = bytearray()
        self.uart_in = []
        self.user = False
        self.saved = {}
        self.ip = 0
        self.space = PSTACK
        self.opspace = DATA
        self.fault_class = FAULT_STACK
        self.journal = None
        self.mmu = [default_page(i % 64) for i in range(128)]
        self.id = 0
        self.fault_ip = 0
        self.fault_addr = 0
        self.mask = 0
        self.timer = {'count': 0, 'compare': 0, 'run': 0, 'reload': 0,
                      'match': 0, 'prescale': 0, 'divider': 0}
        self.spi_dev = None     # the chip on the SPI master, see enc28j60_model
        self.spi_cs = 0
        self.spi_div = 1
        self.spi_result = 0
        self.skipping = False
        self.istart = 0
        self.steps = 0
        self.reset_system()
        self.saved = {
            'ip': 0, 'rsp': 0, 'pfp': 0, 'mbp': 0, 'midx': 0, 'sw': 0,
            'psp': 0,
        }

    # ---- the registers of the two contexts ----

    def get_reg(self, name):
        return {
            'ip': self.ip, 'rsp': self.rsp, 'pfp': self.pfp, 'mbp': self.mbp,
            'midx': self.midx, 'sw': self.flags, 'psp': self.psp & 0xffff,
        }[name]

    def set_reg(self, name, v):
        v &= 0xffff
        if name == 'sw':
            self.set_flags(v)
        else:
            setattr(self, name, v)

    def swap_context(self):
        mine = {n: self.get_reg(n) for n in REGS}
        for n in REGS:
            self.set_reg(n, self.saved[n])
        self.saved = mine
        self.user = not self.user

    def reset_system(self):
        for n, v in zip(REGS, (0, 0, 0xf000, 0, 0, 0, 0xf000)):
            self.set_reg(n, v)
        self.user = False
        for i in range(64):
            self.mmu[i] = default_page(i)
        self.skipping = False

    # ---- physical memory and the devices ----

    def phys_rd16(self, page, off):
        # the word at byte offset `off` (even) of the physical page
        if page < SDRAM_PAGES:
            a = page << 12 | off
            return self.ram.get(a >> 1, junk(a))
        if FLASH_PAGE <= page < FLASH_PAGE + FLASH_PAGES:
            i = (page - FLASH_PAGE) << 12 | off
            if i + 1 < len(self.flash):
                return self.flash[i] | self.flash[i + 1] << 8
            return 0xffff
        if page == PERIPH_PAGE:
            return self.periph_rd(off)
        raise KeyError(page)

    def phys_wr16(self, page, off, v):
        if page < SDRAM_PAGES:
            a = page << 12 | off
            if self.journal is not None:
                self.journal.append((a >> 1, self.ram.get(a >> 1)))
            self.ram[a >> 1] = v & 0xffff
        elif FLASH_PAGE <= page < FLASH_PAGE + FLASH_PAGES:
            pass
        elif page == PERIPH_PAGE:
            self.periph_wr(off, v)
        else:
            raise KeyError(page)

    def present(self, page):
        return (
            page < SDRAM_PAGES or page == PERIPH_PAGE
            or FLASH_PAGE <= page < FLASH_PAGE + FLASH_PAGES
        )

    def translate(self, space, a, code):
        page = self.mmu[self.user << 6 | space << 4 | a >> 12]
        if not self.present(page):
            raise Fault(code, a)
        return page, a & 0xffe

    def rd16(self, a):
        page, off = self.translate(self.space, a & 0xfffe, self.fault_class)
        return self.phys_rd16(page, off)

    def wr16(self, a, v):
        a &= 0xfffe
        page, off = self.translate(self.space, a, self.fault_class)
        self.phys_wr16(page, a & 0xffe, v)

    def rd(self, a, s):
        save = self.space, self.fault_class
        self.space, self.fault_class = self.opspace, FAULT_MEM
        try:
            return super().rd(a, s)
        finally:
            self.space, self.fault_class = save

    def wr(self, a, s, v):
        save = self.space, self.fault_class
        self.space, self.fault_class = self.opspace, FAULT_MEM
        try:
            if s and not a & 1:
                page, off = self.translate(self.space, a, FAULT_MEM)
                if page == PERIPH_PAGE and 0x040 <= off < 0x048:
                    self.periph_wr(off, v & 0xff, True)
                    return
            super().wr(a, s, v)
        finally:
            self.space, self.fault_class = save

    def call(self, target, ret):
        save = self.space, self.fault_class
        self.space, self.fault_class = RSTACK, FAULT_MEM
        try:
            return super().call(target, ret)
        finally:
            self.space, self.fault_class = save

    def ret(self, n):
        save = self.space, self.fault_class
        self.space, self.fault_class = RSTACK, FAULT_MEM
        try:
            if n is None:
                self.space = PSTACK
                n = self.rd16(self.pfp)
                self.space = RSTACK
            ip = self.rd16(self.rsp)
            pfp = self.rd16(self.rsp + 2)
        finally:
            self.space, self.fault_class = save
        self.pfp = pfp
        self.rsp = (self.rsp + 4) & 0xffff
        self.psp = (self.psp + 2 * n) & 0xffff
        return ip

    # the peripherals: byte offsets in the page (0xf000 .. in the default map)

    def periph_rd(self, off):
        if off == 0x000:
            return self.uart_in.pop(0) if self.uart_in else 0
        if off == 0x002:
            return 1 | (2 if self.uart_in else 0)
        if off == 0x008:
            return self.lines()
        if off == 0x00a:
            return self.mask
        if off == 0x00c:
            return self.lines() & self.mask
        if 0x010 <= off < 0x01a:
            t = self.timer
            return {
                0x10: t['count'], 0x12: t['compare'],
                0x14: t['run'] | t['reload'] << 1, 0x16: t['match'],
                0x18: t['prescale'],
            }.get(off, 0)
        if 0x020 <= off < 0x034:
            k = (off - 0x020) >> 1
            if k < 7:
                return self.saved[REGS[k]]
            return (self.id, self.fault_ip, self.fault_addr)[k - 7] if k < 10 else 0
        if 0x040 <= off < 0x048:
            return self.spi_rd(off)
        if 0x100 <= off < 0x200:
            return self.mmu[(off - 0x100) >> 1]
        return 0

    def periph_wr(self, off, v, byte=False):
        if off == 0x000:
            self.out.append(v & 0xff)
        elif off == 0x00a:
            self.mask = v & 0xff
        elif 0x010 <= off < 0x01a:
            t = self.timer
            if off == 0x10:
                t['count'] = v
                t['divider'] = t['prescale']
            elif off == 0x12:
                t['compare'] = v
            elif off == 0x14:
                t['run'], t['reload'] = v & 1, v >> 1 & 1
            elif off == 0x16:
                if v & 1:
                    t['match'] = 0
            elif off == 0x18:
                t['prescale'] = v
                t['divider'] = v
        elif 0x020 <= off < 0x034:
            k = (off - 0x020) >> 1
            if k < 7:
                n = REGS[k]
                self.saved[n] = v & (0x1f if n == 'sw' else 0xffff)
        elif 0x040 <= off < 0x048:
            self.spi_wr(off, v, byte)
        elif 0x100 <= off < 0x200:
            self.mmu[(off - 0x100) >> 1] = v & 0xffff

    # the SPI master (spi_master.py): a transfer is done at once, and the timer
    # is clocked for the time that it takes

    def spi_xfer(self, b):
        dev = self.spi_dev
        cs_n = 0 if self.spi_cs else 1
        r = 0
        for i in range(8):
            mosi = b >> (7 - i) & 1
            if dev:
                dev.pins(cs_n, 0, mosi)
                dev.pins(cs_n, 1, mosi)
                r = r << 1 | dev.miso
                dev.pins(cs_n, 0, mosi)
            else:
                r <<= 1
        return r

    def spi_rd(self, off):
        if off == 0x040:
            return self.spi_result
        if off == 0x042:
            return self.spi_cs
        if off == 0x044:
            return self.spi_div
        if off == 0x046:
            return 2 if self.spi_dev and not self.spi_dev.int else 0
        return 0

    def spi_wr(self, off, v, byte):
        if off == 0x040:
            data = [v & 0xff] if byte else [v & 0xff, v >> 8 & 0xff]
            r = [self.spi_xfer(b) for b in data]
            self.spi_result = r[0] | (r[1] << 8 if len(r) > 1 else 0)
            self.clock(len(data) * 16 * (max(self.spi_div, 1) + 1))
        elif off == 0x042:
            cs = v & 1
            if cs != self.spi_cs and self.spi_dev:
                self.spi_dev.pins(0 if cs else 1, 0, 0)
            self.spi_cs = cs
        elif off == 0x044:
            self.spi_div = v & 0xff

    def lines(self):
        return self.timer['match'] | (1 if self.uart_in else 0) << 1

    def clock(self, n):
        t = self.timer
        if not t['run']:
            return
        for _ in range(n):
            if t['divider'] == 0:
                t['divider'] = t['prescale']
                if t['count'] == t['compare']:
                    t['match'] = 1
                    t['count'] = 0 if t['reload'] else (t['count'] + 1) & 0xffff
                else:
                    t['count'] = (t['count'] + 1) & 0xffff
            else:
                t['divider'] -= 1

    # ---- the instruction cycle ----

    def snapshot(self):
        return {n: self.get_reg(n) for n in REGS} | {'skip': self.skipping}

    def restore(self, snap):
        for n in REGS:
            self.set_reg(n, snap[n])
        self.skipping = snap['skip']

    def enter_system(self, code, fault_ip, fault_addr=0):
        self.id = code
        self.fault_ip = fault_ip
        self.fault_addr = fault_addr
        self.skipping = False
        if self.user:
            self.swap_context()
        else:
            # a fault of the system context: a soft reset
            self.reset_system()

    def fetch(self, a):
        self.space, self.fault_class = CODE, FAULT_MEM
        try:
            return self.rd16(a)
        finally:
            self.space = PSTACK

    def step_one(self):
        """One instruction (or one entry of the system context)."""
        self.steps += 1
        self.clock(self.cycles)
        if self.user and self.lines() & self.mask:
            act = self.lines() & self.mask
            n = (act & -act).bit_length() - 1
            if self.skipping:
                # the SKIP before the abandoned instruction runs again
                self.ip = self.istart
            self.enter_system(ID_IRQ | n, self.ip)
            return
        start = self.ip
        self.istart = start
        snap = self.snapshot()
        self.journal = []
        try:
            self.exec_at(start)
        except Fault as f:
            for a, old in reversed(self.journal):
                if old is None:
                    self.ram.pop(a, None)
                else:
                    self.ram[a] = old
            self.restore(snap)
            self.journal = None
            self.enter_system(ID_FAULT | f.code, start, f.addr)
            return
        self.journal = None

    def exec_at(self, pc):
        w = self.fetch(pc)
        if self.skipping:
            self.skipping = False
            self.ip = (pc + 2 * self.length(w)) & 0xffff
            return
        if self.is_skip(w):
            self.skipping = bool(self.cond((w >> 6) & 15))
            self.ip = (pc + 2) & 0xffff
            return
        n = self.length(w)
        words = [self.fetch(pc + 2 * k) for k in range(n)]
        nxt = (pc + 2 * n) & 0xffff
        fmt = w >> 14
        if fmt == 3 and (w >> 12) & 3 == 3 and w & 0x800:
            op = (w >> 6) & 31
            p = w & 63
            if op == 0 and p == 0:
                self.ip = nxt
                return
            if op == 2 and p == 0:
                if self.user:
                    raise Fault(FAULT_ILLEGAL)
                self.ip = nxt
                self.swap_context()
                return
            if op == 4:
                self.ip = nxt
                self.enter_system(ID_TRAP | p, nxt)
                return
            if op not in (1, 5, 6, 7, 9):
                raise Fault(FAULT_ILLEGAL)
        if fmt == 3 and (w >> 12) & 3 == 2 and (w >> 10) & 3 == 1 \
                and not w & 0x7f and (w >> 9) & 1:
            # DIV: a division by zero is a fault
            bits = 8 if (w >> 7) & 1 else 16
            if self.item(0) & ((1 << bits) - 1) == 0:
                raise Fault(FAULT_DIV)
        if not self.legal(w):
            raise Fault(FAULT_ILLEGAL)
        self.opspace = self.opspace_for(w, words)
        target = self.step(words, nxt)
        self.ip = nxt if target is None else target & 0xffff
        self.psp &= 0xffff

    @staticmethod
    def legal(w):
        """The two-operand formats (0, 1, 2): the combinations that exist."""
        fmt = w >> 14
        op = (w >> 10) & 15
        dd = (w >> 8) & 3
        if fmt == 3:
            return True
        if op > 13:
            return False
        if fmt == 0:
            return op not in (9, 10, 11)
        if fmt == 1:
            if op == 11:
                return not w & 0x80
            if op == 10:
                return dd < 2
            return True
        f = w & 0x7f
        e = f & 1
        reg = f < 16
        ptr = f >> 3 == 2
        tos_ptr = ptr and (f >> 1) & 3 == 3
        absm = f >> 1 == 0x10
        imm16 = f == 0x22
        spm = bool(f & 0x40)
        k = (f >> 1) & 0x1f
        gen_ok = reg or ptr or absm or imm16 or (spm and not (k < 4 and e))
        if op == 11:
            return False
        if op == 9:
            return (gen_ok and not imm16 and not tos_ptr) or (
                tos_ptr and bool(dd & 1))
        if op == 10:
            mem = (ptr and not tos_ptr) or absm or (spm and k >= 4) or tos_ptr
            return dd < 2 and mem
        return gen_ok

    def opspace_for(self, w, words):
        fmt = w >> 14
        if fmt == 1:
            return PSTACK
        if fmt == 2:
            from cpu_model import gen_operand
            g = gen_operand(w)
            if g is not None and g[0] == 6:
                return PSTACK
        return DATA

    def uart_feed(self, data):
        self.uart_in.extend(data)


def run(m, steps, quiet_stop=True):
    for _ in range(steps):
        before = (m.ip, m.user)
        m.step_one()
        waiting = m.user and m.mask and (m.timer['run'] or m.uart_in)
        if (m.ip, m.user) == before and not m.skipping and not waiting:
            # a branch to itself in the same context: stopped
            return m.steps
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('flash')
    ap.add_argument('--steps', type=int, default=5000000)
    ap.add_argument('--uart-in', default='')
    args = ap.parse_args()
    m = SystemModel(open(args.flash, 'rb').read())
    m.uart_feed(args.uart_in.encode())
    n = run(m, args.steps)
    sys.stdout.buffer.write(bytes(m.out))
    print(f'\n[{"halted" if n else "step limit"} after {m.steps} steps, '
          f'ip {m.ip:#06x}, {"user" if m.user else "system"}]',
          file=sys.stderr)


if __name__ == '__main__':
    main()
