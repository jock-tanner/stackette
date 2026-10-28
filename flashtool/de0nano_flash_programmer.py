"""
Flash programmer design for the DE0-Nano: a UART to SPI bridge on the pins of
the SPI flash (de0nano_spi_flash.py) and the UART pins of the CPU design, so
nothing needs to be rewired.

    python de0nano_flash_programmer.py [baud]

builds the design and loads it into the FPGA (volatile, JTAG). Then use
flashtool.py with the same baud rate. Baud rates up to 1 Mbaud work if the
USB serial adapter supports them; 115200 is the default.

LED 0: the flash is selected (CS# low). LED 1: the bridge is working on a
command.
"""
import sys

from amaranth import Elaboratable, Module
from amaranth.build import Pins, Resource
from amaranth.lib import wiring

from de0nano_spi_flash import DE0NanoSPIFlashPhy
from spi_bridge import SPIBridge


class FlashProgrammer(Elaboratable):
    def __init__(self, baud=115200):
        self.baud = baud

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = bridge = SPIBridge(
            divisor=round(50_000_000 / self.baud)
        )
        m.submodules.phy = phy = DE0NanoSPIFlashPhy()
        wiring.connect(m, bridge.spi, phy.spi)

        platform.add_resources([
            Resource('tx', 0, Pins('8', dir='o', conn=('gpio', 0))),
            Resource('rx', 0, Pins('10', dir='i', conn=('gpio', 0))),
        ])
        tx_pin = platform.request('tx')
        rx_pin = platform.request('rx')
        m.d.comb += [
            tx_pin.o.eq(bridge.serial.tx),
            bridge.serial.rx.eq(rx_pin.i),
            platform.request('led', 0).o.eq(~bridge.spi.cs_n),
            platform.request('led', 1).o.eq(bridge.busy),
        ]
        return m


if __name__ == '__main__':
    from amaranth_boards.de0nano import DE0NanoPlatform

    baud = int(sys.argv[1]) if len(sys.argv) > 1 else 115200
    DE0NanoPlatform().build(FlashProgrammer(baud), do_program=True)
