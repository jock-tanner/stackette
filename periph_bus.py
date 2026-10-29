"""
Peripheral bus: splits the one `periph` port of MemorySelect over the devices.

The peripheral page is 0xf000..0xffff in the data space; a device sits at
these byte addresses with the default map of the MMU (the port carries word
addresses, 8 bits wide, so the devices repeat every 512 bytes):

    0xf000..0xf007   UART       (4 words)
    0xf020..0xf03f   context device (16 words, see context_device.py)
    0xf100..0xf1ff   MMU registers (128 words, see mmu.py)

The rest is not mapped: a read gives 0 and a write is dropped.

The same rules as in memory_select.py: a pure multiplexer for the requests,
the responses of the devices are ORed together, and the initiator must not
have more than one read in flight.
"""
from amaranth import Module, Signal
from amaranth.lib.wiring import Component, In, Out

from memory_port import MemoryPort
from mmu import REG_ADDR_WIDTH
from uart import UART_ADDR_WIDTH

PERIPH_ADDR_WIDTH = 8
CTX_ADDR_WIDTH = 4


class PeriphBus(Component):
    bus: In(MemoryPort(PERIPH_ADDR_WIDTH))
    uart: Out(MemoryPort(UART_ADDR_WIDTH))
    ctx: Out(MemoryPort(CTX_ADDR_WIDTH))
    mmu: Out(MemoryPort(REG_ADDR_WIDTH))

    def elaborate(self, platform):
        m = Module()
        req, resp = self.bus.req, self.bus.resp

        sel_uart = Signal()
        sel_ctx = Signal()
        sel_mmu = Signal()
        m.d.comb += [
            sel_uart.eq(req.addr[2:] == 0),
            sel_ctx.eq(req.addr[4:] == 1),
            sel_mmu.eq(req.addr[7:] == 1),
        ]
        unmapped = ~sel_uart & ~sel_ctx & ~sel_mmu

        for target, sel in (
            (self.uart, sel_uart), (self.ctx, sel_ctx), (self.mmu, sel_mmu)
        ):
            r = target.req
            m.d.comb += [
                r.valid.eq(req.valid & sel),
                r.we.eq(req.we),
                r.byte_hi.eq(req.byte_hi),
                r.byte_lo.eq(req.byte_lo),
                r.addr.eq(req.addr),    # truncated
                r.wdata.eq(req.wdata),
            ]
        m.d.comb += req.ready.eq(
            (sel_uart & self.uart.req.ready)
            | (sel_ctx & self.ctx.req.ready)
            | (sel_mmu & self.mmu.req.ready)
            | unmapped
        )

        # a read of nothing: 0, one cycle after the request, like the devices
        dummy = Signal()
        m.d.sync += dummy.eq(req.valid & unmapped & ~req.we)

        m.d.comb += [
            resp.valid.eq(
                self.uart.resp.valid
                | self.ctx.resp.valid
                | self.mmu.resp.valid
                | dummy
            ),
            resp.data.eq(
                (self.uart.resp.data & self.uart.resp.valid.replicate(16))
                | (self.ctx.resp.data & self.ctx.resp.valid.replicate(16))
                | (self.mmu.resp.data & self.mmu.resp.valid.replicate(16))
            ),
        ]
        return m
