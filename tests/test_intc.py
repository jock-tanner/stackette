"""
Test for intc.py: the registers, the mask, the priority of the lines.

Run with `python test_intc.py` (or pytest).
"""
from amaranth.sim import Simulator

from intc import N_LINES, REG_ACTIVE, REG_MASK, REG_RAW, InterruptController


def test_intc():
    dut = InterruptController()
    req, resp = dut.bus.req, dut.bus.resp
    sim = Simulator(dut)
    sim.add_clock(1e-6)

    async def access(ctx, addr, we=0, wdata=0, byte_hi=1, byte_lo=1):
        ctx.set(req.addr, addr)
        ctx.set(req.we, we)
        ctx.set(req.wdata, wdata)
        ctx.set(req.byte_hi, byte_hi)
        ctx.set(req.byte_lo, byte_lo)
        ctx.set(req.valid, 1)
        await ctx.tick()
        ctx.set(req.valid, 0)
        if not we:
            assert ctx.get(resp.valid) == 1
            return ctx.get(resp.data)

    async def bench(ctx):
        # all the lines are masked after the reset
        ctx.set(dut.lines, 0b10110100)
        await ctx.tick()
        assert ctx.get(dut.irq) == 0
        assert await access(ctx, REG_RAW) == 0b10110100
        assert await access(ctx, REG_MASK) == 0
        assert await access(ctx, REG_ACTIVE) == 0
        # a response only to a read
        ctx.set(req.valid, 1)
        ctx.set(req.we, 1)
        await ctx.tick()
        ctx.set(req.valid, 0)
        await ctx.tick()
        assert ctx.get(resp.valid) == 0

        await access(ctx, REG_MASK, we=1, wdata=0b00110000)
        assert await access(ctx, REG_MASK) == 0b00110000
        assert await access(ctx, REG_ACTIVE) == 0b00110000
        assert ctx.get(dut.irq) == 1
        assert ctx.get(dut.irq_num) == 4        # the lowest line wins
        # the line goes away: no interrupt, whatever the mask
        ctx.set(dut.lines, 0b10000100)
        await ctx.tick()
        assert ctx.get(dut.irq) == 0
        ctx.set(dut.lines, 0b10100100)
        await ctx.tick()
        assert ctx.get(dut.irq) == 1 and ctx.get(dut.irq_num) == 5
        # only the low byte holds the mask, and byte enables are honored
        await access(ctx, REG_MASK, we=1, wdata=0xff00, byte_hi=1, byte_lo=0)
        assert await access(ctx, REG_MASK) == 0b00110000
        await access(ctx, REG_MASK, we=1, wdata=0xffff, byte_hi=0, byte_lo=1)
        assert await access(ctx, REG_MASK) == 0xff
        assert ctx.get(dut.irq_num) == 2
        # the reserved register and the read only ones
        await access(ctx, REG_RAW, we=1, wdata=0)
        await access(ctx, REG_ACTIVE, we=1, wdata=0)
        assert await access(ctx, REG_RAW) == 0b10100100
        assert await access(ctx, 3) == 0
        # all the lines
        for n in range(N_LINES):
            ctx.set(dut.lines, 1 << n)
            await ctx.tick()
            assert ctx.get(dut.irq) == 1 and ctx.get(dut.irq_num) == n

    sim.add_testbench(bench)
    sim.run()
    print('ok')


if __name__ == '__main__':
    test_intc()
