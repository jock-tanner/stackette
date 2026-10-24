"""
Read-only SPI NOR flash reader (MX25L8005 and compatible) behind a MemoryPort.

Platform independent: it only talks to an SPIFlashPins signature. See
de0nano_spi_flash.py for the board pin mapping.

  * 8 Mbit = 1 MiB = 512 Ki words of 16 bit, so the word address is 19 bits.
  * Words are little-endian: the byte at the even address is the low byte.
  * Only reads are supported. A write request is accepted and silently
    dropped. Byte enables are ignored (every read returns a full word).
  * Uses the plain READ (03h) command in SPI mode 0, MSB first. The command
    is 8 bits, the address 24 bits, then the data bits follow.
  * Sequential reads are fast: after a read the chip stays selected, and if
    the next request is for the following word, only 16 more clocks are sent
    (no new command). Anything else deselects the chip for cs_high_cycles
    and starts a new READ.

  Clocks per word: 48 for a random read, 16 for a sequential one.
  SCLK = clock / (2 * half_period); the default gives 12.5 MHz from 50 MHz.
  Check the READ (03h) maximum clock frequency in the datasheet of your exact
  part before raising it.

Protocol notes (see memory_port.py): hold req.valid until req.ready; one
resp.valid pulse per accepted read.
"""
from amaranth import Cat, Const, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from memory_port import MemoryPort

FLASH_ADDR_WIDTH = 19       # 512 Ki words of 16 bit = 1 MiB
FLASH_WORDS = 1 << FLASH_ADDR_WIDTH
READ_CMD = 0x03


class SPIFlashPins(wiring.Signature):
    """SPI pins, from the controller's point of view."""
    def __init__(self):
        super().__init__({
            'cs_n': Out(1),     # chip select, active low
            'sclk': Out(1),
            'mosi': Out(1),     # flash SI
            'miso': In(1),      # flash SO
        })


class SPIFlashReader(wiring.Component):
    bus: In(MemoryPort(FLASH_ADDR_WIDTH))
    spi: Out(SPIFlashPins())

    def __init__(self, *, half_period=2, cs_high_cycles=8):
        super().__init__()
        assert half_period >= 2, 'need at least 2 cycles per SCLK half period'
        self.half_period = half_period
        self.cs_high_cycles = cs_high_cycles

    def elaborate(self, platform):
        m = Module()
        req, resp, spi = self.bus.req, self.bus.resp, self.spi

        cs_n = Signal(init=1)
        sclk = Signal()
        sh_out = Signal(32)
        sh_in = Signal(16)
        nbits = Signal(6)
        div = Signal(range(self.half_period))
        gap = Signal(range(self.cs_high_cycles + 1))
        next_addr = Signal(FLASH_ADDR_WIDTH)
        pending = Signal(FLASH_ADDR_WIDTH)
        tick = Signal()

        m.d.comb += [
            spi.cs_n.eq(cs_n),
            spi.sclk.eq(sclk),
            spi.mosi.eq(sh_out[31]),
            tick.eq(div == self.half_period - 1),
            # first byte received (even address) is the low byte of the word
            resp.data.eq(Cat(sh_in[8:16], sh_in[0:8])),
        ]
        m.d.sync += resp.valid.eq(0)

        def begin(addr):
            """Select the chip and start a fresh READ at word address `addr`."""
            return [
                # command, 24-bit byte address = {0000, word address, 0}
                sh_out.eq(Cat(Const(0, 1), addr, Const(0, 4),
                              Const(READ_CMD, 8))),
                nbits.eq(48),
                next_addr.eq(addr + 1),
                div.eq(0),
                sclk.eq(0),
                cs_n.eq(0),
            ]

        with m.FSM():
            with m.State('IDLE'):
                # Chip deselected. Writes are accepted and dropped.
                m.d.comb += req.ready.eq(1)
                with m.If(req.valid & ~req.we):
                    m.d.sync += begin(req.addr)
                    m.next = 'XFER'

            with m.State('OPEN'):
                # Chip still selected, SCLK idle low: a sequential read can
                # continue without a new command.
                m.d.comb += req.ready.eq(1)
                with m.If(req.valid & ~req.we):
                    with m.If(req.addr == next_addr):
                        m.d.sync += [
                            sh_out.eq(0),
                            nbits.eq(16),
                            next_addr.eq(next_addr + 1),
                            div.eq(0),
                        ]
                        m.next = 'XFER'
                    with m.Else():
                        m.d.sync += [
                            pending.eq(req.addr),
                            cs_n.eq(1),
                            gap.eq(self.cs_high_cycles),
                        ]
                        m.next = 'GAP'

            with m.State('GAP'):
                # Keep CS# high for a minimum time, then start the new READ.
                with m.If(gap == 0):
                    m.d.sync += begin(pending)
                    m.next = 'XFER'
                with m.Else():
                    m.d.sync += gap.eq(gap - 1)

            with m.State('XFER'):
                # SPI mode 0: MOSI changes on the falling edge, MISO is
                # sampled on the rising edge.
                with m.If(tick):
                    m.d.sync += [div.eq(0), sclk.eq(~sclk)]
                    with m.If(~sclk):               # rising edge
                        m.d.sync += sh_in.eq(Cat(spi.miso, sh_in[:15]))
                    with m.Else():                  # falling edge
                        m.d.sync += [
                            sh_out.eq(sh_out << 1),
                            nbits.eq(nbits - 1),
                        ]
                        with m.If(nbits == 1):
                            m.d.sync += resp.valid.eq(1)
                            m.next = 'OPEN'
                with m.Else():
                    m.d.sync += div.eq(div + 1)

        return m
