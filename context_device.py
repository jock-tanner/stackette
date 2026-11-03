"""
Context device: the memory-mapped view of the registers of the inactive
context, and of the exception data, for the system software.

Word numbers (the same as the CTX_ constants of cpu.py):

   0  IP            of the inactive context
   1  RSP
   2  PFP
   3  MBP
   4  MIDX
   5  SW            status register (only the implemented bits are kept)
   6  PSP
   7  ID            why the system context runs (read only), see cpu.py
   8  FAULT_IP      where the instruction that failed starts (read only)
   9  FAULT_ADDR    byte address of the request that was refused (read only)
   10..15           read as 0

In system mode the inactive context is the user context, so the system
software prepares a user context here, and finds the state of an interrupted
one. Byte enables are honored for writes. The words 7..9 and 10..15 ignore
writes. Registers are read at the clock edge that accepts the request and the
data comes one cycle later, like the other devices.

Nothing here stops a user context that has the page mapped (there is no
privilege check): the system software must keep the page out of reach.
"""
from amaranth import Module, Mux, Signal
from amaranth.lib.wiring import Component, In, Out

from memory_port import MemoryPort

CTX_ADDR_WIDTH = 4


class ContextDevice(Component):
    bus: In(MemoryPort(CTX_ADDR_WIDTH))
    # the CPU's context port
    ctx_addr: Out(4)
    ctx_we: Out(1)
    ctx_wdata: Out(16)
    ctx_rdata: In(16)

    def elaborate(self, platform):
        m = Module()
        req, resp = self.bus.req, self.bus.resp

        merged = Signal(16)
        m.d.comb += [
            req.ready.eq(1),
            self.ctx_addr.eq(req.addr),
            self.ctx_we.eq(req.valid & req.we),
            self.ctx_wdata.eq(merged),
            merged[0:8].eq(
                Mux(req.byte_lo, req.wdata[0:8], self.ctx_rdata[0:8])
            ),
            merged[8:16].eq(
                Mux(req.byte_hi, req.wdata[8:16], self.ctx_rdata[8:16])
            ),
        ]
        m.d.sync += [
            resp.valid.eq(req.valid & ~req.we),
            resp.data.eq(self.ctx_rdata),
        ]
        return m
