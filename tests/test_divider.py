from amaranth.sim import Simulator


async def run_division_cycles(
    sim, dut, dividend_val, divisor_val, sel_8b_val, is_signed_val
):
    mask = 0xFF if sel_8b_val else 0xFFFF

    sim.set(dut.dividend, dividend_val & mask)
    sim.set(dut.divisor, divisor_val & mask)
    sim.set(dut.size, sel_8b_val)
    sim.set(dut.signed, is_signed_val)
    sim.set(dut.start, 1)
    await sim.tick()

    sim.set(dut.start, 0)
    await sim.tick()

    cycles = 0
    while not sim.get(dut.done):
        await sim.tick()
        cycles += 1
        if cycles > 20:
            raise TimeoutError(
                'Hardware engine timed out waiting for `done` flag.'
            )

    return sim.get(dut.quotient), sim.get(dut.remainder), sim.get(dut.zero_div)


def to_signed(val, bits):
    return val - (1 << bits) if val >= (1 << (bits - 1)) else val


def test_divider():
    """
    This function is discovered by pytest and performs actual hardware
    assertions.
    """
    from divider import ConfigurableRadix4Divider

    dut = ConfigurableRadix4Divider()

    # Internal simulation loop
    async def sim_loop(sim):
        # Test Case A: 16-bit Unsigned (5000 / 3)
        q, r, z = await run_division_cycles(
            sim, dut, 5000, 3, sel_8b_val=0, is_signed_val=0
        )
        assert q == 1666, f'Expected Q=1666, got {q}.'
        assert r == 2, f'Expected R=2, got {r}.'
        assert z == 0

        # Test Case B: 16-bit Signed (-1234 / 10)
        q, r, z = await run_division_cycles(
            sim, dut, -1234, 10, sel_8b_val=0, is_signed_val=1,
        )
        assert to_signed(q, 16) == -123, f'Expected Q=-123, got {to_signed(q, 16)}.'
        assert to_signed(r, 16) == -4, f'Expected R=-4, got {to_signed(r, 16)}.'
        assert z == 0

        # Test Case C: Divide by Zero
        _, _, z = await run_division_cycles(
            sim, dut, 42, 0, sel_8b_val=0, is_signed_val=0
        )
        assert z == 1, 'Expected zero_div flag to be raised.'

        # 16-bit Max Unsigned Value / 1 (65535 / 1)
        q, r, z = await run_division_cycles(sim, dut, 0xFFFF, 1, sel_8b_val=0, is_signed_val=0)
        assert q == 0xFFFF and r == 0 and z == 0

        # 16-bit Max Unsigned Value / Max Unsigned Value (65535 / 65535)
        q, r, z = await run_division_cycles(sim, dut, 0xFFFF, 0xFFFF, sel_8b_val=0, is_signed_val=0)
        assert q == 1 and r == 0 and z == 0

        # Small Dividend, Large Divisor (Quotient should be exactly 0, Remainder = Dividend)
        # 5 / 500 = 0, R = 5
        q, r, z = await run_division_cycles(sim, dut, 5, 500, sel_8b_val=0, is_signed_val=0)
        assert q == 0 and r == 5 and z == 0

        # 8-bit Max Unsigned Value Boundaries
        # 255 / 2 = 127, R = 1
        q, r, z = await run_division_cycles(sim, dut, 255, 2, sel_8b_val=1, is_signed_val=0)
        assert q == 127 and r == 1 and z == 0

    # Set up and execute the Amaranth simulation environment
    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(sim_loop)
    sim.run()


def spec(dividend, divisor, size, signed):
    """
    What the divider must give, written from its description: quotient,
    remainder, zero_div, overflow. Only the low byte of the operands counts
    in 8-bit mode.
    """
    bits = 8 if size else 16
    mask = (1 << bits) - 1
    a, b = dividend & mask, divisor & mask
    if b == 0:
        return mask, a, 1, 0
    if signed:
        sa, sb = to_signed(a, bits), to_signed(b, bits)
        if sa == -(1 << (bits - 1)) and sb == -1:
            return 1 << (bits - 1), 0, 0, 1
        q = abs(sa) // abs(sb)
        if (sa < 0) != (sb < 0):
            q = -q
        return q & mask, (sa - q * sb) & mask, 0, 0
    return a // b, a % b, 0, 0


async def divide(ctx, dut, dividend, divisor, size, signed):
    """
    A division with the operands as they are (not masked to the width): the
    result (quotient, remainder, zero_div, overflow) as `spec` gives it.
    Checks the number of clocks and the ready and done flags on the way.
    """
    assert ctx.get(dut.ready) == 1
    ctx.set(dut.dividend, dividend)
    ctx.set(dut.divisor, divisor)
    ctx.set(dut.size, size)
    ctx.set(dut.signed, signed)
    ctx.set(dut.start, 1)
    await ctx.tick()
    ctx.set(dut.start, 0)
    # the inputs may change now
    ctx.set(dut.dividend, 0x5555)
    ctx.set(dut.divisor, 0xaaaa)
    ctx.set(dut.size, 1 - size)
    ctx.set(dut.signed, 1 - signed)
    clocks = 0
    while not ctx.get(dut.done):
        assert ctx.get(dut.ready) == 0, 'ready while busy'
        await ctx.tick()
        clocks += 1
        assert clocks <= 8, 'no done'
    assert clocks == (4 if size else 8), clocks
    assert ctx.get(dut.ready) == 1
    return (
        ctx.get(dut.quotient),
        ctx.get(dut.remainder),
        ctx.get(dut.zero_div),
        ctx.get(dut.overflow),
    )


