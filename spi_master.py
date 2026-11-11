"""
SPI master for the ENC28J60 (SPI mode 0, MSB first). The pins belong to this
one chip, so the chip select timing of its datasheet (DS39662, table 16-3) is
done here: the time from CS_N low to the first clock, the time from the last
clock to CS_N high, and the time CS_N stays high between two commands.

Registers (word addresses; the port is 2 address bits x 16 data bits):

  0  DATA     write: sends a byte (a byte access) or two bytes (a word access),
                     and starts to receive as many.
                     A word goes out low byte first, which is the order of the
                     bytes in memory.
                     Waits (req.ready is low) while the shifter is busy, and,
                     with CS = 1, until CS_N is low and the setup time is over.
              read:  the bytes that came in with the last transfer, the first
                     one in the low byte. Waits while the shifter is busy, so
                     a store of DATA followed by a load of it is a transfer.
  1  CONTROL  read/write
                bit 0  CS   1 selects the chip (CS_N low). Deselecting waits
                            until the shifter is idle, then the hold time, then
                            CS_N goes high and stays so for the disable time.
  2  DIVIDER  read/write, 8 bits: SCLK is the clock / (2 * (DIVIDER + 1)); 1
              (12.5 MHz at 50 MHz) after the reset, 0 counts as 1, as the chip
              takes 20 MHz at the most
  3  STATUS   read only
                bit 0  BUSY   a transfer or a change of CS_N is going on
                bit 1  INT    the INT pin of the chip is low: it asks for
                              service. This is the interrupt line too
                              (`irq`), until the chip's flags are cleared.

MISO is sampled in the clock cycle that lowers SCLK, 80 ns after the last
rising edge at 12.5 MHz: the chip needs 50 ns after the falling edge to show
the bit. MOSI changes at the falling edge, half a period before the chip
takes it.

A byte access with the byte enable of the high byte only does nothing.
"""
from amaranth import Cat, C, Module, Mux, Signal
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.wiring import Component, In, Out

from de0nano_spi_ethernet import SPIEtherPins
from memory_port import MemoryPort

SPI_ADDR_WIDTH = 2
REG_DATA = 0
REG_CONTROL = 1
REG_DIVIDER = 2
REG_STATUS = 3

DESELECTED, SETUP, SELECTED, HOLD, DISABLE = range(5)

# the times of the chip, in ns (DS39662 table 16-3): CS setup 50, CS hold 210
# (for the MAC and MII registers), CS disable 210
T_SETUP = 50
T_HOLD = 210
T_DISABLE = 210


