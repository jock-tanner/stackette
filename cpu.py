from amaranth import *
from amaranth.lib.wiring import Component, Out, connect, flipped

from memory_port import MemoryPort
from memory_select import CPU_ADDR_WIDTH, Space
from stack_cache import Maint, Shift, StackCache

# Initial pointer values from the memory map, for the CPU reset logic.
IP_INIT = 0x0000
RSP_INIT = 0x0000
PSP_INIT = 0xf000
SFP_INIT = 0xf000


class CPU(Component):
    mem: Out(MemoryPort(CPU_ADDR_WIDTH))        # code and data accesses
    space: Out(Space)                           # address space of `mem` requests
    stack_mem: Out(MemoryPort(CPU_ADDR_WIDTH))  # the stack cache's spill/fill port
                                                # (always Space.PSTACK)

    # for display purpose
    high_addr: Out(4)

    def elaborate(self, platform):
        m = Module()

        # parameter stack: the cache sits between the CPU core and memory.
        # The core talks to it through stack.cmd; the cache's own memory port
        # is passed straight out of the CPU as `stack_mem`.
        m.submodules.stack = stack = StackCache()
        connect(m, flipped(self.stack_mem), stack.mem)
        cmd = stack.cmd

        # memory interface
        req, resp = self.mem.req, self.mem.resp

        # programming model registers
        ip = Signal(16, init=IP_INIT)
        rsp = Signal(16, init=RSP_INIT)
        sfp = Signal(16, init=SFP_INIT)
        mbp = Signal(16)
        midx = Signal(16)
        sw = Signal(16)

        instr = Signal(16)

        m.d.comb += self.space.eq(Space.CODE)


        m.d.comb += self.high_addr.eq(ip[12:])  # only for display
        wait_cnt = Signal(12, init=0xfff)

        with m.FSM(reset='INIT') as fsm:
            with m.State('INIT'):
                # the cache does not know PSP at power-up: set it once
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.SET_PSP),
                    cmd.arg.eq(PSP_INIT),
                ]
                with m.If(cmd.ready):
                    m.next = 'FETCH'

            with m.State('FETCH'):
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(0),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq(ip[1:]),
                ]
                with m.If(req.ready):
                    m.next = 'DECODE'

            with m.State('DECODE'):
                with m.If(resp.valid):
                    m.d.sync += ip.eq(ip + 2)
                    m.d.sync += instr.eq(resp.data)
                    m.next = 'PUSH'

            with m.State('PUSH'):
                # placeholder use of the cache: push every fetched word.
                # A real decoder drives shift / need / w0_* / w1_* here;
                # fields not assigned stay 0 (no shift, nothing needed).
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.shift.eq(Shift.PUSH),
                    cmd.arg.eq(instr),
                ]
                with m.If(cmd.ready):
                    m.next = 'WAIT'

            with m.State('WAIT'):
                with m.If(wait_cnt == 0):
                    m.d.sync += wait_cnt.eq(0xfff)
                    m.next = 'FETCH'

                with m.Else():
                    m.d.sync += wait_cnt.eq(wait_cnt - 1)

        return m


class CPUTop(Elaboratable):
    def elaborate(self, platform):
        from memory_arbiter import MemoryArbiter
        from memory_select import MemorySelect
        from de0nano_sdram import DE0NanoSDRAM
        from de0nano_spi_flash import DE0NanoSPIFlash

        m = Module()
        m.submodules.cpu = cpu = CPU()

        # connect memry selector to memories
        m.submodules.mem_sel = mem_sel = MemorySelect()
        m.submodules.sdram = sdram = DE0NanoSDRAM()
        m.submodules.flash = flash = DE0NanoSPIFlash()
        connect(m, flash.bus, mem_sel.flash)
        connect(m, sdram.bus, mem_sel.ram)

        # Two initiators share the one MemorySelect port: the stack cache
        # (port0, wins) and the CPU's code/data accesses (port1). Each comes
        # with the address space its requests are made in.
        m.submodules.arb = arb = MemoryArbiter(CPU_ADDR_WIDTH, n=2)
        connect(m, cpu.stack_mem, arb.port0)
        m.d.comb += arb.space0.eq(Space.PSTACK)
        connect(m, cpu.mem, arb.port1)
        m.d.comb += arb.space1.eq(cpu.space)
        connect(m, arb.mem, mem_sel.cpu)
        m.d.comb += mem_sel.space.eq(arb.space)

        m.d.comb += [
            platform.request('led', 0).o.eq(cpu.high_addr[0]),
            platform.request('led', 1).o.eq(cpu.high_addr[1]),
            platform.request('led', 2).o.eq(cpu.high_addr[2]),
            platform.request('led', 3).o.eq(cpu.high_addr[3]),
        ]

        return m


if __name__ == '__main__':
    from amaranth_boards.de0nano import DE0NanoPlatform

    DE0NanoPlatform().build(CPUTop(), do_program=True)
