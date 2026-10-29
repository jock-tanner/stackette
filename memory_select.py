"""
Memory select: splits one MemoryPort of physical word addresses over the
targets by address, and reports a fault for an address that has no memory.

The addresses come from the MMU (mmu.py): a 28-bit physical byte address, a
27-bit word address on the port. Its top 16 bits are the physical page number
(pages of 4 KiB, 2048 words):

    pages 0x0000..0x1fff   SDRAM, 32 MiB (the SDRAM word address is the low
                           24 bits)
    pages 0x2000..0x20ff   serial flash, 1 MiB, read only (the flash word
                           address is the low 19 bits)
    pages 0x2100..0x7fff   nothing: a request for them is a fault
    pages 0x8000..0xffff   memory-mapped peripherals (PDP-11 style); a
                           peripheral gets the low bits of the word address,
                           cut down to the width of `periph`. Splitting that
                           further among several peripherals is the job of
                           the peripheral bus behind `periph` (periph_bus.py).

A request for an address in the nothing range is not passed on: `fault` is high
while it is presented (and `cpu.req.ready` is low), the way the CPU expects a
refusal to come: in the same cycle as the request, instead of ready.

Behavior (this version: not pipelined, no state at all)
  * A write to flash is accepted and dropped (the reader is read only).
  * Pure multiplexer: no registers, no added latency. ready depends
    combinationally on the request fields, as the protocol allows.
  * Responses are ORed together (only the target that was asked drives
    resp.valid, the others are idle). That is only correct if the initiator
    has at most ONE read outstanding at a time: it must not issue a read
    before the previous read's response has arrived. Targets have different
    latencies, so two reads in flight to different targets could come back
    out of order. The SDRAM controller and the flash reader accept one request
    at a time anyway. Writes may be issued at any time.
    For an initiator that wants more than one read in flight, see
    memory_select_pipelined.MemorySelectPipelined.

This takes one initiator. Several (instruction fetch, stack cache, return
stack, data pointer accesses) need an arbiter in front of it, and the MMU
between them.
"""
from amaranth import Const, Module, Signal
from amaranth.lib.enum import IntEnum
from amaranth.lib.wiring import Component, In, Out

from memory_port import MemoryPort
from sdram_controller import HOST_ADDR_WIDTH
from spi_flash import FLASH_ADDR_WIDTH
from uart import UART_ADDR_WIDTH

CPU_ADDR_WIDTH = 15                 # 16-bit byte address -> 15-bit word address
PHYS_ADDR_WIDTH = 27                # 28-bit byte address -> 27-bit word address
PAGE_WORD_BITS = 11                 # a page is 4 KiB: 2048 words
# physical page numbers (the top 16 bits of the physical byte address)
FLASH_PAGE = 0x2000                 # the first page of the flash
PERIPH_PAGE = 0x8000                # the first page of the peripherals

class Space(IntEnum, shape=2):
    CODE = 0
    RSTACK = 1
    PSTACK = 2
    DATA = 3


class MemorySelect(Component):
    def __init__(self, *, periph_addr_width=UART_ADDR_WIDTH):
        assert HOST_ADDR_WIDTH == 13 + PAGE_WORD_BITS        # pages 0..0x1fff
        assert FLASH_ADDR_WIDTH == 8 + PAGE_WORD_BITS        # 256 pages
        super().__init__({
            'cpu':    In(MemoryPort(PHYS_ADDR_WIDTH)),
            'fault':  Out(1),
            'flash':  Out(MemoryPort(FLASH_ADDR_WIDTH)),
            'ram':    Out(MemoryPort(HOST_ADDR_WIDTH)),
            'periph': Out(MemoryPort(periph_addr_width)),
        })

    def decode(self, m):
        """One-hot target select [flash, ram, periph], from the page number.
        None of them is a request for an address that has no memory."""
        cpu = self.cpu.req
        page = cpu.addr[PAGE_WORD_BITS:]
        sel_flash, sel_ram, sel_periph = Signal(), Signal(), Signal()
        m.d.comb += [
            sel_ram.eq(page[13:] == 0),
            sel_flash.eq(page[8:] == FLASH_PAGE >> 8),
            sel_periph.eq(page[15]),
        ]
        return [sel_flash, sel_ram, sel_periph]

    def connect(self, m, sel, gate):
        """
        Request fan-out, ready mux and response fan-in. `gate` is a 1-bit
        value: when low, no request is passed on and ready is low (reads
        only; writes are never held back by the subclass that uses it).
        """
        cpu, resp = self.cpu.req, self.cpu.resp
        flash, ram, periph = self.flash, self.ram, self.periph
        sel_flash, sel_ram, sel_periph = sel
        m.d.comb += self.fault.eq(cpu.valid & ~(sel_flash | sel_ram | sel_periph))

        for i, t in enumerate([flash, ram, periph]):
            r = t.req
            valid = cpu.valid & sel[i] & gate
            if t is flash:
                valid = valid & ~cpu.we                 # writes to flash are dropped
            m.d.comb += [
                r.valid.eq(valid),
                r.we.eq(cpu.we),
                r.byte_hi.eq(cpu.byte_hi),
                r.byte_lo.eq(cpu.byte_lo),
                r.addr.eq(cpu.addr),                    # zero-extended / truncated
                r.wdata.eq(cpu.wdata),
            ]
        m.d.comb += cpu.ready.eq(gate & (
            (sel_flash & (cpu.we | flash.req.ready))
            | (sel_ram & ram.req.ready)
            | (sel_periph & periph.req.ready)
        ))
        resp_valid = Signal()
        m.d.comb += [
            resp_valid.eq(flash.resp.valid | ram.resp.valid | periph.resp.valid),
            resp.valid.eq(resp_valid),
            resp.data.eq(
                (flash.resp.data & flash.resp.valid.replicate(16))
                | (ram.resp.data & ram.resp.valid.replicate(16))
                | (periph.resp.data & periph.resp.valid.replicate(16))
            ),
        ]
        return resp_valid

    def elaborate(self, platform):
        m = Module()
        self.connect(m, self.decode(m), Const(1))
        return m
