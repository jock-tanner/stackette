"""
MMU: translates the virtual addresses of the CPU into physical ones.

It sits between the arbiter (memory_arbiter.py) and MemorySelect. A virtual
address is 16 bits, a physical one 28 bits; a page is 4 KiB. The low 12 bits
of the address go through, the high 4 bits (the virtual page) pick one of 16
registers, whose 16 bits are the physical page (bits 27:12). Each of the four
address spaces of the CPU (code, return stack, parameter stack, data) has its
own 16 registers, and there are two such arrays of 64 registers: one for the
system context and one for the user context, picked by the `user` flag.

The 128 registers live in an Amaranth Memory. They are exposed to the system
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

Two ways to read the registers, picked by the `sync` parameter:

  * sync=False (default): asynchronous read ports. The translation is
    combinational, there is no latency, and a fault from MemorySelect comes
    back in the same cycle as the request. An FPGA has no asynchronous block
    RAM, so the memory becomes LUTs (a lot of them).
  * sync=True: synchronous read ports, which the synthesis tool can put in
    block RAM. The translation is looked up while the request is presented
    and used in the next cycle, so every request takes one more clock: in the
    first cycle `ready` and `fault` are low and nothing goes out on `mem`, in
    the second the request goes through as before. Nothing else changes for
    the initiator: it holds the request until `ready` or `fault`, as the
    protocol says. The lookup is remembered by the address it was made for
    (the virtual page, the space and `user`), so a request that is replaced
    by another one (the arbiter switches to a higher priority) is looked up
    again, and so is one for which a register was written in the meantime.
"""
from amaranth import C, Cat, Module, Signal
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

    def __init__(self, *, sync=False):
        super().__init__()
        self.sync = sync

    def elaborate(self, platform):
        m = Module()
        req, resp = self.cpu.req, self.cpu.resp
        out, back = self.mem.req, self.mem.resp

        init = [default_page(i % SYSTEM_REGS) for i in range(2 * SYSTEM_REGS)]
        m.submodules.regs_mem = regs_mem = Memory(
            shape=16, depth=2 * SYSTEM_REGS, init=init
        )
        read_domain = 'sync' if self.sync else 'comb'
        translate = regs_mem.read_port(domain=read_domain)
        reading = regs_mem.read_port(domain=read_domain)
        # the bytes of a register are written separately: no read-modify-write
        writing = regs_mem.write_port(granularity=8)

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
        key = Signal(REG_ADDR_WIDTH)
        m.d.comb += key.eq(
            Cat(req.addr[PAGE_WORD_BITS:], self.space, self.user)
        )
        # `go`: the request may go out now
        go = Signal()
        if self.sync:
            # the register that `translate.data` shows was read in the last
            # cycle for the address `last_key`
            last_key = Signal(REG_ADDR_WIDTH)
            looked = Signal()
            m.d.sync += [
                last_key.eq(key),
                looked.eq(req.valid & ~writing.en.any()),
            ]
            m.d.comb += go.eq(looked & (last_key == key) & ~sweeping)
        else:
            m.d.comb += go.eq(~sweeping)
        m.d.comb += [
            translate.addr.eq(key),
            out.valid.eq(req.valid & go),
            out.we.eq(req.we),
            out.byte_hi.eq(req.byte_hi),
            out.byte_lo.eq(req.byte_lo),
            out.addr.eq(Cat(req.addr[:PAGE_WORD_BITS], translate.data)),
            out.wdata.eq(req.wdata),
            req.ready.eq(out.ready & go),
            resp.valid.eq(back.valid),
            resp.data.eq(back.data),
            self.fault.eq(self.mem_fault),
        ]

        # ---- the registers as a device ----
        rreq, rresp = self.regs.req, self.regs.resp
        m.d.comb += [
            rreq.ready.eq(1),
            reading.addr.eq(rreq.addr),
        ]
        with m.If(sweeping):
            m.d.comb += [
                writing.addr.eq(Cat(sweep, C(0, 1))),
                writing.data.eq(sweep_page),
                writing.en.eq(0b11),
            ]
        with m.Else():
            write = rreq.valid & rreq.we
            m.d.comb += [
                writing.addr.eq(rreq.addr),
                writing.data.eq(rreq.wdata),
                writing.en.eq(Cat(rreq.byte_lo & write, rreq.byte_hi & write)),
            ]
        m.d.sync += rresp.valid.eq(rreq.valid & ~rreq.we)
        if self.sync:
            # the read port has the word one cycle after the address
            m.d.comb += rresp.data.eq(reading.data)
        else:
            m.d.sync += rresp.data.eq(reading.data)
        return m
