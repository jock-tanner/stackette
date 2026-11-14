"""
ATA (IDE) PIO host for a CompactFlash card in True IDE mode (or a hard disk on
an IDE-to-CF adapter): the task file registers and the data port, one 16-bit
transfer at a time, with the timing of PIO mode 0 (cycle 600 ns) after the
reset and registers to make it faster.

The pins (`pins`, see IDEPins): DA[2:0], CS0#, CS1#, DIOR#, DIOW#, DD[15:0]
(the driver and the direction of the bus are separate: dd_o, dd_oe, dd_i),
INTRQ and IORDY. No RESET#, DMARQ, DMACK#, IOCS16# are needed: the reset of
the card is the software reset (SRST, bit 2 of the device control register),
and the DMA modes are not used. The card must be a master (the adapter selects
that) and the driver selects it with the DEV bit of the drive/head register.

Registers (word addresses; the port is 5 address bits x 16 data bits, the
byte address is twice the word address):

   0  DATA       16-bit data port, a read or a write of a word is one cycle
                 on the bus. The byte order is that of the memory: the first
                 byte of a sector is in the low byte of the first word. Use
                 only word accesses: a byte access is also a 16-bit cycle and
                 the other byte is lost.
   1  ERROR / FEATURES              (CS0#, DA = 1)
   2  SECTOR COUNT                  (DA = 2)
   3  LBA 7:0   (sector number)     (DA = 3)
   4  LBA 15:8  (cylinder low)      (DA = 4)
   5  LBA 23:16 (cylinder high)     (DA = 5)
   6  DRIVE/HEAD: bit 6 LBA, bit 4 DEV, bits 3:0 = LBA 27:24   (DA = 6)
   7  STATUS / COMMAND              (DA = 7); reading STATUS clears INTRQ
      The registers 1..7 are 8 bits wide: a read gives the low byte, a write
      sends the low byte.
   8  SETUP     read/write, 8 bits: clocks from the address to the strobe
   9  STROBE    read/write, 8 bits: clocks of the strobe, 4 at the least
  10  RECOVER   read/write, 8 bits: clocks after the strobe and its hold
                (2 clocks) before the next cycle may start
  11  STATUS of this controller; a write clears bit 1
                bit 0  INTRQ   the interrupt request of the card (a level,
                               also the interrupt line `irq`)
                bit 1  TIMEOUT IORDY did not come in about 1.3 ms: the cycle
                               was ended anyway
                bit 2  IORDY   the IORDY pin now
  12  RESET     bit 0: 1 holds the RESET# pin of the card low (a hardware
                reset), 0 releases it; 0 after the reset of the FPGA. The pin
                is optional: without a wire to the card it does nothing
  13            reserved: reads as 0, writes ignored
  16  BB_CTRL   the pins by hand, to find a fault of the wiring: bit 0 turns it
                on (the cycles of the bus are off the pins then, all of
                them come from the bits here), bit 1 CS0#, bit 2 CS1#,
                bit 3 DIOR#, bit 4 DIOW#, bits 7:5 DA, bit 8 drives DD, bit 9
                RESET#. 0 at the reset of the FPGA.
  17  BB_DD_OUT the 16 bits that DD gets when BB_CTRL bit 8 is set
  18  BB_DD_IN  (read) DD as it is on the pins
  19  BB_PINS   (read) bit 0 INTRQ, bit 1 IORDY, as they are on the pins
  20..31        reserved: read as 0, writes ignored
  14  ALTERNATE STATUS (read, does not clear INTRQ) / DEVICE CONTROL (write:
      bit 2 SRST, bit 1 nIEN)       (CS1#, DA = 6)
  15  DRIVE ADDRESS (read)          (CS1#, DA = 7)

The reset values of 8, 9 and 10 are PIO mode 0 at 50 MHz: 5 clocks of address
setup (70 ns at the least), 16 of strobe (290 ns), 8 of recovery, with the 2 of
the hold a cycle of 31 clocks (600 ns). The faster modes
of the card (mode 4: 25, 70, 120 ns) are for the software to set, after it
has asked the card with SET FEATURES.

A request for a register 0..7, 14 or 15 waits (req.ready is low) until the
cycle before is done; the response of a read comes when the strobe ends, with
the data that was on DD at the last clock of the strobe. A write is accepted
at once if the bus is idle, and the next request waits for the end of its
cycle. The registers 8..13 never wait. IORDY (a card that is not ready) holds
the strobe low; the pin is synchronized here. INTRQ is synchronized too.

The outputs come directly from registers, so they do not glitch.
"""
from amaranth import Cat, Module, Mux, Signal
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.wiring import Component, In, Out, Signature

from memory_port import MemoryPort

IDE_ADDR_WIDTH = 5
REG_DATA = 0
REG_SETUP = 8
REG_STROBE = 9
REG_RECOVER = 10
REG_STATUS = 11
REG_ALT_STATUS = 14
REG_BB_CTRL = 16
REG_BB_DD_OUT = 17
REG_BB_DD_IN = 18
REG_BB_PINS = 19

