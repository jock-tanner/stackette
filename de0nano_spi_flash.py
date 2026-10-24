"""
DE0-Nano wiring for the MX25L8005 SPI flash, plus a small bring-up design.

The flash sits on the GPIO_0 header (JP1). Wiring (all 3.3 V, so no level
shifting is needed; never use header pin 11, which is 5 V):

    header pin   FPGA pin   signal         flash pin (8-pin DIP / SOP)
    ----------   --------   ------------   ---------------------------
    29           -          3.3 V          8  VCC   (+ 0.1 uF to GND)
    30           -          GND            4  GND
    31           C9         cs_n           1  CS#
    32           D9         sclk           6  SCLK
    33           E11        mosi           5  SI
    34           E10        miso           2  SO
    (3.3 V)      -          tie high       3  WP#
    (3.3 V)      -          tie high       7  HOLD#

The FPGA pin names are the GPIO_0[24..27] assignments from a community
pinout table of the board; check them against the DE0-Nano user manual before
you power up. To use other header pins, change FLASH_PINS below.

DE0NanoSPIFlash has the same shape as DE0NanoSDRAM: one MemoryPort (`bus`, a
19-bit word address, read-only) and nothing else, so it plugs into the same
wiring.connect() lines. It does not create clocks: it runs in whatever "sync"
domain the design already has (the one DE0NanoSDRAM creates, or the board's
default 50 MHz clock in a flash-only design).
"""
from amaranth import Cat, Elaboratable, Module, Mux, Signal
from amaranth.build import Attrs, Pins, Resource, Subsignal
from amaranth.lib.wiring import connect, flipped, Component, In

from memory_port import MemoryPort
from spi_flash import FLASH_ADDR_WIDTH, SPIFlashPins, SPIFlashReader

# FPGA pins of the four SPI signals (GPIO_0 header pins 31..34).
FLASH_PINS = dict(cs_n='C9', clk='D9', mosi='E11', miso='E10')


def spi_flash_resources(pins=FLASH_PINS):
    """The 'spi_flash' resource that DE0NanoSPIFlashPhy requests."""
    return [
        Resource(
            'spi_flash', 0,
            Subsignal('cs_n', Pins(pins['cs_n'], dir='o')),
            Subsignal('clk', Pins(pins['clk'], dir='o')),
            Subsignal('mosi', Pins(pins['mosi'], dir='o')),
            Subsignal('miso', Pins(pins['miso'], dir='i')),
            Attrs(io_standard='3.3-V LVTTL'),
        )
    ]


class DE0NanoSPIFlashPhy(Component):
    """
    Maps the SPIFlashPins signature onto the pins above. It adds the
    'spi_flash' resource to the platform itself, so do not add it elsewhere.
    """
    spi: In(SPIFlashPins())

    def __init__(self, *, pins=FLASH_PINS):
        super().__init__()
        self.pins = pins

    def elaborate(self, platform):
        m = Module()
        platform.add_resources(spi_flash_resources(self.pins))
        pins = platform.request('spi_flash', 0)
        s = self.spi
        m.d.comb += [
            pins.cs_n.o.eq(s.cs_n),     # physical levels, no inversion
            pins.clk.o.eq(s.sclk),
            pins.mosi.o.eq(s.mosi),
            s.miso.eq(pins.miso.i),
        ]
        return m


class DE0NanoSPIFlash(Component):
    """SPI flash reader plus pins, behind a read-only MemoryPort."""
    bus: In(MemoryPort(FLASH_ADDR_WIDTH))

    def __init__(self, *, half_period=2, pins=FLASH_PINS):
        super().__init__()
        self.half_period = half_period
        self.pins = pins

    def elaborate(self, platform):
        m = Module()
        m.submodules.reader = reader = SPIFlashReader(
            half_period=self.half_period
        )
        m.submodules.phy = phy = DE0NanoSPIFlashPhy(pins=self.pins)

        connect(m, reader.spi, phy.spi)
        connect(m, flipped(self.bus), reader.bus)
        return m


class FlashBringup(Elaboratable):
    """
    Wiring check without SDRAM. Reads the flash contents byte by byte and
    shows each byte on LEDs 0..7 for about 0.7 s, then moves on to the next.
    Program the flash with a recognizable file first, e.g. the bytes
    01 02 04 08 10 20 40 80: a single lit LED then walks across the board.
    All LEDs lit means MISO reads high (flash not responding or MISO floating);
    all dark usually means MISO is stuck low or the flash is not powered.
    """
    def elaborate(self, platform):
        m = Module()
        m.submodules.flash = flash = DE0NanoSPIFlash()
        req, resp = flash.bus.req, flash.bus.resp

        word = Signal(16)
        idx = Signal(FLASH_ADDR_WIDTH + 1)      # byte index: {word addr, hi}
        timer = Signal(25)                      # ~0.67 s at 50 MHz
        m.d.sync += timer.eq(timer + 1)
        with m.If(timer == 0):
            m.d.sync += idx.eq(idx + 1)

        with m.FSM():
            with m.State('ISSUE'):
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(0),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq(idx[1:]),
                ]
                with m.If(req.ready):
                    m.next = 'WAIT'
            with m.State('WAIT'):
                with m.If(resp.valid):
                    m.d.sync += word.eq(resp.data)
                    m.next = 'ISSUE'

        leds = Cat([platform.request('led', i).o for i in range(8)])
        m.d.comb += leds.eq(Mux(idx[0], word[8:16], word[0:8]))
        return m


if __name__ == '__main__':
    from amaranth_boards.de0nano import DE0NanoPlatform

    DE0NanoPlatform().build(FlashBringup(), do_program=True)
