"""
SDRAM tester, split into a platform-independent initiator (MemoryTester) and
a thin DE0-Nano top level. The same initiator works against the SDRAM
subsystem, a cache in front of it, or a simulated memory.
"""
from amaranth import Module, Signal, Elaboratable
from amaranth.lib.wiring import Component, Out, connect

from memory_port import MemoryPort
from sdram_controller import HOST_ADDR_WIDTH


class MemoryTester(Component):
    """
    Writes a pattern to every word, reads it back, flags mismatches.

    Uses full-word accesses only (both byte enables set). Each request is
    held until accepted, so the tester needs no startup delay of its own:
    it simply waits while the memory subsystem initializes.
    """
    mem: Out(MemoryPort(HOST_ADDR_WIDTH))

    # status outputs
    mismatch: Out(1)    # sticky: high from the first mismatch on
    done: Out(1)        # high once the write+read pass has finished
    write_wait: Out(1)  # waiting for a write to be accepted
    read_wait: Out(1)   # waiting for a read response

    def __init__(self, *, word_count=16777216, pattern=0xA5A5):
        super().__init__()
        self.word_count = word_count
        self.pattern = pattern

    def elaborate(self, platform):
        m = Module()
        req, resp = self.mem.req, self.mem.resp

        addr = Signal(max(self.word_count - 1, 1).bit_length())
        last = addr == self.word_count - 1

        m.d.comb += [
            req.byte_hi.eq(1),     # high byte enabled
            req.byte_lo.eq(1),     # low byte enabled
        ]

        with m.FSM():
            with m.State('WRITE'):
                # Hold the request until accepted (valid & ready).
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(1),
                    req.addr.eq(addr),
                    req.wdata.eq(self.pattern),
                    self.write_wait.eq(~req.ready),
                ]
                with m.If(req.ready):
                    with m.If(last):
                        m.d.sync += addr.eq(0)
                        m.next = 'READ_ISSUE'
                    with m.Else():
                        m.d.sync += addr.eq(addr + 1)

            with m.State('READ_ISSUE'):
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(0),
                    req.addr.eq(addr),
                ]
                with m.If(req.ready):
                    m.next = 'READ_WAIT'

            with m.State('READ_WAIT'):
                m.d.comb += self.read_wait.eq(~resp.valid)
                with m.If(resp.valid):
                    with m.If(resp.data != self.pattern):
                        m.d.sync += self.mismatch.eq(1)
                    with m.If(last):
                        m.next = 'DONE'
                    with m.Else():
                        m.d.sync += addr.eq(addr + 1)
                        m.next = 'READ_ISSUE'

            with m.State('DONE'):
                m.d.comb += self.done.eq(1)

        return m


class DE0NanoTop(Elaboratable):
    """Board top level: tester + SDRAM subsystem + LEDs."""
    def __init__(self, **tester_kwargs):
        super().__init__()
        self.tester_kwargs = tester_kwargs

    def elaborate(self, platform):
        from de0nano_sdram import DE0NanoSDRAM

        m = Module()
        m.submodules.memory = memory = DE0NanoSDRAM()
        m.submodules.tester = tester = MemoryTester(**self.tester_kwargs)

        # The one line to change when a cache goes in between:
        connect(m, tester.mem, memory.bus)

        m.d.comb += [
            platform.request('led', 0).o.eq(tester.mismatch),
            platform.request('led', 3).o.eq(tester.write_wait),
            platform.request('led', 4).o.eq(tester.read_wait),
            platform.request('led', 7).o.eq(tester.done),
        ]
        return m


if __name__ == '__main__':
    from amaranth_boards.de0nano import DE0NanoPlatform

    DE0NanoPlatform().build(DE0NanoTop(), do_program=True)
