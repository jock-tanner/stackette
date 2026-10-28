"""
Simulation test of spi_bridge.py: the serial side is driven bit by bit, the SPI
side is a small slave that answers every byte with 0xa5 first and then the
complement of the byte it received before. Run: python test_spi_bridge.py
"""
from amaranth.sim import Simulator

from spi_bridge import SPIBridge

# short enough that the one byte of reply buffering is needed for 256 bytes
DIVISOR = 20
IDLE_TIMEOUT = 3000


def test_bridge():
    dut = SPIBridge(divisor=DIVISOR, idle_timeout=IDLE_TIMEOUT)
    sim = Simulator(dut)
    sim.add_clock(1e-6)

    replies = []
    mosi_bytes = []
    selects = []

    async def serial_rx(ctx):
        # collect the bytes the bridge sends
        while True:
            while ctx.get(dut.serial.tx) == 1:
                await ctx.tick()
            await ctx.tick().repeat(DIVISOR + DIVISOR // 2)
            value = 0
            for i in range(8):
                value |= ctx.get(dut.serial.tx) << i
                await ctx.tick().repeat(DIVISOR)
            assert ctx.get(dut.serial.tx) == 1, 'stop bit'
            replies.append(value)

    async def spi_slave(ctx):
        out = 0xa5
        shifted = 0
        nbit = 0
        prev_cs = 1
        prev_sclk = 0
        while True:
            await ctx.tick()
            cs_n = ctx.get(dut.spi.cs_n)
            sclk = ctx.get(dut.spi.sclk)
            if prev_cs and not cs_n:
                out, nbit = 0xa5, 0
                ctx.set(dut.spi.miso, out >> 7 & 1)
                selects.append(1)
            if prev_cs != cs_n and cs_n:
                selects.append(0)
            if not cs_n:
                if sclk and not prev_sclk:
                    shifted = (shifted << 1 | ctx.get(dut.spi.mosi)) & 0xff
                    nbit += 1
                    if nbit == 8:
                        mosi_bytes.append(shifted)
                        out = shifted ^ 0xff
                        nbit = 0
                if prev_sclk and not sclk:
                    ctx.set(dut.spi.miso, out >> (7 - nbit) & 1)
            prev_cs, prev_sclk = cs_n, sclk

    async def send(ctx, data):
        for b in data:
            for bit in [0] + [b >> i & 1 for i in range(8)] + [1]:
                ctx.set(dut.serial.rx, bit)
                await ctx.tick().repeat(DIVISOR)

    async def idle(ctx, ticks):
        await ctx.tick().repeat(ticks)

    async def bench(ctx):
        ctx.set(dut.serial.rx, 1)
        await idle(ctx, 20)

        # select, a three byte transfer, deselect, an unknown command
        await send(ctx, [0x01, 0x02, 3, 0x11, 0x22, 0x33, 0x00, 0x7f])
        await idle(ctx, DIVISOR * 40)
        assert replies == [0x01, 0xa5, 0xee, 0xdd, 0x00, 0xff], replies
        assert mosi_bytes == [0x11, 0x22, 0x33], mosi_bytes
        assert selects == [1, 0], selects
        assert ctx.get(dut.spi.cs_n) == 1

        # length 0 means 256 bytes
        replies.clear()
        mosi_bytes.clear()
        data = [(i * 7 + 1) & 0xff for i in range(256)]
        await send(ctx, [0x01, 0x02, 0] + data + [0x00])
        await idle(ctx, DIVISOR * 40)
        assert mosi_bytes == data
        assert len(replies) == 258, len(replies)
        assert replies[1] == 0xa5 and replies[2:257] == [b ^ 0xff for b in data[:255]]

        # a command that stops in the middle is dropped after the timeout
        replies.clear()
        await send(ctx, [0x02, 5, 0x44])
        await idle(ctx, IDLE_TIMEOUT + 200)
        replies.clear()
        await send(ctx, [0x00])
        await idle(ctx, DIVISOR * 40)
        assert replies == [0x00], replies
        print('spi bridge test passed')

    sim.add_testbench(serial_rx, background=True)
    sim.add_testbench(spi_slave, background=True)
    sim.add_testbench(bench)
    sim.run()


if __name__ == '__main__':
    test_bridge()
