"""
Test for muldiv.py: multiplication and division through the common
start/done handshake, against a plain Python description.

Run with `python test_muldiv.py` (or pytest).
"""
import random

from amaranth.sim import Simulator


def signed_of(v, bits):
    return v - (1 << bits) if v >> (bits - 1) else v


def spec(a, b, div, size, signed):
    """-> primary, secondary, zero_div, overflow"""
    bits = 8 if size else 16
    mask = (1 << bits) - 1
    a &= mask
    b &= mask
    if signed:
        a, b = signed_of(a, bits), signed_of(b, bits)
    if div:
        if b == 0:
            return None, None, 1, 0
        q = abs(a) // abs(b)
        if (a < 0) != (b < 0):
            q = -q
        r = a - q * b
        return q & mask, r & mask, 0, int(bool(signed) and q == 1 << (bits - 1))
    p = a * b
    lo = p & mask
    hi = p >> bits & mask
    if signed:
        big = p != signed_of(lo, bits)
    else:
        big = hi != 0
    return lo, hi, 0, int(big)


async def operate(ctx, dut, a, b, div, size, signed):
    """Run one operation with the operands changing after the start."""
    assert ctx.get(dut.ready) == 1
    ctx.set(dut.a, a)
    ctx.set(dut.b, b)
    ctx.set(dut.div, div)
    ctx.set(dut.size, size)
    ctx.set(dut.signed, signed)
    ctx.set(dut.start, 1)
    await ctx.tick()
    ctx.set(dut.start, 0)
    ctx.set(dut.a, 0x5555)
    ctx.set(dut.b, 0xaaaa)
    ctx.set(dut.div, 1 - div)
    ctx.set(dut.size, 1 - size)
    ctx.set(dut.signed, 1 - signed)
    clocks = 0
    while not ctx.get(dut.done):
        assert ctx.get(dut.ready) == 0, 'ready while busy'
        await ctx.tick()
        clocks += 1
        assert clocks <= 8, 'no done'
    assert clocks == (1 if not div else 4 if size else 8), clocks
    assert ctx.get(dut.ready) == 1
    got = (
        ctx.get(dut.primary),
        ctx.get(dut.secondary),
        ctx.get(dut.zero_div),
        ctx.get(dut.overflow),
    )
    # done is one clock long, the results hold
    await ctx.tick()
    assert ctx.get(dut.done) == 0
    assert (
        ctx.get(dut.primary),
        ctx.get(dut.secondary),
        ctx.get(dut.zero_div),
        ctx.get(dut.overflow),
    ) == got
    return got


def run_bench(bench):
    from muldiv import MulDiv

    dut = MulDiv()
    sim = Simulator(dut)
    sim.add_clock(1e-6)

    async def testbench(ctx):
        await bench(ctx, dut)

    sim.add_testbench(testbench)
    sim.run()


def check(want, got, case):
    if want[2]:
        # the results of a division by zero are not used
        assert got[2] == 1, case
    else:
        assert got == want, (case, got, want)


def test_muldiv_edges():
    values = [0, 1, 2, 3, 7, 0x7f, 0x80, 0x81, 0xff, 0x100, 0x7fff, 0x8000,
              0x8001, 0xfffe, 0xffff, 0x1234, 0xff90, 0x12c8]

    async def bench(ctx, dut):
        for div in (0, 1):
            for size in (0, 1):
                for signed in (0, 1):
                    for a in values:
                        for b in values:
                            got = await operate(
                                ctx, dut, a, b, div, size, signed
                            )
                            want = spec(a, b, div, size, signed)
                            check(want, got, (a, b, div, size, signed))

    run_bench(bench)


def test_muldiv_random():
    rng = random.Random(7)

    async def bench(ctx, dut):
        for _ in range(3000):
            a = rng.getrandbits(16)
            b = rng.getrandbits(16)
            if rng.random() < 0.3:
                b >>= rng.randrange(16)
            div, size, signed = (rng.getrandbits(1) for _ in range(3))
            got = await operate(ctx, dut, a, b, div, size, signed)
            check(spec(a, b, div, size, signed), got, (a, b, div, size, signed))

    run_bench(bench)


if __name__ == '__main__':
    test_muldiv_edges()
    test_muldiv_random()
    print('ok')
