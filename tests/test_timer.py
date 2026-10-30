"""
Test for timer.py: the registers, byte enables, the clearing of the flag, and
a random run of reads, writes and clocks against a model of the timer that
does the same each clock.

Run with `python test_timer.py` (or pytest).
"""
import random

from amaranth.sim import Simulator

from timer import (
    REG_COMPARE, REG_CONTROL, REG_COUNT, REG_PRESCALE, REG_STATUS, Timer,
)


class Model:
    def __init__(self):
        self.count = self.compare = self.prescale = self.divider = 0
        self.run = self.reload = self.match = 0

    def read(self, addr):
        return [
            self.count,
            self.compare,
            self.run | self.reload << 1,
            self.match,
            self.prescale,
            0,
            0,
            0,
        ][addr]

    def step(self, op=None):
        """One clock; `op` is (addr, we, wdata, byte_hi, byte_lo) or None."""
        write = op is not None and op[1]
        addr, _, wdata, hi, lo = op if op else (0, 0, 0, 0, 0)
        old = self.__dict__.copy()

        def merged(value):
            if lo:
                value = value & 0xff00 | wdata & 0x00ff
            if hi:
                value = value & 0x00ff | wdata & 0xff00
            return value

        # the clearing comes first, so that a match wins over it
        if write and addr == REG_STATUS and lo and wdata & 1:
            self.match = 0
        if self.run:
            if self.divider == 0:
                self.divider = self.prescale
                if self.count == self.compare:
                    self.match = 1
                    self.count = 0 if self.reload else self.count + 1 & 0xffff
                else:
                    self.count = (self.count + 1) & 0xffff
            else:
                self.divider -= 1
        # the writes win over the counting
        if write:
            if addr == REG_COUNT:
                self.count = merged(old['count'])
                self.divider = old['prescale']
            elif addr == REG_COMPARE:
                self.compare = merged(old['compare'])
            elif addr == REG_CONTROL and lo:
                self.run = wdata & 1
                self.reload = wdata >> 1 & 1
            elif addr == REG_PRESCALE:
                self.prescale = merged(old['prescale'])
                self.divider = self.prescale


def test_timer():
    dut = Timer()
    req, resp = dut.bus.req, dut.bus.resp
    sim = Simulator(dut)
    sim.add_clock(1e-6)

    async def clock(ctx, model, op):
        """One clock with a request (or none); check against the model."""
        if op is None:
            ctx.set(req.valid, 0)
        else:
            addr, we, wdata, hi, lo = op
            ctx.set(req.valid, 1)
            ctx.set(req.addr, addr)
            ctx.set(req.we, we)
            ctx.set(req.wdata, wdata)
            ctx.set(req.byte_hi, hi)
            ctx.set(req.byte_lo, lo)
        expected = None
        if op is not None and not op[1]:
            expected = model.read(op[0])
        model.step(op)
        await ctx.tick()
        if expected is None:
            assert ctx.get(resp.valid) == 0
        else:
            assert ctx.get(resp.valid) == 1
            assert ctx.get(resp.data) == expected, (op, ctx.get(resp.data))
        assert ctx.get(dut.irq) == model.match, op

    async def bench(ctx):
        model = Model()
        # all zero after the reset, and nothing counts
        for addr in range(8):
            await clock(ctx, model, (addr, 0, 0, 1, 1))
            assert ctx.get(resp.data) == 0
        for _ in range(5):
            await clock(ctx, model, None)
        assert ctx.get(dut.irq) == 0

        # a simple run: a match at COMPARE, the flag stays until cleared
        await clock(ctx, model, (REG_COMPARE, 1, 5, 1, 1))
        await clock(ctx, model, (REG_CONTROL, 1, 1, 1, 1))
        for _ in range(20):
            await clock(ctx, model, None)
        assert ctx.get(dut.irq) == 1
        await clock(ctx, model, (REG_STATUS, 1, 0, 1, 1))
        assert ctx.get(dut.irq) == 1
        await clock(ctx, model, (REG_STATUS, 1, 0x0100, 1, 0))
        assert ctx.get(dut.irq) == 1
        await clock(ctx, model, (REG_CONTROL, 1, 0, 1, 1))
        await clock(ctx, model, (REG_STATUS, 1, 1, 1, 1))
        assert ctx.get(dut.irq) == 0
        # byte enables of a register
        await clock(ctx, model, (REG_PRESCALE, 1, 0x1234, 1, 1))
        await clock(ctx, model, (REG_PRESCALE, 1, 0xff00, 0, 0))
        await clock(ctx, model, (REG_PRESCALE, 1, 0xab00, 1, 0))
        await clock(ctx, model, (REG_PRESCALE, 0, 0, 1, 1))
        assert ctx.get(resp.data) == 0xab34
        await clock(ctx, model, (REG_PRESCALE, 1, 0, 1, 1))

        # random: small values, so that the matches and the reloads happen
        rng = random.Random(7)
        for _ in range(6000):
            if rng.random() < 0.3:
                addr = rng.randrange(8)
                we = rng.randrange(2)
                value = rng.randrange(4 if addr == REG_CONTROL else 12)
                if addr == REG_STATUS:
                    value = rng.randrange(2)
                if rng.random() < 0.2:
                    value |= rng.randrange(256) << 8
                op = (addr, we, value, rng.randrange(2), rng.randrange(2))
                if addr == REG_PRESCALE and we and value > 6:
                    op = (addr, we, 3, 1, 1)
                await clock(ctx, model, op)
            else:
                await clock(ctx, model, None)

    sim.add_testbench(bench)
    sim.run()
    print('ok')


if __name__ == '__main__':
    test_timer()
