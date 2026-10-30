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


if __name__ == '__main__':
    test_divider()