def run_bench(bench):
    from divider import ConfigurableRadix4Divider

    dut = ConfigurableRadix4Divider()
    sim = Simulator(dut)
    sim.add_clock(1e-6)

    async def testbench(ctx):
        await bench(ctx, dut)

    sim.add_testbench(testbench)
    sim.run()


def test_divider_edges():
    cases = [
        # 8-bit, signed: signs of the quotient and the remainder
        (100, 7, 1, 1),
        (-100, 7, 1, 1),
        (100, -7, 1, 1),
        (-100, -7, 1, 1),
        (-128, 1, 1, 1),
        (127, -128, 1, 1),
        (-128, 127, 1, 1),
        # 16-bit, signed
        (-1234, 10, 0, 1),
        (1234, -10, 0, 1),
        (-32768, 1, 0, 1),
        (-32768, 2, 0, 1),
        (-32768, 32767, 0, 1),
        (32767, -32768, 0, 1),
        (-1, -1, 0, 1),
        # the same bits unsigned
        (0xff80, 0xff01, 0, 0),
        (0x80, 0x81, 1, 0),
        (0xff, 0xff, 1, 0),
        # the quotient does not fit
        (-32768, -1, 0, 1),
        (-128, -1, 1, 1),
        # but not unsigned, and not other divisors
        (0x8000, 0xffff, 0, 0),
        (0x80, 0xff, 1, 0),
        (-32768, -2, 0, 1),
        # a divisor of 0
        (42, 0, 0, 0),
        (42, 0, 0, 1),
        (-42, 0, 0, 1),
        (-42, 0, 1, 1),
        (0, 0, 0, 0),
        # small and large
        (0, 5, 0, 0),
        (0, 5, 1, 1),
        (5, 500, 0, 0),
        (0xffff, 1, 0, 0),
        (0xffff, 0xffff, 0, 0),
        (0xffff, 2, 0, 0),
        (0xfffe, 0xffff, 0, 0),
    ]

    async def bench(ctx, dut):
        for dividend, divisor, size, signed in cases:
            # the operands as they are, and with other bits above the byte
            # in the 8-bit mode, which must not matter, zero in the divisor
            # included
            junk = [(0, 0)]
            if size:
                junk += [(0xa500, 0x5a00), (0xff00, 0xff00), (0, 0xff00)]
            for hi_a, hi_b in junk:
                a = (dividend & (0xff if size else 0xffff)) | hi_a
                b = (divisor & (0xff if size else 0xffff)) | hi_b
                got = await divide(ctx, dut, a, b, size, signed)
                want = spec(a, b, size, signed)
                assert got == want, (
                    f'{a:#x} / {b:#x}, size={size}, signed={signed}: '
                    f'got {got}, expected {want}'
                )

        # the flags and the result hold until the next start
        got = await divide(ctx, dut, 7, 0, 0, 0)
        assert got == spec(7, 0, 0, 0)
        for _ in range(5):
            await ctx.tick()
        assert ctx.get(dut.zero_div) == 1
        assert ctx.get(dut.quotient) == 0xffff
        assert ctx.get(dut.done) == 0
        got = await divide(ctx, dut, 7, 2, 0, 0)
        assert got == (3, 1, 0, 0)

        # a start while it is busy does nothing
        ctx.set(dut.dividend, 1000)
        ctx.set(dut.divisor, 7)
        ctx.set(dut.size, 0)
        ctx.set(dut.signed, 0)
        ctx.set(dut.start, 1)
        await ctx.tick()
        ctx.set(dut.dividend, 1)
        ctx.set(dut.divisor, 1)
        for _ in range(3):
            await ctx.tick()
            assert ctx.get(dut.ready) == 0
        while not ctx.get(dut.done):
            await ctx.tick()
        # `start` goes down before the next clock edge
        ctx.set(dut.start, 0)
        assert (ctx.get(dut.quotient), ctx.get(dut.remainder)) == (142, 6)
        await ctx.tick()

        # done is one clock long
        ctx.set(dut.dividend, 9)
        ctx.set(dut.divisor, 2)
        ctx.set(dut.start, 1)
        await ctx.tick()
        ctx.set(dut.start, 0)
        seen = 0
        for _ in range(20):
            await ctx.tick()
            seen += ctx.get(dut.done)
        assert seen == 1, seen
        assert (ctx.get(dut.quotient), ctx.get(dut.remainder)) == (4, 1)

    run_bench(bench)


def test_divider_random():
    import random

    rng = random.Random(11)

    def operand(size):
        bits = 8 if size else 16
        return rng.choice([
            rng.getrandbits(bits),
            rng.getrandbits(bits),
            rng.getrandbits(bits // 2),
            rng.getrandbits(3),
            0,
            1,
            (1 << bits) - 1,
            1 << (bits - 1),
            (1 << (bits - 1)) - 1,
        ])

    async def bench(ctx, dut):
        for _ in range(2000):
            size = rng.randrange(2)
            signed = rng.randrange(2)
            a, b = operand(size), operand(size)
            if size:
                a |= rng.getrandbits(8) << 8
                b |= rng.getrandbits(8) << 8
            got = await divide(ctx, dut, a, b, size, signed)
            want = spec(a, b, size, signed)
            assert got == want, (
                f'{a:#x} / {b:#x}, size={size}, signed={signed}: '
                f'got {got}, expected {want}'
            )

    run_bench(bench)


if __name__ == '__main__':
    test_divider()
    test_divider_edges()
    test_divider_random()
