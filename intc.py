"""
Interrupt controller: collects the interrupt lines of the devices, masks them
and tells the CPU which one is to be served.

The lines are levels: a device keeps its line high until the system software
has dealt with the cause (read the UART, cleared the timer flag). The
controller keeps no state about them, only the mask. Line 0 has the highest
priority.

Registers (word addresses; 2 address bits x 16 data bits, byte enables are
honored on the writes):

  0  RAW     read only: the lines as they are (bit n = line n)
  1  MASK    read/write: a line takes part only if its bit is set. Zero after
             the reset: all the lines are masked.
  2  ACTIVE  read only: RAW & MASK. The CPU is interrupted while it is not 0.
  3  reserved: read as 0, writes ignored.

The CPU takes the interrupt only in the user context, between two
instructions. In the system context interrupts wait (the system software
runs with them off, and does not need an enable flag for that). RETI to the
user context with a line still active enters the system context again at
once, so the handler has to remove the cause or mask the line first. The
number that goes to the ID register is the number of the active line with
the highest priority at the moment of the entry (`irq_num`).

Registers are read at the clock edge that accepts the request and the data
comes one cycle later, like the other devices.
"""
from amaranth import Module, Signal
from amaranth.lib.wiring import Component, In, Out

from memory_port import MemoryPort

INTC_ADDR_WIDTH = 2
N_LINES = 8
REG_RAW = 0
REG_MASK = 1
REG_ACTIVE = 2


class InterruptController(Component):
    bus: In(MemoryPort(INTC_ADDR_WIDTH))
    lines: In(N_LINES)
    irq: Out(1)
    irq_num: Out(6)

    def elaborate(self, platform):
        m = Module()
        req, resp = self.bus.req, self.bus.resp

        mask = Signal(N_LINES)
        active = Signal(N_LINES)
        m.d.comb += [
            active.eq(self.lines & mask),
            self.irq.eq(active.any()),
            req.ready.eq(1),
        ]
        # the lowest set bit of `active`
        for n in reversed(range(N_LINES)):
            with m.If(active[n]):
                m.d.comb += self.irq_num.eq(n)

        with m.If(req.valid & req.we & (req.addr == REG_MASK)):
            with m.If(req.byte_lo):
                m.d.sync += mask.eq(req.wdata[:N_LINES])

        data = Signal(16)
        with m.Switch(req.addr):
            with m.Case(REG_RAW):
                m.d.comb += data.eq(self.lines)
            with m.Case(REG_MASK):
                m.d.comb += data.eq(mask)
            with m.Case(REG_ACTIVE):
                m.d.comb += data.eq(active)
        m.d.sync += [
            resp.valid.eq(req.valid & ~req.we),
            resp.data.eq(data),
        ]
        return m
