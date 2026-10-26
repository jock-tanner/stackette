"""
Memory select: splits one CPU-side MemoryPort over the targets by address.

Temporary memory map (byte addresses; the port carries word addresses):

    0x0000..0x0fff   serial flash, first 4 KiB, read only, direct access
    0x1000..0xefff   SDRAM: data and parameter stack
    0xf000..0xffff   SDRAM: return stack
    0xf000..0xffff   return stack space: SDRAM
                     data space: memory-mapped peripherals (PDP-11 style)

Every request carries the address space it was made in (`space`, valid and
held together with cpu.req.valid): CODE (ip), RSTACK (rsp), PSTACK (psp, pfp)
or DATA (absolute pointer, mbp, mbp+midx). These are the four spaces the MMU
will later translate separately. Until then the map is the same for all of
them except the top page, where DATA means peripherals and every other space
means SDRAM. So the return stack and the peripherals share 0xf000..0xffff
without clashing.

Both SDRAM regions are one target: the SDRAM word address is the CPU word
address, zero-extended. The 4 KiB of SDRAM under the flash window are unused.
A peripheral gets the CPU word address cut down to the width of `periph`.
Splitting that further among several peripherals is the job of a small bus
behind `periph`.

Once the MMU exists, `space` goes to it and this block decodes the physical
address it produces instead.

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
stack, data pointer accesses) need an arbiter in front of it.
"""
from amaranth import Cat, Const, Module, Signal
from amaranth.lib.enum import IntEnum
from amaranth.lib.wiring import Component, In, Out

from memory_port import MemoryPort
from sdram_controller import HOST_ADDR_WIDTH
from spi_flash import FLASH_ADDR_WIDTH
from uart import UART_ADDR_WIDTH

CPU_ADDR_WIDTH = 15  # 16-bit byte address -> 15-bit word address
FLASH_WINDOW_BYTES = 0x1000
FLASH_WINDOW_WORDS = FLASH_WINDOW_BYTES // 2
MAX_OUTSTANDING = 15  # for pipelined version

class Space(IntEnum, shape=2):
    CODE = 0
    RSTACK = 1
    PSTACK = 2
    DATA = 3


PERIPH_PAGE = 0xF  # address bits 15:12 of the peripheral page


class MemorySelect(Component):
    def __init__(self, *, periph_addr_width=UART_ADDR_WIDTH):
        assert HOST_ADDR_WIDTH >= CPU_ADDR_WIDTH
        assert FLASH_WINDOW_WORDS <= 1 << FLASH_ADDR_WIDTH
        super().__init__({
            'cpu':    In(MemoryPort(CPU_ADDR_WIDTH)),
            'space':  In(Space),
            'flash':  Out(MemoryPort(FLASH_ADDR_WIDTH)),
            'ram':    Out(MemoryPort(HOST_ADDR_WIDTH)),
            'periph': Out(MemoryPort(periph_addr_width)),
        })

    def decode(self, m):
        """One-hot target select [flash, ram, periph], from space and address."""
        cpu = self.cpu.req
        sel_flash, sel_ram, sel_periph = Signal(), Signal(), Signal()
        m.d.comb += [
            sel_periph.eq((self.space == Space.DATA)
                          & (cpu.addr[11:] == PERIPH_PAGE)),
            sel_flash.eq(cpu.addr[11:] == 0),
            sel_ram.eq(~sel_flash & ~sel_periph),
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

        for i, t in enumerate([flash, ram, periph]):
            r = t.req
            valid = cpu.valid & sel[i] & gate
            if t is flash:
                valid = valid & ~cpu.we  # writes to flash are dropped
            m.d.comb += [
                r.valid.eq(valid),
                r.we.eq(cpu.we),
                r.byte_hi.eq(cpu.byte_hi),
                r.byte_lo.eq(cpu.byte_lo),
                r.addr.eq(cpu.addr),  # zero-extended / truncated
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


class MemorySelectPipelined(MemorySelect):
    """
    Pipelined memory select: like memory_select.MemorySelect, but the initiator
    may issue further reads before earlier ones have been answered.

    Targets have different latencies, so to keep responses in request order this
    version counts the reads in flight (`pend`) and remembers which target they
    went to (`cur`, one-hot). A read to another target than the one with reads
    outstanding is held off (ready low) until those have been answered. Reads to
    the same target pipeline as far as the target allows, up to 15 outstanding.
    Writes never wait. Everything else (memory map, spaces, ports) is as in
    memory_select.py; the interface is identical, so the two are interchangeable.

    Nothing in the design uses this yet: the SDRAM controller and the flash
    reader take one request at a time, so `pend` never exceeds 1 with them.
    """
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
            same.eq((cur[0] & sel_flash) | (cur[1] & sel_ram) | (cur[2] & sel_periph)),
            gate.eq(cpu.we | (pend == 0) | (same & (pend != MAX_OUTSTANDING))),
        ]
        resp_valid = self.connect(m, sel, gate)

        accept_read = Signal()
        m.d.comb += accept_read.eq(cpu.valid & cpu.ready & ~cpu.we)
        m.d.sync += pend.eq(pend + accept_read - resp_valid)
        with m.If(accept_read):
            m.d.sync += cur.eq(Cat(sel_flash, sel_ram, sel_periph))
        return m
