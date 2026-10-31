from amaranth.sim import Simulator

from uart import REG_DATA, REG_STATUS, UART, UARTCore


def test_sim_core():
    """The original example's testbench, against UARTCore."""
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


def test_sim_bus():
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
        assert ctx.get(uart.rx_avail) == 1      # the interrupt line is up
        assert await bus_read(ctx, REG_DATA) == 0x5A
        assert not (await bus_read(ctx, REG_STATUS) & 0b0010)   # consumed
        assert ctx.get(uart.rx_avail) == 0      # ... and down

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
    test_sim_core()
    test_sim_bus()
