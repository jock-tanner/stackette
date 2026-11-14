"""
Peripheral bus: splits the one `periph` port of MemorySelect over the devices.

The peripheral page is 0xf000..0xffff in the data space; a device sits at
these byte addresses with the default map of the MMU (the port carries word
addresses, 8 bits wide, so the devices repeat every 512 bytes):

    0xf000..0xf007   UART       (4 words)
    0xf008..0xf00f   interrupt controller (4 words, see intc.py)
    0xf010..0xf01f   timer (8 words, see timer.py)
    0xf020..0xf03f   context device (16 words, see context_device.py)
    0xf040..0xf047   SPI master (4 words, see spi_master.py)
    0xf080..0xf0bf   IDE host (32 words, see ide_pio.py)
    0xf100..0xf1ff   MMU registers (128 words, see mmu.py)

The rest is not mapped: a read gives 0 and a write is dropped.

The same rules as in memory_select.py: a pure multiplexer for the requests,
the responses of the devices are ORed together, and the initiator must not
have more than one read in flight.
"""
from functools import reduce
from operator import or_

from amaranth import Module, Signal
from amaranth.lib.wiring import Component, In, Out

from ide_pio import IDE_ADDR_WIDTH
from intc import INTC_ADDR_WIDTH
from memory_port import MemoryPort
from mmu import REG_ADDR_WIDTH
from spi_master import SPI_ADDR_WIDTH
from timer import TIMER_ADDR_WIDTH
from uart import UART_ADDR_WIDTH

PERIPH_ADDR_WIDTH = 8
CTX_ADDR_WIDTH = 4


class PeriphBus(Component):
    bus: In(MemoryPort(PERIPH_ADDR_WIDTH))
    uart: Out(MemoryPort(UART_ADDR_WIDTH))
    intc: Out(MemoryPort(INTC_ADDR_WIDTH))
    timer: Out(MemoryPort(TIMER_ADDR_WIDTH))
    ctx: Out(MemoryPort(CTX_ADDR_WIDTH))
    spi: Out(MemoryPort(SPI_ADDR_WIDTH))
    ide: Out(MemoryPort(IDE_ADDR_WIDTH))
    mmu: Out(MemoryPort(REG_ADDR_WIDTH))

    def elaborate(self, platform):
        m = Module()
        req, resp = self.bus.req, self.bus.resp

        sel_uart = Signal()
        sel_intc = Signal()
        sel_timer = Signal()
        sel_ctx = Signal()
        sel_spi = Signal()
        sel_ide = Signal()
        sel_mmu = Signal()
        m.d.comb += [
            sel_uart.eq(req.addr[2:] == 0),
            sel_intc.eq(req.addr[2:] == 1),
            sel_timer.eq(req.addr[3:] == 1),
            sel_ctx.eq(req.addr[4:] == 1),
            sel_spi.eq(req.addr[2:] == 8),
            sel_ide.eq(req.addr[5:] == 2),
            sel_mmu.eq(req.addr[7:] == 1),
        ]
        unmapped = ~(
            sel_uart | sel_intc | sel_timer | sel_ctx | sel_spi | sel_ide
            | sel_mmu
        )

        targets = (
            (self.uart, sel_uart),
            (self.intc, sel_intc),
            (self.timer, sel_timer),
            (self.ctx, sel_ctx),
            (self.spi, sel_spi),
            (self.ide, sel_ide),
            (self.mmu, sel_mmu),
        )
        for target, sel in targets:
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
            unmapped
            | (sel_uart & self.uart.req.ready)
            | (sel_intc & self.intc.req.ready)
            | (sel_timer & self.timer.req.ready)
            | (sel_ctx & self.ctx.req.ready)
            | (sel_spi & self.spi.req.ready)
            | (sel_ide & self.ide.req.ready)
            | (sel_mmu & self.mmu.req.ready)
        )

        # a read of nothing: 0, one cycle after the request, like the devices
        dummy = Signal()
        m.d.sync += dummy.eq(req.valid & unmapped & ~req.we)

        m.d.comb += [
            resp.valid.eq(
                dummy | reduce(or_, (t.resp.valid for t, _ in targets))
            ),
            resp.data.eq(
                reduce(
                    or_,
                    (
                        t.resp.data & t.resp.valid.replicate(16)
                        for t, _ in targets
                    ),
                )
            ),
        ]
        return m
