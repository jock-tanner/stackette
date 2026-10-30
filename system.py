"""
The CPU with everything between it and the memories and the UART: the arbiter
that shares one port between the code/data accesses and the stack cache, the
MMU, the memory select, the peripheral bus and the devices on it (the context
device and the MMU registers). The memories and the UART stay outside:

    flash   serial flash reader, physical word addresses, read only
    ram     SDRAM controller
    uart    the UART's register port

cpu.CPUTop connects the real ones; tests connect behavioral models.

     CPU --- code, data --+
      |                   +-- arbiter -- MMU -- MemorySelect -- flash, ram
      +--- stack cache ---+                           |
                                                      +-- periph bus -- uart,
                                                          context device,
                                                          MMU registers

The MMU is in the path of every request. A fault (MemorySelect's refusal of
an address with no memory) goes back through the MMU and the arbiter to the
initiator whose request it was.
"""
from amaranth import Module
from amaranth.lib import wiring
from amaranth.lib.wiring import Out, connect, flipped

from context_device import ContextDevice
from cpu import CPU
from memory_arbiter import MemoryArbiter
from memory_port import MemoryPort
from memory_select import CPU_ADDR_WIDTH, MemorySelect, Space
from mmu import MMU
from periph_bus import PERIPH_ADDR_WIDTH, PeriphBus
from sdram_controller import HOST_ADDR_WIDTH
from spi_flash import FLASH_ADDR_WIDTH
from uart import UART_ADDR_WIDTH


class System(wiring.Component):
    flash: Out(MemoryPort(FLASH_ADDR_WIDTH))
    ram: Out(MemoryPort(HOST_ADDR_WIDTH))
    uart: Out(MemoryPort(UART_ADDR_WIDTH))

    def __init__(self, **cpu_kwargs):
        super().__init__()
        self.cpu_kwargs = cpu_kwargs

    def elaborate(self, platform):
        m = Module()
        m.submodules.cpu = cpu = self.cpu = CPU(**self.cpu_kwargs)
        m.submodules.arb = arb = MemoryArbiter(CPU_ADDR_WIDTH, n=2)
        m.submodules.mmu = mmu = MMU()
        m.submodules.sel = sel = MemorySelect(periph_addr_width=PERIPH_ADDR_WIDTH)
        m.submodules.periph = periph = PeriphBus()
        m.submodules.ctx_dev = ctx_dev = ContextDevice()

        # two initiators share the one port: the stack cache (port0, wins)
        # and the CPU's code/data accesses (port1). Each comes with the
        # address space its requests are made in.
        connect(m, cpu.stack_mem, arb.port0)
        m.d.comb += arb.space0.eq(Space.PSTACK)
        connect(m, cpu.mem, arb.port1)
        m.d.comb += arb.space1.eq(cpu.space)
        m.d.comb += [
            cpu.stack_mem_fault.eq(arb.fault0),
            cpu.mem_fault.eq(arb.fault1),
        ]

        # the translation, and the refusal that comes back
        connect(m, arb.mem, mmu.cpu)
        m.d.comb += [
            mmu.space.eq(arb.space),
            mmu.user.eq(cpu.user),
            mmu.reset_system.eq(cpu.soft_reset),
            arb.fault.eq(mmu.fault),
        ]
        connect(m, mmu.mem, sel.cpu)
        m.d.comb += mmu.mem_fault.eq(sel.fault)

        # the targets
        connect(m, flipped(self.flash), sel.flash)
        connect(m, flipped(self.ram), sel.ram)
        connect(m, sel.periph, periph.bus)
        connect(m, flipped(self.uart), periph.uart)
        connect(m, periph.ctx, ctx_dev.bus)
        connect(m, periph.mmu, mmu.regs)
        m.d.comb += [
            cpu.ctx_addr.eq(ctx_dev.ctx_addr),
            cpu.ctx_we.eq(ctx_dev.ctx_we),
            cpu.ctx_wdata.eq(ctx_dev.ctx_wdata),
            ctx_dev.ctx_rdata.eq(cpu.ctx_rdata),
        ]

        return m
