"""
DE0-Nano SDRAM subsystem: everything between the board's clock/SDRAM pins and
a platform-independent MemoryPort.

  clk50 pin -> PLL -> "sync" domain (clk_sys) + SDRAM clock pin (clk_sdram)
  MemoryPort -> SDRAMController -> SDRAMPins -> DE0NanoSDRAMPhy -> board pins

The CPU (or tester, or a cache in front of this) only sees `bus`, and only
uses the "sync" domain this module creates. All platform.request() calls
and pin polarity live here and in the Phy, nowhere else.
"""
from amaranth import (
    ClockDomain, ClockSignal, DomainRenamer, Module, ResetSignal, Signal,
)
from amaranth.lib import wiring
from amaranth.lib.wiring import In

from memory_port import MemoryPort
from sdram_controller import HOST_ADDR_WIDTH, SDRAMController, SDRAMPins
from pll_clock import DE0NanoSDRAMPLL


class DE0NanoSDRAMPhy(wiring.Component):
    """Maps the SDRAMPins signature onto the board's 'sdram' resource."""
    sdram: In(SDRAMPins())

    def __init__(self, *, clk):
        super().__init__()
        self.clk = clk      # the phase-shifted SDRAM clock, driven to the pin

    def elaborate(self, platform):
        m = Module()
        pins = platform.request('sdram')
        s = self.sdram
        m.d.comb += [
            pins.clk.o.eq(self.clk),

            pins.a.o.eq(s.a),
            pins.ba.o.eq(s.ba),

            pins.dq.o.eq(s.dq_o),
            pins.dq.oe.eq(s.dq_oe),
            s.dq_i.eq(pins.dq.i),

            # the resource uses active-high logical levels
            pins.ras.o.eq(~s.ras_n),
            pins.cas.o.eq(~s.cas_n),
            pins.we.o.eq(~s.we_n),
            pins.cs.o.eq(~s.cs_n),
            pins.clk_en.o.eq(s.cke),
            pins.dqm.o.eq(s.dqm),
        ]
        return m


class DE0NanoSDRAM(wiring.Component):
    """
    Clocking, power-up reset hold, SDRAM controller and pins.

    reset_cycles: how long the controller is held in reset after the PLL
    locks (the SDRAM needs time after power-up before initialization).
    read_delay: see SDRAMController.
    Until the controller's init sequence finishes, bus.req.ready stays low,
    so users just keep valid asserted and need no startup state of their own.
    """
    bus: In(MemoryPort(HOST_ADDR_WIDTH))

    def __init__(self, *, reset_cycles=1024, read_delay=1):
        super().__init__()
        self.reset_cycles = reset_cycles
        self.read_delay = read_delay

    def elaborate(self, platform):
        m = Module()

        clk50 = platform.request('clk50', 0)
        m.submodules.pll = pll = DE0NanoSDRAMPLL()
        m.d.comb += pll.clk_50mhz.eq(clk50.i)

        # "sync" is the system domain. "sdram_ctl" has the same clock but its
        # own reset, so the controller can be held in reset for longer.
        m.domains.sync = ClockDomain('sync')
        m.domains.sdram_ctl = ClockDomain('sdram_ctl')

        hold = Signal(range(self.reset_cycles + 1), init=self.reset_cycles)
        with m.If(hold != 0):
            m.d.sync += hold.eq(hold - 1)

        m.d.comb += [
            ClockSignal('sync').eq(pll.clk_sys),
            ResetSignal('sync').eq(~pll.locked),
            ClockSignal('sdram_ctl').eq(pll.clk_sys),
            ResetSignal('sdram_ctl').eq(~pll.locked | (hold != 0)),
        ]

        controller = SDRAMController(read_delay=self.read_delay)
        m.submodules.controller = DomainRenamer({'sync': 'sdram_ctl'})(controller)
        m.submodules.phy = phy = DE0NanoSDRAMPhy(clk=pll.clk_sdram)

        wiring.connect(m, controller.sdram, phy.sdram)
        # forward our own port to the controller's (same direction, so flip)
        wiring.connect(m, wiring.flipped(self.bus), controller.bus)
        return m
