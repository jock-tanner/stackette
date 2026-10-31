"""
UART with a MemoryPort register interface.

The serial engine (UARTCore) is the original Amaranth UART example, unchanged.
UART wraps it in a small register file so that the CPU, a cache or a test
initiator can talk to it exactly like to the SDRAM or the flash.

Registers (word addresses; the port is 2 address bits x 16 data bits):

  0  DATA    write: send the low `data_bits` bits as one frame.
                    Stalls (req.ready low) while the transmitter is busy, so
                    software never loses a byte and never has to poll TX.
             read:  return the received byte, zero-extended, and consume it.
                    Reading while RX available = 0 returns stale data and
                    consumes nothing: check STATUS first.
  1  STATUS  read only (a write is accepted and ignored)
               bit 0  TX ready   the transmitter is idle
               bit 1  RX avail   a received byte is waiting in DATA
               bit 2  RX error   framing error in the waiting byte
                                 (only meaningful while RX avail = 1)
               bit 3  RX overrun a frame started while the previous byte was
                                 still unread; that frame is lost. Stays set
                                 until the next frame starts after a read.
  2,3 reserved: read as 0, writes ignored.

Port behavior (see memory_port.py):
  * req.ready is high except for a DATA write while the transmitter is busy.
    It depends combinationally on req.addr / req.we, so an initiator must not
    make req.valid depend on req.ready (the usual valid/ready rule).
  * A read produces one resp.valid pulse on the cycle after it was accepted;
    the data was captured on the accept edge. Writes produce no response.
  * Byte enables are ignored: the registers are always accessed as words.

Pins (`pins`, a UARTPins signature): tx is driven, rx is sampled. rx is not
synchronized here; run it through a 2-FF synchronizer first if it comes from
off-chip.

divisor = round(clk-rate / baud-rate), e.g. 50e6 / 115200 = 434.
"""
from amaranth import *
from amaranth.lib.wiring import Component, In, Out, Signature

from memory_port import MemoryPort

UART_ADDR_WIDTH = 2
REG_DATA = 0
REG_STATUS = 1


class UARTPins(Signature):
    """UART pins, from the UART's point of view."""
    def __init__(self):
        super().__init__({
            'tx': Out(1),
            'rx': In(1),
        })