def clocks(ns, clk_hz):
    """The number of clocks that last at least `ns`, and one more."""
    return -(-ns * clk_hz // 1_000_000_000) + 1


class SPIMaster(Component):
    bus: In(MemoryPort(SPI_ADDR_WIDTH))
    pins: Out(SPIEtherPins())
    irq: Out(1)

    def __init__(self, *, clk_hz=50_000_000):
        super().__init__()
        self.clk_hz = clk_hz

    def elaborate(self, platform):
        m = Module()
        req, resp = self.bus.req, self.bus.resp

        cs_req = Signal()
        cs_off = Signal()   # a deselect is waiting, for the end of a transfer
        div = Signal(8, init=1)

        # the shifter
        shifting = Signal()
        word = Signal()
        tx = Signal(16)
        rx = Signal(16)
        nbits = Signal(5)
        half = Signal(8)
        cnt = Signal(8)
        sclk = Signal()
        result = Signal(16)

        # the chip select
        cs_state = Signal(3)
        cs_cnt = Signal(range(max(
            clocks(T_SETUP, self.clk_hz),
            clocks(T_HOLD, self.clk_hz),
            clocks(T_DISABLE, self.clk_hz),
        ) + 1))

        int_pin = Signal(init=1)
        m.submodules.sync_int = FFSynchronizer(self.pins.int, int_pin, init=1)

        div_eff = Signal(8)
        m.d.comb += div_eff.eq(Mux(div == 0, 1, div))

        selected = Signal()
        m.d.comb += selected.eq((cs_state == SELECTED) & ~cs_off)
        busy = Signal()
        m.d.comb += busy.eq(
            shifting
            | (cs_state == SETUP) | (cs_state == HOLD) | (cs_state == DISABLE)
        )

        # the requests
        is_data = Signal()
        m.d.comb += is_data.eq(req.addr == REG_DATA)
        m.d.comb += req.ready.eq(
            Mux(
                is_data,
                ~shifting & (~req.we | ~cs_req | selected),
                1,
            )
        )
        accept = Signal()
        m.d.comb += accept.eq(req.valid & req.ready)
        start = Signal()
        m.d.comb += start.eq(accept & req.we & is_data & req.byte_lo)

        with m.If(accept & req.we & req.byte_lo):
            with m.If(req.addr == REG_CONTROL):
                m.d.sync += cs_req.eq(req.wdata[0])
            with m.If(req.addr == REG_DIVIDER):
                m.d.sync += div.eq(req.wdata[0:8])

        # a deselect is remembered until CS_N has gone high, so that a deselect
        # and a select that follow at once are two commands, not one
        with m.If(
            accept & req.we & req.byte_lo & (req.addr == REG_CONTROL)
            & ~req.wdata[0]
            & ((cs_state == SETUP) | (cs_state == SELECTED))
        ):
            m.d.sync += cs_off.eq(1)

        # the shifter
        new_rx = Signal(16)
        m.d.comb += new_rx.eq(Cat(self.pins.miso, rx[0:15]))
        with m.If(start):
            m.d.sync += [
                word.eq(req.byte_hi),
                tx.eq(Mux(
                    req.byte_hi,
                    Cat(req.wdata[8:16], req.wdata[0:8]),
                    Cat(C(0, 8), req.wdata[0:8]),
                )),
                nbits.eq(Mux(req.byte_hi, 16, 8)),
                half.eq(div_eff),
                cnt.eq(div_eff),
                sclk.eq(0),
                shifting.eq(1),
            ]
        with m.Elif(shifting):
            with m.If(cnt != 0):
                m.d.sync += cnt.eq(cnt - 1)
            with m.Else():
                m.d.sync += cnt.eq(half)
                with m.If(~sclk):
                    # rising edge: the chip takes MOSI
                    m.d.sync += sclk.eq(1)
                with m.Else():
                    # falling edge: MISO is sampled, the next bit of MOSI
                    m.d.sync += [
                        sclk.eq(0),
                        rx.eq(new_rx),
                        tx.eq(Cat(C(0, 1), tx[0:15])),
                        nbits.eq(nbits - 1),
                    ]
                    with m.If(nbits == 1):
                        m.d.sync += [
                            shifting.eq(0),
                            result.eq(Mux(
                                word,
                                Cat(new_rx[8:16], new_rx[0:8]),
                                new_rx[0:8],
                            )),
                        ]

        # CS_N
        with m.Switch(cs_state):
            with m.Case(DESELECTED):
                with m.If(cs_req):
                    m.d.sync += [
                        cs_state.eq(SETUP),
                        cs_cnt.eq(clocks(T_SETUP, self.clk_hz) - 1),
                    ]
            with m.Case(SETUP):
                with m.If(cs_cnt == 0):
                    m.d.sync += cs_state.eq(SELECTED)
                with m.Else():
                    m.d.sync += cs_cnt.eq(cs_cnt - 1)
            with m.Case(SELECTED):
                with m.If(cs_off & ~shifting & ~start):
                    m.d.sync += [
                        cs_state.eq(HOLD),
                        cs_cnt.eq(clocks(T_HOLD, self.clk_hz) - 1),
                    ]
            with m.Case(HOLD):
                with m.If(shifting | start):
                    m.d.sync += cs_cnt.eq(clocks(T_HOLD, self.clk_hz) - 1)
                with m.Elif(cs_cnt == 0):
                    m.d.sync += [
                        cs_state.eq(DISABLE),
                        cs_cnt.eq(clocks(T_DISABLE, self.clk_hz) - 1),
                    ]
                with m.Else():
                    m.d.sync += cs_cnt.eq(cs_cnt - 1)
            with m.Case(DISABLE):
                with m.If(cs_cnt == 0):
                    m.d.sync += cs_state.eq(DESELECTED)
                with m.Else():
                    m.d.sync += cs_cnt.eq(cs_cnt - 1)

        # the deselect that is done: forgotten, unless another one has come
        with m.If((cs_state == SELECTED) & cs_off & ~shifting & ~start):
            m.d.sync += cs_off.eq(0)
            with m.If(
                accept & req.we & req.byte_lo & (req.addr == REG_CONTROL)
                & ~req.wdata[0]
            ):
                m.d.sync += cs_off.eq(1)

        # the pins
        m.d.comb += [
            self.pins.cs_n.eq((cs_state == DESELECTED) | (cs_state == DISABLE)),
            self.pins.sclk.eq(sclk),
            self.pins.mosi.eq(tx[15]),
            self.irq.eq(~int_pin),
        ]

        # the reads
        data = Signal(16)
        with m.Switch(req.addr):
            with m.Case(REG_DATA):
                m.d.comb += data.eq(result)
            with m.Case(REG_CONTROL):
                m.d.comb += data.eq(cs_req)
            with m.Case(REG_DIVIDER):
                m.d.comb += data.eq(div)
            with m.Case(REG_STATUS):
                m.d.comb += data.eq(Cat(busy, ~int_pin))
        m.d.sync += [
            resp.valid.eq(accept & ~req.we),
            resp.data.eq(data),
        ]
        return m
