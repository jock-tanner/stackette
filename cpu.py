from amaranth import *
from amaranth.lib.wiring import Component, Out, connect

from memory_port import MemoryPort
from memory_select import CPU_ADDR_WIDTH, Space

# Initial pointer values from the memory map, for the CPU reset logic.
IP_INIT = 0x0000
RSP_INIT = 0x0000
PSP_INIT = 0xf000
SFP_INIT = 0xf000


class CPU(Component):
    mem: Out(MemoryPort(CPU_ADDR_WIDTH))
    space: Out(Space)

    # for display purpose
    high_addr: Out(4)

    def elaborate(self, platform):
        m = Module()

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

        with m.FSM(reset='FETCH') as fsm:
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
        from memory_select import MemorySelect
        from de0nano_sdram import DE0NanoSDRAM
        from de0nano_spi_flash import DE0NanoSPIFlash

        m = Module()

        m.submodules.sdram = sdram = DE0NanoSDRAM()
        m.submodules.flash = flash = DE0NanoSPIFlash()

        m.submodules.mem_sel = mem_sel = MemorySelect()
        connect(m, flash.bus, mem_sel.flash)
        connect(m, sdram.bus, mem_sel.ram)

        m.submodules.cpu = cpu = CPU()
        connect(m, cpu.mem, mem_sel.cpu)
        m.d.comb += mem_sel.space.eq(cpu.space)

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