IDLE, SETUP, STROBE, HOLD, RECOV = range(5)

# PIO mode 0, in ns
T_SETUP = 70
T_STROBE = 290
T_CYCLE = 600
HOLD_CLOCKS = 2     # address and data stay after the strobe: 40 ns at 50 MHz
MIN_STROBE = 4
IORDY_TIMEOUT = 0xffff


def clocks(ns, clk_hz):
    """The number of clocks that last at least `ns`, and one more."""
    return -(-ns * clk_hz // 1_000_000_000) + 1


# the reset values of SETUP, STROBE and RECOVER at 50 MHz: 5 clocks (100 ns),
# 16 (320 ns) and 8, so that a cycle is 5 + 16 + 2 + 8 = 31 clocks (620 ns)
SETUP_DEFAULT = clocks(T_SETUP, 50_000_000)
STROBE_DEFAULT = clocks(T_STROBE, 50_000_000)
RECOVER_DEFAULT = max(
    0,
    clocks(T_CYCLE, 50_000_000) - SETUP_DEFAULT - STROBE_DEFAULT
    - HOLD_CLOCKS,
)


class IDEPins(Signature):
    """IDE pins, from the controller's point of view."""
    def __init__(self):
        super().__init__({
            'da': Out(3),
            'cs0_n': Out(1),
            'cs1_n': Out(1),
            'dior_n': Out(1),
            'diow_n': Out(1),
            'dd_o': Out(16),
            'dd_oe': Out(1),
            'dd_i': In(16),
            'intrq': In(1),
            'iordy': In(1),
            'reset_n': Out(1),
        })


class IDEPio(Component):
    bus: In(MemoryPort(IDE_ADDR_WIDTH))
    pins: Out(IDEPins())
    irq: Out(1)

    def __init__(self):
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        req, resp = self.bus.req, self.bus.resp
        pins = self.pins

        setup = Signal(8, init=SETUP_DEFAULT)
        strobe = Signal(8, init=STROBE_DEFAULT)
        recover = Signal(8, init=RECOVER_DEFAULT)
        timeout = Signal()

        iordy = Signal(init=1)
        intrq = Signal()
        m.submodules.sync_iordy = FFSynchronizer(pins.iordy, iordy, init=1)
        m.submodules.sync_intrq = FFSynchronizer(pins.intrq, intrq, init=0)

        state = Signal(3)
        cnt = Signal(8)
        wait = Signal(16)
        is_wr = Signal()
        cs0_n = Signal(init=1)
        cs1_n = Signal(init=1)
        da = Signal(3)
        dior_n = Signal(init=1)
        diow_n = Signal(init=1)
        dd_o = Signal(16)
        dd_oe = Signal()

        # the decoding: the task file (words 0..7, 14, 15), the controller's
        # own registers (8..11)
        ide_sel = Signal()
        ctl_sel = Signal()
        bb_sel = Signal()
        low = Signal()      # the words 0..15
        m.d.comb += [
            low.eq(~req.addr[4]),
            ide_sel.eq(low & (~req.addr[3] | (req.addr[1:4] == 0b111))),
            ctl_sel.eq(low & (req.addr[2:4] == 0b10)),
            bb_sel.eq(req.addr[2:5] == 0b100),
        ]
        m.d.comb += req.ready.eq(Mux(ide_sel, state == IDLE, 1))
        rst_sel = Signal()
        m.d.comb += rst_sel.eq(low & (req.addr[1:4] == 0b110))
        reset_hold = Signal()
        accept = Signal()
        m.d.comb += accept.eq(req.valid & req.ready)
        start = Signal()
        m.d.comb += start.eq(accept & ide_sel)

        # the registers of the controller
        with m.If(accept & req.we & ctl_sel & req.byte_lo):
            with m.Switch(req.addr[0:2]):
                with m.Case(0):
                    m.d.sync += setup.eq(req.wdata[0:8])
                with m.Case(1):
                    m.d.sync += strobe.eq(req.wdata[0:8])
                with m.Case(2):
                    m.d.sync += recover.eq(req.wdata[0:8])
                with m.Case(3):
                    m.d.sync += timeout.eq(0)

        with m.If(accept & req.we & rst_sel & req.byte_lo):
            m.d.sync += reset_hold.eq(req.wdata[0])

        bb_ctrl = Signal(10)
        bb_dd = Signal(16)
        with m.If(accept & req.we & bb_sel & req.byte_lo):
            with m.Switch(req.addr[0:2]):
                with m.Case(0):
                    m.d.sync += bb_ctrl.eq(req.wdata[0:10])
                with m.Case(1):
                    m.d.sync += bb_dd.eq(req.wdata)
        bb_data = Signal(16)
        with m.Switch(req.addr[0:2]):
            with m.Case(0):
                m.d.comb += bb_data.eq(bb_ctrl)
            with m.Case(1):
                m.d.comb += bb_data.eq(bb_dd)
            with m.Case(2):
                m.d.comb += bb_data.eq(pins.dd_i)
            with m.Case(3):
                m.d.comb += bb_data.eq(Cat(intrq, iordy))

        ctl_data = Signal(16)
        with m.Switch(req.addr[0:2]):
            with m.Case(0):
                m.d.comb += ctl_data.eq(setup)
            with m.Case(1):
                m.d.comb += ctl_data.eq(strobe)
            with m.Case(2):
                m.d.comb += ctl_data.eq(recover)
            with m.Case(3):
                m.d.comb += ctl_data.eq(Cat(intrq, timeout, iordy))
        m.d.sync += [
            resp.valid.eq(accept & ~req.we & ~ide_sel),
            resp.data.eq(
                Mux(ctl_sel, ctl_data,
                    Mux(rst_sel, reset_hold, Mux(bb_sel, bb_data, 0)))
            ),
        ]

        # the cycle on the bus
        setup_eff = Signal(8)
        strobe_eff = Signal(8)
        m.d.comb += [
            setup_eff.eq(Mux(setup == 0, 1, setup)),
            strobe_eff.eq(Mux(strobe < MIN_STROBE, MIN_STROBE, strobe)),
        ]
        min_done = Signal()
        m.d.comb += min_done.eq(cnt + 1 >= strobe_eff)
        wait_out = Signal()
        m.d.comb += wait_out.eq(wait == IORDY_TIMEOUT)
        is_data = Signal()
        m.d.comb += is_data.eq(~cs0_n & (da == 0))

        with m.Switch(state):
            with m.Case(IDLE):
                with m.If(start):
                    m.d.sync += [
                        is_wr.eq(req.we),
                        cs0_n.eq(req.addr[3]),
                        cs1_n.eq(~req.addr[3]),
                        da.eq(req.addr[0:3]),
                        dd_o.eq(req.wdata),
                        dd_oe.eq(req.we),
                        cnt.eq(setup_eff - 1),
                        state.eq(SETUP),
                    ]
            with m.Case(SETUP):
                with m.If(cnt == 0):
                    m.d.sync += [
                        dior_n.eq(is_wr),
                        diow_n.eq(~is_wr),
                        cnt.eq(0),
                        wait.eq(0),
                        state.eq(STROBE),
                    ]
                with m.Else():
                    m.d.sync += cnt.eq(cnt - 1)
            with m.Case(STROBE):
                with m.If(~min_done):
                    m.d.sync += cnt.eq(cnt + 1)
                with m.Elif(iordy | wait_out):
                    # the last clock of the strobe: the data is taken now
                    m.d.sync += [
                        dior_n.eq(1),
                        diow_n.eq(1),
                        cnt.eq(HOLD_CLOCKS - 1),
                        state.eq(HOLD),
                    ]
                    with m.If(~iordy):
                        m.d.sync += timeout.eq(1)
                    with m.If(~is_wr):
                        m.d.sync += [
                            resp.valid.eq(1),
                            resp.data.eq(Mux(
                                is_data, pins.dd_i, pins.dd_i & 0xff,
                            )),
                        ]
                with m.Else():
                    m.d.sync += wait.eq(wait + 1)
            with m.Case(HOLD):
                with m.If(cnt == 0):
                    m.d.sync += [
                        cs0_n.eq(1),
                        cs1_n.eq(1),
                        dd_oe.eq(0),
                    ]
                    with m.If(recover == 0):
                        m.d.sync += state.eq(IDLE)
                    with m.Else():
                        m.d.sync += [
                            cnt.eq(recover - 1),
                            state.eq(RECOV),
                        ]
                with m.Else():
                    m.d.sync += cnt.eq(cnt - 1)
            with m.Case(RECOV):
                with m.If(cnt == 0):
                    m.d.sync += state.eq(IDLE)
                with m.Else():
                    m.d.sync += cnt.eq(cnt - 1)

        with m.If(bb_ctrl[0]):
            m.d.comb += [
                pins.cs0_n.eq(bb_ctrl[1]),
                pins.cs1_n.eq(bb_ctrl[2]),
                pins.dior_n.eq(bb_ctrl[3]),
                pins.diow_n.eq(bb_ctrl[4]),
                pins.da.eq(bb_ctrl[5:8]),
                pins.dd_oe.eq(bb_ctrl[8]),
                pins.dd_o.eq(bb_dd),
                pins.reset_n.eq(bb_ctrl[9]),
            ]
        with m.Else():
            m.d.comb += [
                pins.da.eq(da),
                pins.cs0_n.eq(cs0_n),
                pins.cs1_n.eq(cs1_n),
                pins.dior_n.eq(dior_n),
                pins.diow_n.eq(diow_n),
                pins.dd_o.eq(dd_o),
                pins.dd_oe.eq(dd_oe),
                pins.reset_n.eq(~reset_hold),
            ]
        m.d.comb += self.irq.eq(intrq)
        return m
