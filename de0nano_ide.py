from amaranth import Module
from amaranth.build import Attrs, Pins, Resource, Subsignal
from amaranth.lib.wiring import Component, In

from ide_pio import IDEPins

# the IDE-CF adapter on the DE0-Nano: the pins of the FPGA
IDE_PINS = {
    'da': 'J16 J13 K15',
    'dd': 'L16 N9 N12 P11 T10 R12 R13 T14 T13 T12 T11 R11 R10 P9 N11 K16',
    'cs0_n': 'N16',
    'cs1_n': 'P16',
    'dior_n': 'P15',
    'diow_n': 'R16',
    'intrq': 'L15',
    'iordy': 'R14',
    # RESET# of the card (pin 1 of the IDE connector); None: not wired
    'reset_n': 'T15',
}


def ide_resources(pins):
    return [
        Resource(
            'ide',
            0,
            Subsignal('da', Pins(pins['da'], dir='o')),
            Subsignal('dd', Pins(pins['dd'], dir='io')),
            Subsignal('cs0_n', Pins(pins['cs0_n'], dir='o')),
            Subsignal('cs1_n', Pins(pins['cs1_n'], dir='o')),
            Subsignal('dior_n', Pins(pins['dior_n'], dir='o')),
            Subsignal('diow_n', Pins(pins['diow_n'], dir='o')),
            Subsignal('intrq', Pins(pins['intrq'], dir='i')),
            *(
                [Subsignal('reset_n', Pins(pins['reset_n'], dir='o'))]
                if pins.get('reset_n') else []
            ),
            # a pull-up: an adapter that does not drive IORDY means "ready"
            Subsignal(
                'iordy', Pins(pins['iordy'], dir='i'),
                Attrs(weak_pull_up_resistor='ON'),
            ),
            Attrs(io_standard='3.3-V LVTTL'),
        ),
    ]


class DE0NanoIDEPins(Component):
    ide: In(IDEPins())

    def __init__(self, *, pins=IDE_PINS):
        super().__init__()
        self.pins = pins

    def elaborate(self, platform):
        m = Module()

        platform.add_resources(ide_resources(self.pins))
        pins = platform.request('ide', 0)

        m.d.comb += [
            pins.da.o.eq(self.ide.da),
            pins.cs0_n.o.eq(self.ide.cs0_n),
            pins.cs1_n.o.eq(self.ide.cs1_n),
            pins.dior_n.o.eq(self.ide.dior_n),
            pins.diow_n.o.eq(self.ide.diow_n),
            pins.dd.o.eq(self.ide.dd_o),
            pins.dd.oe.eq(self.ide.dd_oe),
            self.ide.dd_i.eq(pins.dd.i),
            self.ide.intrq.eq(pins.intrq.i),
            self.ide.iordy.eq(pins.iordy.i),
        ]
        if self.pins.get('reset_n'):
            m.d.comb += pins.reset_n.o.eq(self.ide.reset_n)
        return m
