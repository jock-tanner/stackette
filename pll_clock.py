from amaranth import *


class DE0NanoSDRAMPLL(Elaboratable):
    def __init__(self, clk_sdram_div=1, clk_sdram_mul=1):
        # keep defaults for normal use
        self.clk_sdram_div = clk_sdram_div
        self.clk_sdram_mul = clk_sdram_mul

        # Clock inputs & control
        self.clk_50mhz = Signal()

        # PLL Outputs
        self.clk_sys = Signal()    # Drives FPGA controller logic
        self.clk_sdram = Signal()  # Tied to the external DRAM_CLK pin
        self.locked = Signal()

    def elaborate(self, platform):
        m = Module()

        # instantiate the Intel Cyclone IV ALTPLL primitive
        m.submodules.pll = Instance(
            'altpll',

            # generic parameters
            p_device_family='Cyclone IV E',
            p_instance_type='DEFAULT',

            # Input clock properties (50 MHz = 20,000 ps period)
            p_inclk0_input_frequency=20000,
            p_operation_mode='NORMAL',

            # system clock (e.g., 50 MHz, 0 degree phase shift)
            p_clk0_multiply_by=1,
            p_clk0_divide_by=1,
            p_clk0_phase_shift='0',

            # SDRAM dedicated clock (50 MHz, phase-shifted by -3ns)
            p_clk1_multiply_by=self.clk_sdram_mul,
            p_clk1_divide_by=self.clk_sdram_div,

            # -3ns shift to counter PCB trace skew
            p_clk1_phase_shift='-3000',

            p_compensate_clock='CLK0',
            p_intended_device_family='Cyclone IV E',

            # port connections

            # 2-bit vector [clk1, clk0]
            i_inclk=Cat(self.clk_50mhz, C(0, 1)),

            # tie reset low or connect to a button
            i_areset=C(0, 1),

            # clk output vector [clk1, clk0]
            o_clk=Cat(self.clk_sys, self.clk_sdram),

            o_locked=self.locked,
        )

        return m
