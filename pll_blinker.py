from amaranth import *
from amaranth_boards.de0nano import DE0NanoPlatform

from pll_clock import DE0NanoSDRAMPLL


class PLLBlinker(Elaboratable):
    def elaborate(self, platform):
        m = Module()

        clk50_pin = platform.request('clk50', 0)
        sys_led = platform.request('led', 0)
        sdram_led = platform.request('led', 1)

        # connect board oscillator to PLL input
        m.submodules.pll_gen = pll_gen = DE0NanoSDRAMPLL(clk_sdram_mul=2)
        m.d.comb += pll_gen.clk_50mhz.eq(clk50_pin.i)

        m.domains.sync = ClockDomain('sync')
        m.d.comb += [
            ClockSignal('sync').eq(pll_gen.clk_sdram),

            # Hold fabric in reset until PLL stabilizes
            ResetSignal('sync').eq(~pll_gen.locked),
        ]

        clk_freq = platform.default_clk_frequency
        half_second_ticks = int(clk_freq // 2)
        counter = Signal(range(half_second_ticks))
        with m.If(counter == half_second_ticks - 1):
            m.d.sync += counter.eq(0)
            m.d.sync += sys_led.o.eq(~sys_led.o)
        with m.Else():
            m.d.sync += counter.eq(counter + 1)

        return m


if __name__ == '__main__':
    platform = DE0NanoPlatform()
    platform.build(PLLBlinker(), do_program=True)
