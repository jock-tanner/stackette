from amaranth import *
from amaranth.build import Attrs, Pins, Resource, Subsignal
from amaranth.lib.wiring import connect, Component, In, Out, Signature


ETHER_PINS = {
    'cs_n': 'N15',
    'sclk': 'P14',
    'mosi': 'L14',
    'miso': 'N14',
    'int': 'L13',
}


def spi_ether_resources(pins):
    return [
        Resource(
            'spi_ether',
            0,
            Subsignal('cs_n', Pins(pins['cs_n'], dir='o')),
            Subsignal('sclk', Pins(pins['sclk'], dir='o')),
            Subsignal('mosi', Pins(pins['mosi'], dir='o')),
            Subsignal('miso', Pins(pins['miso'], dir='i')),
            Subsignal('int', Pins(pins['int'], dir='i')),
            Attrs(io_standard='3.3-V LVTTL'),
        ),
    ]


class SPIEtherPins(Signature):
    """SPI pins, from the controller's point of view."""
    def __init__(self):
        super().__init__({
            'cs_n': Out(1),
            'sclk': Out(1),
            'mosi': Out(1),
            'miso': In(1),
            'int': In(1),
        })


class DE0NanoSPIEtherPins(Component):
    spi: In(SPIEtherPins())

    def __init__(self, *, pins=ETHER_PINS):
        super().__init__()
        self.pins = pins

    def elaborate(self, platform):
        m = Module()

        platform.add_resources(spi_ether_resources(self.pins))
        pins = platform.request('spi_ether', 0)

        m.d.comb += [
            pins.cs_n.o.eq(self.spi.cs_n),
            pins.sclk.o.eq(self.spi.sclk),
            pins.mosi.o.eq(self.spi.mosi),
            self.spi.miso.eq(pins.miso.i),
            self.spi.int.eq(pins.int.i),
        ]
        return m
