"""
UART to SPI bridge, for programming the SPI flash in place.

The FPGA only moves bytes: it drives the chip select and shifts bytes out and
in. All flash knowledge (write enable, erase, page program, status polling)
lives in the host tool, flashtool.py. Load this design instead of the CPU
design (see de0nano_flash_programmer.py), run the tool, then load the CPU
design again.

Protocol, 8N1 serial. Every command is answered, so the host always knows
where it is:

  00        CS# high (deselect)                      reply 00
  01        CS# low (select)                         reply 01
  02 n d..  n data bytes (n = 0 means 256): each is shifted out on MOSI
            and the byte shifted in at the same time is sent back      reply n bytes
  other     unknown command, ignored                 reply ff

The reply to each data byte is sent as soon as that byte is done, so a long
transfer streams. The transmitter starts each frame one clock after the
previous one, the host's bytes come exactly every 10 bit times, so the replies
fall behind by a clock per byte. One byte of buffering absorbs this for the 256
bytes of one transfer as long as a bit is at least 14 clocks (checked with a
model of the UART and this state machine), i.e. up to about 3.5 Mbaud at 50 MHz.

Resynchronization: if the host stops in the middle of a command, the bridge
goes back to waiting for a command after about 20 ms without a byte. A host
that waits 50 ms and clears its input buffer can always start over.

SPI mode 0, MSB first. SCLK = clock / (2 * half_period); the default gives
12.5 MHz from 50 MHz, the same as the flash reader.
"""
from amaranth import Cat, Module, Mux, Signal
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.wiring import Out

from spi_flash import SPIFlashPins
from uart import UARTCore, UARTPins

CMD_CS_HIGH = 0x00
CMD_CS_LOW = 0x01
CMD_XFER = 0x02
REPLY_UNKNOWN = 0xff
IDLE_TIMEOUT = 1 << 20


class SPIBridge(wiring.Component):
    serial: Out(UARTPins())
    spi: Out(SPIFlashPins())
    # high while a command is being worked on, for an LED
    busy: Out(1)

    def __init__(self, *, divisor, half_period=2, idle_timeout=IDLE_TIMEOUT):
        assert half_period >= 2
        super().__init__()
        self.idle_timeout = idle_timeout
        self.divisor = divisor
        self.half_period = half_period

    def elaborate(self, platform):
        m = Module()
        spi = self.spi

        m.submodules.core = core = UARTCore(self.divisor)
        m.submodules.rx_sync = FFSynchronizer(self.serial.rx, core.rx_i, init=1)
        m.d.comb += self.serial.tx.eq(core.tx_o)

        cs_n = Signal(init=1)
        sclk = Signal()
        sh_out = Signal(8)
        sh_in = Signal(8)
        nbits = Signal(4)
        div = Signal(range(self.half_period))
        count = Signal(9)
        result = Signal(8)
        reply = Signal(8)
        reply_valid = Signal()
        idle = Signal(range(self.idle_timeout))
        tick = Signal()

        m.d.comb += [
            spi.cs_n.eq(cs_n),
            spi.sclk.eq(sclk),
            spi.mosi.eq(sh_out[7]),
            tick.eq(div == self.half_period - 1),
            core.tx_data.eq(reply),
            core.tx_rdy.eq(reply_valid),
        ]
        # The reply byte waits in `reply` until the transmitter is idle, so
        # the next byte can be received meanwhile. (Waiting for the
        # transmitter in the main state machine loses received bytes: the
        # transmitter starts each frame a cycle late, the host does not.)
        # The state machine only sets reply_valid when it is clear.
        with m.If(reply_valid & core.tx_ack):
            m.d.sync += reply_valid.eq(0)

        def watchdog():
            with m.If(idle == self.idle_timeout - 1):
                m.d.sync += idle.eq(0)
                m.next = 'CMD'
            with m.Else():
                m.d.sync += idle.eq(idle + 1)

        with m.FSM():
            with m.State('CMD'):
                m.d.sync += [idle.eq(0), count.eq(0)]
                with m.If(core.rx_rdy):
                    m.d.comb += core.rx_ack.eq(1)
                    with m.Switch(core.rx_data):
                        with m.Case(CMD_CS_HIGH):
                            m.d.sync += [cs_n.eq(1), result.eq(CMD_CS_HIGH)]
                            m.next = 'QUEUE'
                        with m.Case(CMD_CS_LOW):
                            m.d.sync += [cs_n.eq(0), result.eq(CMD_CS_LOW)]
                            m.next = 'QUEUE'
                        with m.Case(CMD_XFER):
                            m.next = 'LEN'
                        with m.Default():
                            m.d.sync += result.eq(REPLY_UNKNOWN)
                            m.next = 'QUEUE'

            with m.State('LEN'):
                m.d.comb += self.busy.eq(1)
                with m.If(core.rx_rdy):
                    m.d.comb += core.rx_ack.eq(1)
                    m.d.sync += [
                        count.eq(Mux(core.rx_data == 0, 256, core.rx_data)),
                        idle.eq(0),
                    ]
                    m.next = 'DATA'
                with m.Else():
                    watchdog()

            with m.State('DATA'):
                m.d.comb += self.busy.eq(1)
                with m.If(core.rx_rdy):
                    m.d.comb += core.rx_ack.eq(1)
                    m.d.sync += [
                        sh_out.eq(core.rx_data),
                        nbits.eq(8),
                        div.eq(0),
                        idle.eq(0),
                    ]
                    m.next = 'XFER'
                with m.Else():
                    watchdog()

            with m.State('XFER'):
                # SPI mode 0: MOSI changes on the falling edge, MISO is
                # sampled on the rising edge. SCLK is low again at the end.
                m.d.comb += self.busy.eq(1)
                with m.If(tick):
                    m.d.sync += [div.eq(0), sclk.eq(~sclk)]
                    with m.If(~sclk):
                        m.d.sync += sh_in.eq(Cat(spi.miso, sh_in[:7]))
                    with m.Else():
                        m.d.sync += [
                            sh_out.eq(sh_out << 1),
                            nbits.eq(nbits - 1),
                        ]
                        with m.If(nbits == 1):
                            m.d.sync += [
                                result.eq(sh_in),
                                count.eq(count - 1),
                            ]
                            m.next = 'QUEUE'
                with m.Else():
                    m.d.sync += div.eq(div + 1)

            with m.State('QUEUE'):
                # Hand the reply byte to the transmitter as soon as the
                # previous one has left the holding register.
                m.d.comb += self.busy.eq(1)
                with m.If(~reply_valid):
                    m.d.sync += [reply.eq(result), reply_valid.eq(1)]
                    with m.If(count == 0):
                        m.next = 'CMD'
                    with m.Else():
                        m.next = 'DATA'

        return m
