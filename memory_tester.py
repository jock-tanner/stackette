from amaranth import *
from sdram_controller import SDRAMController
from pll_clock import DE0NanoSDRAMPLL


class SDRAMTest(Elaboratable):
    """
    SDRAM tester.
    """
    def __init__(
        self, *, word_count=16777216, pattern=0xA5A5, reset_cycles=1024,
        **sdram_kwargs
    ):
        self.word_count = word_count
        self.pattern = pattern
        self.reset_cycles = reset_cycles
        self.sdram_kwargs = sdram_kwargs

        # Outputs
        self.led = Signal()  # sticky: goes high on first mismatch, stays high
        self.done = Signal()  # high once the write+read pass has finished
        self.write_wait = Signal()
        self.read_wait = Signal()

    def elaborate(self, platform):
        m = Module()

        clk50_pin = platform.request('clk50', 0)
        sdram_pins = platform.request('sdram')
        mismatch_led = platform.request('led', 0)
        done_led = platform.request('led', 7)
        write_wait_led = platform.request('led', 3)
        read_wait_led = platform.request('led', 4)

        m.submodules.pll_gen = pll_gen = DE0NanoSDRAMPLL()
        m.submodules.sdram_controller = sdram_controller = SDRAMController(
            **self.sdram_kwargs
        )
        addr_bits = max(self.word_count - 1, 1).bit_length()
        addr = Signal(addr_bits)
        rst_cnt = Signal(range(self.reset_cycles + 1), init=self.reset_cycles)
        mismatch = Signal()  # sticky error flag
        m.d.comb += [
            self.led.eq(mismatch),
            self.done.eq(0),
        ]

        m.d.comb += pll_gen.clk_50mhz.eq(clk50_pin.i)

        m.domains.sync = ClockDomain('sync')
        m.domains.sdram = ClockDomain('sdram')
        m.d.comb += [
            # create clock domains (if necessary)
            ClockSignal('sync').eq(pll_gen.clk_sys),
            ResetSignal('sync').eq(~pll_gen.locked),
            ClockSignal('sdram').eq(pll_gen.clk_sdram),
            ResetSignal('sdram').eq(~pll_gen.locked),

            # wire up sdram
            # - clock
            sdram_pins.clk.o.eq(pll_gen.clk_sdram),

            # - address
            sdram_pins.a.o.eq(sdram_controller.addr),
            sdram_pins.ba.o.eq(sdram_controller.bank_addr),

            # - data
            sdram_pins.dq.o.eq(sdram_controller.data_o),
            sdram_pins.dq.oe.eq(sdram_controller.data_oe),
            sdram_controller.data_i.eq(sdram_pins.dq.i),

            # - control
            sdram_pins.ras.o.eq(~sdram_controller.ras_n),
            sdram_pins.cas.o.eq(~sdram_controller.cas_n),
            sdram_pins.we.o.eq(~sdram_controller.we_n),
            sdram_pins.cs.o.eq(~sdram_controller.cs_n),
            sdram_pins.clk_en.o.eq(sdram_controller.clock_enable),
            sdram_pins.dqm.o.eq(Cat(
                sdram_controller.data_mask_low,
                sdram_controller.data_mask_high,
            )),

            # LEDs
            mismatch_led.o.eq(self.led),
            done_led.o.eq(self.done),
            write_wait_led.o.eq(self.write_wait),
            read_wait_led.o.eq(self.read_wait),
        ]

        with m.FSM(init='RESET') as fsm:
            with m.State('RESET'):
                # Hold sdram.rst_n low for reset_cycles, then let the
                # controller run its own internal init sequence.
                m.d.comb += sdram_controller.rst.eq(1)
                with m.If(rst_cnt == 0):
                    m.d.sync += addr.eq(0)
                    m.next = 'WRITE_ISSUE'
                with m.Else():
                    m.d.sync += rst_cnt.eq(rst_cnt - 1)

            with m.State('WRITE_ISSUE'):
                # Hold wr_enable asserted every cycle until the controller
                # acknowledges with busy - a one-cycle pulse can land on the
                # same cycle a refresh becomes due, in which case the
                # controller services the refresh instead and silently
                # drops the request.
                m.d.comb += [
                    sdram_controller.rst.eq(0),
                    sdram_controller.wr_addr.eq(addr),
                    sdram_controller.wr_data.eq(self.pattern),
                    sdram_controller.wr_enable.eq(1),
                ]
                with m.If(sdram_controller.busy):
                    m.next = 'WRITE_WAIT'

            with m.State('WRITE_WAIT'):
                m.d.comb += sdram_controller.rst.eq(0)
                m.d.comb += self.write_wait.eq(1)
                with m.If(~sdram_controller.busy):
                    with m.If(addr == self.word_count - 1):
                        m.d.sync += addr.eq(0)
                        m.next = 'READ_ISSUE'
                    with m.Else():
                        m.d.sync += addr.eq(addr + 1)
                        m.next = 'WRITE_ISSUE'

            with m.State('READ_ISSUE'):
                # Same reasoning as WRITE_ISSUE: hold rd_enable until busy
                # confirms the read was accepted.
                m.d.comb += [
                    sdram_controller.rst.eq(0),
                    sdram_controller.rd_addr.eq(addr),
                    sdram_controller.rd_enable.eq(1),
                ]
                with m.If(sdram_controller.busy):
                    m.next = 'READ_WAIT'

            with m.State('READ_WAIT'):
                m.d.comb += sdram_controller.rst.eq(0)
                m.d.comb += self.read_wait.eq(~sdram_controller.rd_ready)
                with m.If(sdram_controller.rd_ready):
                    with m.If(sdram_controller.rd_data != self.pattern):
                        m.d.sync += mismatch.eq(1)
                    with m.If(addr == self.word_count - 1):
                        m.next = 'DONE'
                    with m.Else():
                        m.d.sync += addr.eq(addr + 1)
                        m.next = 'READ_ISSUE'

            with m.State('DONE'):
                m.d.comb += [
                    sdram_controller.rst.eq(0),
                    self.done.eq(1),
                ]

        return m


if __name__ == '__main__':
    from amaranth_boards.de0nano import DE0NanoPlatform

    platform = DE0NanoPlatform()
    platform.build(SDRAMTest(), do_program=True)
