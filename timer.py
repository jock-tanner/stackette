"""
Timer: a counter that counts at a divided clock, compares itself with a
value and raises its interrupt line.

Registers (word addresses; 3 address bits x 16 data bits, byte enables are
honored on the writes):

  0  COUNT     read/write: the counter
  1  COMPARE   read/write
  2  CONTROL   read/write
               bit 0  RUN     the counter counts
               bit 1  RELOAD  at the match the counter goes to 0 instead of
                              COMPARE + 1, so the period is COMPARE + 1 ticks
  3  STATUS    bit 0  MATCH   set when the counter has matched COMPARE; the
                              interrupt line is this bit. Writing 1 clears it
                              (a match at the same moment wins).
  4  PRESCALE  read/write: a tick every PRESCALE + 1 clocks
  5..7 reserved: read as 0, writes ignored

All the registers are 0 after the reset: the timer is stopped, and a tick is
every clock. A write of COUNT or PRESCALE restarts the divider. A match is
when a tick finds COUNT == COMPARE, before the counter moves on, so with
RELOAD and COMPARE = n the period is n + 1 ticks.

Registers are read at the clock edge that accepts the request and the data
comes one cycle later, like the other devices.
"""
from amaranth import Cat, Module, Mux, Signal
from amaranth.lib.wiring import Component, In, Out

from memory_port import MemoryPort

TIMER_ADDR_WIDTH = 3
REG_COUNT = 0
REG_COMPARE = 1
REG_CONTROL = 2
REG_STATUS = 3
REG_PRESCALE = 4


class Timer(Component):
    bus: In(MemoryPort(TIMER_ADDR_WIDTH))
    irq: Out(1)

    def elaborate(self, platform):
        m = Module()
        req, resp = self.bus.req, self.bus.resp

        count = Signal(16)
        compare = Signal(16)
        run = Signal()
        reload = Signal()
        match = Signal()
        prescale = Signal(16)
        divider = Signal(16)

        m.d.comb += [
            req.ready.eq(1),
            self.irq.eq(match),
        ]

        def merged(old):
            value = Signal(16)
            m.d.comb += [
                value[0:8].eq(Mux(req.byte_lo, req.wdata[0:8], old[0:8])),
                value[8:16].eq(Mux(req.byte_hi, req.wdata[8:16], old[8:16])),
            ]
            return value

        write = req.valid & req.we
        sel = Signal(8)
        m.d.comb += sel.eq(1 << req.addr)

        # the clearing of MATCH comes first, so that a match wins over it
        with m.If(write & sel[REG_STATUS] & req.byte_lo & req.wdata[0]):
            m.d.sync += match.eq(0)

        tick = Signal()
        m.d.comb += tick.eq(run & (divider == 0))
        with m.If(run):
            with m.If(tick):
                m.d.sync += divider.eq(prescale)
                with m.If(count == compare):
                    m.d.sync += match.eq(1)
                    m.d.sync += count.eq(Mux(reload, 0, count + 1))
                with m.Else():
                    m.d.sync += count.eq(count + 1)
            with m.Else():
                m.d.sync += divider.eq(divider - 1)

        # the writes win over the counting
        new_count = merged(count)
        new_compare = merged(compare)
        new_prescale = merged(prescale)
        with m.If(write):
            with m.If(sel[REG_COUNT]):
                m.d.sync += [count.eq(new_count), divider.eq(prescale)]
            with m.If(sel[REG_COMPARE]):
                m.d.sync += compare.eq(new_compare)
            with m.If(sel[REG_CONTROL] & req.byte_lo):
                m.d.sync += [run.eq(req.wdata[0]), reload.eq(req.wdata[1])]
            with m.If(sel[REG_PRESCALE]):
                m.d.sync += [
                    prescale.eq(new_prescale),
                    divider.eq(new_prescale),
                ]

        data = Signal(16)
        with m.Switch(req.addr):
            with m.Case(REG_COUNT):
                m.d.comb += data.eq(count)
            with m.Case(REG_COMPARE):
                m.d.comb += data.eq(compare)
            with m.Case(REG_CONTROL):
                m.d.comb += data.eq(Cat(run, reload))
            with m.Case(REG_STATUS):
                m.d.comb += data.eq(match)
            with m.Case(REG_PRESCALE):
                m.d.comb += data.eq(prescale)
        m.d.sync += [
            resp.valid.eq(req.valid & ~req.we),
            resp.data.eq(data),
        ]
        return m
