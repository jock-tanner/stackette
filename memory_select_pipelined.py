"""
Pipelined memory select: like memory_select.MemorySelect, but the initiator
may issue further reads before earlier ones have been answered.

Targets have different latencies, so to keep responses in request order this
version counts the reads in flight (`pend`) and remembers which target they
went to (`cur`, one-hot). A read to another target than the one with reads
outstanding is held off (ready low) until those have been answered. Reads to
the same target pipeline as far as the target allows, up to 15 outstanding.
Writes never wait. Everything else (memory map, fault, ports) is as in
memory_select.py; the interface is identical, so the two are interchangeable.

Nothing in the design uses this yet: the SDRAM controller and the flash
reader take one request at a time, so `pend` never exceeds 1 with them.
"""
from amaranth import Cat, Module, Signal

from memory_select import MemorySelect

MAX_OUTSTANDING = 15


class MemorySelectPipelined(MemorySelect):
    def elaborate(self, platform):
        m = Module()
        cpu = self.cpu.req
        sel = self.decode(m)
        sel_flash, sel_ram, sel_periph = sel

        pend = Signal(4)  # reads accepted, response not yet seen
        cur = Signal(3)  # one-hot: target those reads went to
        same = Signal()
        gate = Signal()  # this request may go out now
        m.d.comb += [
            same.eq(
                (cur[0] & sel_flash)
                | (cur[1] & sel_ram)
                | (cur[2] & sel_periph)
            ),
            gate.eq(cpu.we | (pend == 0) | (same & (pend != MAX_OUTSTANDING))),
        ]
        resp_valid = self.connect(m, sel, gate)

        accept_read = Signal()
        m.d.comb += accept_read.eq(cpu.valid & cpu.ready & ~cpu.we)
        m.d.sync += pend.eq(pend + accept_read - resp_valid)
        with m.If(accept_read):
            m.d.sync += cur.eq(Cat(sel_flash, sel_ram, sel_periph))
        return m
