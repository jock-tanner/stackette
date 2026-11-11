"""
Test for spi_master.py: the reset values, byte and word transfers (the order of
the bytes on the wire), the stalls, the SCLK period for several dividers, the
CS_N times of the chip, the INT pin, and the protocol of the ENC28J60 against
its model (enc28j60_model.py): a register read, and the buffer written and read
back in words.

Run with `python test_spi_master.py` (or pytest).
"""
from amaranth.sim import Simulator

from enc28j60_model import ENC28J60
from spi_master import (
    REG_CONTROL, REG_DATA, REG_DIVIDER, REG_STATUS, SPIMaster, T_DISABLE,
    T_HOLD, T_SETUP,
)

CLK_NS = 20


def run_test(body, dev=None, loopback=False):
    dut = SPIMaster()
    sim = Simulator(dut)
    sim.add_clock(CLK_NS * 1e-9)
    log = []        # (cycle, cs_n, sclk, mosi) at every change of the pins
    cycle = [0]

    async def slave(ctx):
        prev = (1, 0, 0)
        while True:
            await ctx.tick()
            cycle[0] += 1
            cur = (
                ctx.get(dut.pins.cs_n), ctx.get(dut.pins.sclk),
                ctx.get(dut.pins.mosi),
            )
            if cur != prev:
                log.append((cycle[0],) + cur)
            if dev:
                dev.pins(*cur)
                ctx.set(dut.pins.miso, dev.miso)
            elif loopback:
                ctx.set(dut.pins.miso, cur[2])
            prev = cur

    async def main(ctx):
        # INT is active low, the idle level of the chip is high
        ctx.set(dut.pins.int, 1)
        await body(ctx, dut, log)

    sim.add_testbench(slave, background=True)
    sim.add_testbench(main)
    sim.run()


async def access(ctx, dut, addr, we=0, wdata=0, hi=1, lo=1):
    """One access; -> (data, stalls) of a read, stalls of a write."""
    req, resp = dut.bus.req, dut.bus.resp
    ctx.set(req.valid, 1)
    ctx.set(req.addr, addr)
    ctx.set(req.we, we)
    ctx.set(req.wdata, wdata)
    ctx.set(req.byte_hi, hi)
    ctx.set(req.byte_lo, lo)
    stalls = 0
    while not ctx.get(req.ready):
        await ctx.tick()
        stalls += 1
        assert stalls < 1000, 'stuck'
    await ctx.tick()
    ctx.set(req.valid, 0)
    if we:
        assert ctx.get(resp.valid) == 0
        return stalls
    assert ctx.get(resp.valid) == 1
    return ctx.get(resp.data), stalls


async def send_byte(ctx, dut, b):
    """A transfer of a byte: the store, then the load of the answer."""
    await access(ctx, dut, REG_DATA, 1, b, hi=0, lo=1)
    return (await access(ctx, dut, REG_DATA))[0]


async def send_word(ctx, dut, w):
    await access(ctx, dut, REG_DATA, 1, w)
    return (await access(ctx, dut, REG_DATA))[0]


async def select(ctx, dut, on):
    await access(ctx, dut, REG_CONTROL, 1, on)


async def idle(ctx, n):
    for _ in range(n):
        await ctx.tick()


def wire_bytes(log):
    """The bytes that MOSI carried at the rising edges of SCLK."""
    bits = []
    prev_sclk = 0
    for _, _, s, m in log:
        if s and not prev_sclk:
            bits.append(m)
        prev_sclk = s
    assert len(bits) % 8 == 0, bits
    return [
        int(''.join(map(str, bits[i:i + 8])), 2)
        for i in range(0, len(bits), 8)
    ]


def rising_edges(log):
    prev = 0
    out = []
    for cyc, _, s, _ in log:
        if s and not prev:
            out.append(cyc)
        prev = s
    return out


