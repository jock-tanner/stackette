"""
Peripheral bus: splits the one `periph` port of MemorySelect over the devices.

The peripheral page is 0xf000..0xffff in the data space; a device sits at
these byte addresses (the port carries word addresses, 7 bits wide):

    0xf000..0xf007   UART       (4 words)
    0xf020..0xf03f   context device (16 words, see context_device.py)

The rest is not mapped: a read gives 0 and a write is dropped.

The same rules as in memory_select.py: a pure multiplexer for the requests,
the responses of the devices are ORed together, and the initiator must not
have more than one read in flight.
"""
from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from memory_port import MemoryPort
from uart import UART_ADDR_WIDTH

PERIPH_ADDR_WIDTH = 7
CTX_ADDR_WIDTH = 4


class PeriphBus(wiring.Component):
    bus: In(MemoryPort(PERIPH_ADDR_WIDTH))
    uart: Out(MemoryPort(UART_ADDR_WIDTH))
    ctx: Out(MemoryPort(CTX_ADDR_WIDTH))

    def elaborate(self, platform):
        m = Module()
        req, resp = self.bus.req, self.bus.resp

        sel_uart = Signal()
        sel_ctx = Signal()
        m.d.comb += [
            sel_uart.eq(req.addr[2:] == 0),
            sel_ctx.eq(req.addr[4:] == 1),
        ]
        unmapped = ~sel_uart & ~sel_ctx

        for target, sel in ((self.uart, sel_uart), (self.ctx, sel_ctx)):
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
            | unmapped
        )

        # a read of nothing: 0, one cycle after the request, like the devices
        dummy = Signal()
        m.d.sync += dummy.eq(req.valid & unmapped & ~req.we)

        m.d.comb += [
            resp.valid.eq(self.uart.resp.valid | self.ctx.resp.valid | dummy),
            resp.data.eq(
                (self.uart.resp.data & self.uart.resp.valid.replicate(16))
                | (self.ctx.resp.data & self.ctx.resp.valid.replicate(16))
            ),
        ]
        return m