class UARTCore(Elaboratable):
    """
    Serial engine. Same logic as the original example UART.

    Parameters
    ----------
    divisor : int
        Set to ``round(clk-rate / baud-rate)``.
        E.g. ``12e6 / 115200`` = ``104``.
    """
    def __init__(self, divisor, data_bits=8):
        assert divisor >= 4

        self.data_bits = data_bits
        self.divisor = divisor

        self.tx_o = Signal()
        self.rx_i = Signal()

        self.tx_data = Signal(data_bits)
        self.tx_rdy = Signal()
        self.tx_ack = Signal()

        self.rx_data = Signal(data_bits)
        self.rx_err = Signal()
        self.rx_ovf = Signal()
        self.rx_rdy = Signal()
        self.rx_ack = Signal()

    def elaborate(self, platform):
        m = Module()

        tx_phase = Signal(range(self.divisor))
        tx_shreg = Signal(1 + self.data_bits + 1, init=-1)
        tx_count = Signal(range(len(tx_shreg) + 1))

        m.d.comb += self.tx_o.eq(tx_shreg[0])
        with m.If(tx_count == 0):
            m.d.comb += self.tx_ack.eq(1)
            with m.If(self.tx_rdy):
                m.d.sync += [
                    tx_shreg.eq(Cat(C(0, 1), self.tx_data, C(1, 1))),
                    tx_count.eq(len(tx_shreg)),
                    tx_phase.eq(self.divisor - 1),
                ]
        with m.Else():
            with m.If(tx_phase != 0):
                m.d.sync += tx_phase.eq(tx_phase - 1)
            with m.Else():
                m.d.sync += [
                    tx_shreg.eq(Cat(tx_shreg[1:], C(1, 1))),
                    tx_count.eq(tx_count - 1),
                    tx_phase.eq(self.divisor - 1),
                ]

        rx_phase = Signal(range(self.divisor))
        rx_shreg = Signal(1 + self.data_bits + 1, init=-1)
        rx_count = Signal(range(len(rx_shreg) + 1))

        m.d.comb += self.rx_data.eq(rx_shreg[1:-1])
        with m.If(rx_count == 0):
            m.d.comb += self.rx_err.eq(~(~rx_shreg[0] & rx_shreg[-1]))
            with m.If(~self.rx_i):
                with m.If(self.rx_ack | ~self.rx_rdy):
                    m.d.sync += [
                        self.rx_rdy.eq(0),
                        self.rx_ovf.eq(0),
                        rx_count.eq(len(rx_shreg)),
                        rx_phase.eq(self.divisor // 2),
                    ]
                with m.Else():
                    m.d.sync += self.rx_ovf.eq(1)
            with m.If(self.rx_ack):
                m.d.sync += self.rx_rdy.eq(0)
        with m.Else():
            with m.If(rx_phase != 0):
                m.d.sync += rx_phase.eq(rx_phase - 1)
            with m.Else():
                m.d.sync += [
                    rx_shreg.eq(Cat(rx_shreg[1:], self.rx_i)),
                    rx_count.eq(rx_count - 1),
                    rx_phase.eq(self.divisor - 1),
                ]
                with m.If(rx_count == 1):
                    m.d.sync += self.rx_rdy.eq(1)

        return m


class UART(Component):
    """Register-file front end for UARTCore, see the module docstring."""
    bus: In(MemoryPort(UART_ADDR_WIDTH))
    pins: Out(UARTPins())
    rx_avail: Out(1)  # a level for an interrupt line: STATUS bit 1

    def __init__(self, divisor, data_bits=8):
        assert data_bits <= 16, 'Registers are 16 bits wide!'
        super().__init__()
        self.divisor = divisor
        self.data_bits = data_bits

    def elaborate(self, platform):
        m = Module()
        req, resp = self.bus.req, self.bus.resp

        m.submodules.core = core = UARTCore(self.divisor, self.data_bits)
        m.d.comb += [
            core.rx_i.eq(self.pins.rx),
            self.pins.tx.eq(core.tx_o),
        ]

        is_data = req.addr == REG_DATA
        is_status = req.addr == REG_STATUS
        write_data = req.we & is_data
        read_data = ~req.we & is_data

        # Only a DATA write can stall, and only while the transmitter is busy.
        accept = Signal()
        m.d.comb += [
            req.ready.eq(~write_data | core.tx_ack),
            accept.eq(req.valid & req.ready),
        ]

        # Transmit: pulse tx_rdy on the accept edge (tx_ack is high then,
        # because ready was only granted when the transmitter was idle).
        m.d.comb += [
            core.tx_data.eq(req.wdata[:self.data_bits]),
            core.tx_rdy.eq(accept & write_data),
        ]

        # Receive: reading DATA while a byte is waiting consumes it.
        m.d.comb += core.rx_ack.eq(accept & read_data & core.rx_rdy)

        m.d.comb += self.rx_avail.eq(core.rx_rdy)

        status = Cat(
            core.tx_ack,                    # bit 0: TX ready
            core.rx_rdy,                    # bit 1: RX available
            core.rx_err & core.rx_rdy,      # bit 2: framing error (see note)
            core.rx_ovf,                    # bit 3: overrun
        )
        # rx_err is computed from the idle shift register, so it reads 1 while
        # nothing has been received; mask it with rx_rdy.

        # Read response: data is captured on the accept edge, before rx_ack
        # takes effect.
        with m.If(accept & ~req.we):
            m.d.sync += resp.valid.eq(1)
            with m.If(is_data):
                m.d.sync += resp.data.eq(core.rx_data)
            with m.Elif(is_status):
                m.d.sync += resp.data.eq(status)
            with m.Else():
                m.d.sync += resp.data.eq(0)
        with m.Else():
            m.d.sync += resp.valid.eq(0)

        return m