def test_reset_values():
    async def body(ctx, dut, log):
        assert ctx.get(dut.pins.cs_n) == 1
        assert ctx.get(dut.pins.sclk) == 0
        assert (await access(ctx, dut, REG_CONTROL))[0] == 0
        assert (await access(ctx, dut, REG_DIVIDER))[0] == 1
        assert (await access(ctx, dut, REG_STATUS))[0] == 0
        assert (await access(ctx, dut, REG_DATA))[0] == 0
    run_test(body)


def test_byte_and_word_loopback():
    async def body(ctx, dut, log):
        await select(ctx, dut, 1)
        assert await send_byte(ctx, dut, 0xa5) == 0xa5
        assert await send_byte(ctx, dut, 0x00) == 0x00
        assert await send_word(ctx, dut, 0x1234) == 0x1234
        assert await send_word(ctx, dut, 0xfedc) == 0xfedc
        # a store with the high byte enable only does nothing
        await access(ctx, dut, REG_DATA, 1, 0x5500, hi=1, lo=0)
        assert (await access(ctx, dut, REG_STATUS))[0] & 1 == 0
        await select(ctx, dut, 0)
        await idle(ctx, 40)
        # the order on the wire: a word goes out low byte first
        assert wire_bytes(log) == [0xa5, 0x00, 0x34, 0x12, 0xdc, 0xfe]
    run_test(body, loopback=True)


def test_stalls():
    async def body(ctx, dut, log):
        await select(ctx, dut, 1)
        stalls = await access(ctx, dut, REG_DATA, 1, 0x55, hi=0, lo=1)
        # the first store waits for the setup time of CS_N, not for a transfer
        assert stalls <= 8, stalls
        # the next store waits for the transfer of 8 bits, 4 clocks each
        stalls = await access(ctx, dut, REG_DATA, 1, 0x66, hi=0, lo=1)
        assert 24 <= stalls <= 36, stalls
        # a load of DATA waits too
        value, stalls = await access(ctx, dut, REG_DATA)
        assert 24 <= stalls <= 36, stalls
        assert value == 0x66
        assert (await access(ctx, dut, REG_STATUS))[0] & 1 == 0
    run_test(body, loopback=True)


def test_sclk_period():
    async def body(ctx, dut, log):
        for div, period in ((1, 4), (3, 8), (0, 4), (7, 16)):
            await access(ctx, dut, REG_DIVIDER, 1, div)
            assert (await access(ctx, dut, REG_DIVIDER))[0] == div
            del log[:]
            await select(ctx, dut, 1)
            assert await send_byte(ctx, dut, 0xc3) == 0xc3
            await select(ctx, dut, 0)
            await idle(ctx, 60)
            edges = rising_edges(log)
            assert len(edges) == 8, edges
            assert [b - a for a, b in zip(edges, edges[1:])] == [period] * 7
            await idle(ctx, 40)
    run_test(body, loopback=True)


def test_cs_times():
    async def body(ctx, dut, log):
        await select(ctx, dut, 1)
        await send_byte(ctx, dut, 0x12)
        await select(ctx, dut, 0)
        await idle(ctx, 60)
        await select(ctx, dut, 1)
        await send_byte(ctx, dut, 0x34)
        await select(ctx, dut, 0)
        await idle(ctx, 60)
        cs_edges = []
        prev = 1
        for cyc, c, _, _ in log:
            if c != prev:
                cs_edges.append((cyc, c))
            prev = c
        assert [v for _, v in cs_edges] == [0, 1, 0, 1]
        sclk_falls = []
        prev = 0
        for cyc, _, s, _ in log:
            if prev and not s:
                sclk_falls.append(cyc)
            prev = s
        edges = rising_edges(log)
        # CS_N low to the first clock
        assert (edges[0] - cs_edges[0][0]) * CLK_NS >= T_SETUP
        # the last clock to CS_N high
        last_fall = max(f for f in sclk_falls if f < cs_edges[1][0])
        assert (cs_edges[1][0] - last_fall) * CLK_NS >= T_HOLD
        # CS_N high to the next CS_N low
        assert (cs_edges[2][0] - cs_edges[1][0]) * CLK_NS >= T_DISABLE
        # and the second transfer's clock follows the setup time again
        assert (edges[8] - cs_edges[2][0]) * CLK_NS >= T_SETUP
    run_test(body, loopback=True)


