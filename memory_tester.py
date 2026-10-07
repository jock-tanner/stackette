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

        # The controller has no reset port anymore: it is reset by the reset
        # of the domain it runs in. Run it in its own domain ("sdram_ctl",
        # same clock as "sync") so the reset can be held for reset_cycles
        # after the PLL locks, as the old RESET state did via the rst port.
        sdram_controller = SDRAMController()
        m.submodules.sdram_controller = DomainRenamer({'sync': 'sdram_ctl'})(
            sdram_controller
        )
        host = sdram_controller.host
        resp = sdram_controller.resp
        sdram = sdram_controller.sdram

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
        m.domains.sdram_ctl = ClockDomain('sdram_ctl')

        m.d.comb += [
            host.byte_hi.eq(1),     # high byte enabled
            host.byte_lo.eq(1),     # low byte enabled
        ]

        with m.FSM(init='RESET') as fsm:
            with m.State('RESET'):
                # Keep the controller in reset (see below) for reset_cycles,
                # then let it run its own internal init sequence.
                with m.If(rst_cnt == 0):
                    m.d.sync += addr.eq(0)
                    m.next = 'WRITE'
                with m.Else():
                    m.d.sync += rst_cnt.eq(rst_cnt - 1)

            with m.State('WRITE'):
                # Hold the request until the controller accepts it
                # (valid & ready). ready stays low during the controller's
                # init sequence and whenever a refresh is due, so nothing
                # is dropped. Writes have no response, so move straight on
                # to the next address once accepted.
                m.d.comb += [
                    host.valid.eq(1),
                    host.we.eq(1),
                    host.addr.eq(addr),
                    host.wdata.eq(self.pattern),
                    self.write_wait.eq(~host.ready),
                ]
                with m.If(host.ready):
                    with m.If(addr == self.word_count - 1):
                        m.d.sync += addr.eq(0)
                        m.next = 'READ_ISSUE'
                    with m.Else():
                        m.d.sync += addr.eq(addr + 1)

            with m.State('READ_ISSUE'):
                # Same handshake as WRITE: hold the request until accepted.
                m.d.comb += [
                    host.valid.eq(1),
                    host.we.eq(0),
                    host.addr.eq(addr),
                ]
                with m.If(host.ready):
                    m.next = 'READ_WAIT'

            with m.State('READ_WAIT'):
                m.d.comb += self.read_wait.eq(~resp.valid)
                with m.If(resp.valid):
                    with m.If(resp.data != self.pattern):
                        m.d.sync += mismatch.eq(1)
                    with m.If(addr == self.word_count - 1):
                        m.next = 'DONE'
                    with m.Else():
                        m.d.sync += addr.eq(addr + 1)
                        m.next = 'READ_ISSUE'

            with m.State('DONE'):
                m.d.comb += self.done.eq(1)

        m.d.comb += [
            # create clock domains (if necessary)
            ClockSignal('sync').eq(pll_gen.clk_sys),
            ResetSignal('sync').eq(~pll_gen.locked),
            ClockSignal('sdram').eq(pll_gen.clk_sdram),
            ResetSignal('sdram').eq(~pll_gen.locked),
            ClockSignal('sdram_ctl').eq(pll_gen.clk_sys),
            ResetSignal('sdram_ctl').eq(
                ~pll_gen.locked | fsm.ongoing('RESET')
            ),

            # wire up sdram
            # - clock
            sdram_pins.clk.o.eq(pll_gen.clk_sdram),

            # - address
            sdram_pins.a.o.eq(sdram.a),
            sdram_pins.ba.o.eq(sdram.ba),

            # - data
            sdram_pins.dq.o.eq(sdram.dq_o),
            sdram_pins.dq.oe.eq(sdram.dq_oe),
            sdram.dq_i.eq(sdram_pins.dq.i),

            # - control
            sdram_pins.ras.o.eq(~sdram.ras_n),
            sdram_pins.cas.o.eq(~sdram.cas_n),
            sdram_pins.we.o.eq(~sdram.we_n),
            sdram_pins.cs.o.eq(~sdram.cs_n),
            sdram_pins.clk_en.o.eq(sdram.cke),
            sdram_pins.dqm.o.eq(sdram.dqm),

            # LEDs
            mismatch_led.o.eq(self.led),
            done_led.o.eq(self.done),
            write_wait_led.o.eq(self.write_wait),
            read_wait_led.o.eq(self.read_wait),
        ]

        return m


if __name__ == '__main__':
    from amaranth_boards.de0nano import DE0NanoPlatform

    platform = DE0NanoPlatform()
    platform.build(SDRAMTest(), do_program=True)
