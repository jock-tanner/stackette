"""
MMU: translates the virtual addresses of the CPU into physical ones.

It sits between the arbiter (memory_arbiter.py) and MemorySelect. A virtual
address is 16 bits, a physical one 28 bits; a page is 4 KiB. The low 12 bits
of the address go through, the high 4 bits (the virtual page) pick one of 16
registers, whose 16 bits are the physical page (bits 27:12). Each of the four
address spaces of the CPU (code, return stack, parameter stack, data) has its
own 16 registers, and there are two such arrays of 64 registers: one for the
system context and one for the user context, picked by the `user` flag.

The 128 registers live in an Amaranth Memory with asynchronous read ports (the
translation is combinational: no latency, and a fault from MemorySelect comes
back in the same cycle as the request). They are exposed to the system
software through `regs`, a memory-mapped device of 128 words:

    word  0..63    system array, space * 16 + page (CODE, RSTACK, PSTACK, DATA)
    word 64..127   user array, the same

A register that points to a physical page with no memory makes the request a
fault (see memory_select.py); there are no other page attributes.

Power-up and soft reset values: the default map that the first versions of
the system had, so the system works without any setup. Page 0 of every space
is the first page of the flash, page 15 of the data space is the first page of
the peripherals, every other page is the SDRAM page with the same number. Both
arrays have it at power-up. A soft reset of the CPU (`reset_system` goes high)
puts it back into the system array, one register per cycle (64 cycles) while
all requests wait; the user array is left alone.

Everything else is a wire: a request goes through with its byte enables and
data, ready, the response and the fault come back.
"""
from amaranth import C, Cat, Module, Mux, Signal
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import Component, In, Out

from memory_port import MemoryPort
from memory_select import (
    CPU_ADDR_WIDTH,
    FLASH_PAGE,
    PAGE_WORD_BITS,
    PERIPH_PAGE,
    PHYS_ADDR_WIDTH,
    Space,
)

REG_ADDR_WIDTH = 7
SYSTEM_REGS = 64


def default_page(index):
    """The default content of register `index` (0..63) of an array."""
    page, space = index & 15, index >> 4
    if page == 0:
        return FLASH_PAGE
    if space == Space.DATA and page == 15:
        return PERIPH_PAGE
    return page


class MMU(Component):
    cpu: In(MemoryPort(CPU_ADDR_WIDTH))
    space: In(Space)
    user: In(1)
    mem: Out(MemoryPort(PHYS_ADDR_WIDTH))
    mem_fault: In(1)    # MemorySelect's refusal of the translated request
    fault: Out(1)       # ... and the same, for the initiator
    reset_system: In(1)
    regs: In(MemoryPort(REG_ADDR_WIDTH))

    def elaborate(self, platform):
        m = Module()
        req, resp = self.cpu.req, self.cpu.resp
        out, back = self.mem.req, self.mem.resp

        init = [default_page(i % SYSTEM_REGS) for i in range(2 * SYSTEM_REGS)]
        m.submodules.regs_mem = regs_mem = Memory(shape=16, depth=2 * SYSTEM_REGS, init=init)
        translate = regs_mem.read_port(domain='comb')
        reading = regs_mem.read_port(domain='comb')
        writing = regs_mem.write_port()

        # ---- soft reset of the system array: 64 cycles, all requests wait ----
        sweeping = Signal()
        sweep = Signal(range(SYSTEM_REGS))
        reset_before = Signal()
        m.d.sync += reset_before.eq(self.reset_system)
        with m.If(self.reset_system & ~reset_before):
            m.d.sync += [sweeping.eq(1), sweep.eq(0)]
        with m.Elif(sweeping):
            m.d.sync += sweep.eq(sweep + 1)
            with m.If(sweep == SYSTEM_REGS - 1):
                m.d.sync += sweeping.eq(0)
        sweep_page = Signal(16)
        with m.If(sweep[0:4] == 0):
            m.d.comb += sweep_page.eq(FLASH_PAGE)
        with m.Elif((sweep[4:6] == Space.DATA) & (sweep[0:4] == 15)):
            m.d.comb += sweep_page.eq(PERIPH_PAGE)
        with m.Else():
            m.d.comb += sweep_page.eq(sweep[0:4])

        # ---- the translation ----
        m.d.comb += [
            translate.addr.eq(
                Cat(req.addr[PAGE_WORD_BITS:], self.space, self.user)
            ),
            out.valid.eq(req.valid & ~sweeping),
            out.we.eq(req.we),
            out.byte_hi.eq(req.byte_hi),
            out.byte_lo.eq(req.byte_lo),
            out.addr.eq(Cat(req.addr[:PAGE_WORD_BITS], translate.data)),
            out.wdata.eq(req.wdata),
            req.ready.eq(out.ready & ~sweeping),
            resp.valid.eq(back.valid),
            resp.data.eq(back.data),
            self.fault.eq(self.mem_fault),
        ]

        # ---- the registers as a device ----
        rreq, rresp = self.regs.req, self.regs.resp
        merged = Signal(16)
        m.d.comb += [
            rreq.ready.eq(1),
            reading.addr.eq(rreq.addr),
            merged[0:8].eq(
                Mux(rreq.byte_lo, rreq.wdata[0:8], reading.data[0:8])
            ),
            merged[8:16].eq(
                Mux(rreq.byte_hi, rreq.wdata[8:16], reading.data[8:16])
            ),
        ]
        with m.If(sweeping):
            m.d.comb += [
                writing.addr.eq(Cat(sweep, C(0, 1))),
                writing.data.eq(sweep_page),
                writing.en.eq(1),
            ]
        with m.Else():
            m.d.comb += [
                writing.addr.eq(rreq.addr),
                writing.data.eq(merged),
                writing.en.eq(rreq.valid & rreq.we),
            ]
        m.d.sync += [
            rresp.valid.eq(rreq.valid & ~rreq.we),
            rresp.data.eq(reading.data),
        ]
        return m