def test_back_to_back_commands():
    async def body(ctx, dut, log):
        await select(ctx, dut, 1)
        await send_byte(ctx, dut, 0x12)
        await select(ctx, dut, 0)
        await select(ctx, dut, 1)
        await send_byte(ctx, dut, 0x34)
        await select(ctx, dut, 0)
        await idle(ctx, 60)
        cs_edges = []
        prev = 1
        for cyc, c, _, _ in log:
            if c != prev:
                cs_edges.append((cyc, c))
            prev = c
        # two separate assertions of CS_N, never merged into one
        assert [v for _, v in cs_edges] == [0, 1, 0, 1]
        assert (cs_edges[2][0] - cs_edges[1][0]) * CLK_NS >= T_DISABLE
        assert wire_bytes(log) == [0x12, 0x34]
    run_test(body, loopback=True)


def test_int():
    async def body(ctx, dut, log):
        ctx.set(dut.pins.int, 1)
        await idle(ctx, 4)
        assert (await access(ctx, dut, REG_STATUS))[0] == 0
        assert ctx.get(dut.irq) == 0
        ctx.set(dut.pins.int, 0)
        await idle(ctx, 4)
        assert (await access(ctx, dut, REG_STATUS))[0] == 2
        assert ctx.get(dut.irq) == 1
        ctx.set(dut.pins.int, 1)
        await idle(ctx, 4)
        assert ctx.get(dut.irq) == 0
    run_test(body)


def test_enc28j60():
    dev = ENC28J60(revid=6)

    async def body(ctx, dut, log):
        # RCR EREVID (bank 3, 0x12), the bank first
        await select(ctx, dut, 1)
        await send_byte(ctx, dut, 0x9f)         # BFS ECON1
        await send_byte(ctx, dut, 0x03)
        await select(ctx, dut, 0)
        await select(ctx, dut, 1)
        await send_byte(ctx, dut, 0x12)         # RCR EREVID
        assert await send_byte(ctx, dut, 0) == 6
        await select(ctx, dut, 0)
        # bank 0 again, EWRPT = 0x0100, then WBM of 8 words
        await select(ctx, dut, 1)
        await send_byte(ctx, dut, 0xbf)         # BFC ECON1, 3
        await send_byte(ctx, dut, 0x03)
        await select(ctx, dut, 0)
        for cmd, value in ((0x42, 0x00), (0x43, 0x01), (0x40, 0x00),
                           (0x41, 0x01)):       # WCR EWRPT, ERDPT
            await select(ctx, dut, 1)
            await send_byte(ctx, dut, cmd)
            await send_byte(ctx, dut, value)
            await select(ctx, dut, 0)
        words = [0x1234, 0xabcd, 0x0f0f, 0x8001, 0, 0xffff, 0x7e7e, 0x00ff]
        await select(ctx, dut, 1)
        await send_byte(ctx, dut, 0x7a)         # WBM
        for w in words:
            await send_word(ctx, dut, w)
        await select(ctx, dut, 0)
        assert dev.buf[0x100:0x110] == b''.join(
            w.to_bytes(2, 'little') for w in words
        )
        await select(ctx, dut, 1)
        await send_byte(ctx, dut, 0x3a)         # RBM
        back = [await send_word(ctx, dut, 0) for _ in words]
        await select(ctx, dut, 0)
        assert back == words, back
    run_test(body, dev=dev)


if __name__ == '__main__':
    for name, f in list(globals().items()):
        if name.startswith('test_'):
            f()
            print(name, 'ok')
