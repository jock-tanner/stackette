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


def sim_core():
    """The original example's testbench, against UARTCore."""
    from amaranth.sim import Simulator

    uart = UARTCore(divisor=5)

    sim = Simulator(uart)
    sim.add_clock(1e-6)

    async def testbench_loopback(ctx):
        async for val in ctx.changed(uart.tx_o):
            ctx.set(uart.rx_i, val[0])  # fixed an error in the example

    sim.add_testbench(testbench_loopback, background=True)

    async def testbench_transmit(ctx):
        assert ctx.get(uart.tx_ack)
        assert not ctx.get(uart.rx_rdy)

        ctx.set(uart.tx_data, 0x5A)
        ctx.set(uart.tx_rdy, 1)
        await ctx.tick()
        ctx.set(uart.tx_rdy, 0)
        await ctx.tick()
        assert not ctx.get(uart.tx_ack)

        await ctx.tick().repeat(uart.divisor * 12)

        assert ctx.get(uart.tx_ack)
        assert ctx.get(uart.rx_rdy)
        assert not ctx.get(uart.rx_err)
        assert ctx.get(uart.rx_data) == 0x5A

        ctx.set(uart.rx_ack, 1)
        await ctx.tick()
        ctx.set(uart.rx_ack, 0)
        await ctx.tick()
        assert not ctx.get(uart.rx_rdy)

    sim.add_testbench(testbench_transmit)
    sim.run()
    print('Core test passed.')


def sim_bus():
    """Register interface, with the TX pin looped back to RX."""
    from amaranth.sim import Simulator

    uart = UART(divisor=5)
    req, resp = uart.bus.req, uart.bus.resp

    sim = Simulator(uart)
    sim.add_clock(1e-6)

    async def loopback(ctx):
        ctx.set(uart.pins.rx, 1)        # idle line, not the default 0
        async for val in ctx.changed(uart.pins.tx):
            ctx.set(uart.pins.rx, val[0])

    sim.add_testbench(loopback, background=True)

    # Note: `await ctx.tick().sample(a, b)` returns
    # (clock edge, reset value, a, b), hence the leading `_, _`.

    async def bus_write(ctx, addr, data):
        """Hold the request until accepted; return the number of stalled cycles."""
        ctx.set(req.addr, addr)
        ctx.set(req.we, 1)
        ctx.set(req.wdata, data)
        ctx.set(req.valid, 1)
        stalls = 0
        while True:
            _, _, ready = await ctx.tick().sample(req.ready)
            if ready:
                break
            stalls += 1
        ctx.set(req.valid, 0)
        ctx.set(req.we, 0)
        return stalls

    async def bus_read(ctx, addr):
        ctx.set(req.addr, addr)
        ctx.set(req.we, 0)
        ctx.set(req.valid, 1)
        while True:
            _, _, ready = await ctx.tick().sample(req.ready)
            if ready:
                break
        ctx.set(req.valid, 0)
        # one resp.valid pulse on the cycle after the accept edge
        _, _, valid, data = await ctx.tick().sample(resp.valid, resp.data)
        assert valid
        return data

    async def wait_status(ctx, mask, limit=100):
        for _ in range(limit):
            status = await bus_read(ctx, REG_STATUS)
            if status & mask:
                return status
        raise AssertionError(f'status never showed mask {mask:#x}')

    async def testbench(ctx):
        # reset state: transmitter idle, nothing received
        assert await bus_read(ctx, REG_STATUS) == 0b0001

        # send a byte; the transmitter is busy right afterward
        assert await bus_write(ctx, REG_DATA, 0x5A) == 0
        assert not (await bus_read(ctx, REG_STATUS) & 1)

        # the looped-back byte arrives, no error, no overrun
        status = await wait_status(ctx, 0b0010)
        assert (status >> 1) & 0b111 == 0b001, bin(status)
        assert await bus_read(ctx, REG_DATA) == 0x5A
        assert not (await bus_read(ctx, REG_STATUS) & 0b0010)   # consumed

        # a DATA write while the transmitter is busy stalls until it is idle
        await wait_status(ctx, 0b0001)
        assert await bus_write(ctx, REG_DATA, 0x11) == 0
        stalls = await bus_write(ctx, REG_DATA, 0x22)
        assert stalls > uart.divisor * 8, stalls

        # 0x11 was never read, so the second frame is an overrun and is lost
        await wait_status(ctx, 0b0001)
        status = await bus_read(ctx, REG_STATUS)
        assert status & 0b1010 == 0b1010, bin(status)
        assert await bus_read(ctx, REG_DATA) == 0x11

        # reserved registers read as 0 and ignore writes
        assert await bus_read(ctx, 2) == 0
        await bus_write(ctx, 3, 0xFFFF)

    sim.add_testbench(testbench)
    sim.run()
    print('Bus test passed.')


if __name__ == '__main__':
    sim_core()
    sim_bus()
