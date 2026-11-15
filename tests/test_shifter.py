"""
Test for shifter.py: SLL, SRL and SRA (shifts that saturate at the width, with the last bit
shifted out as the carry), ROL and ROR (modulo the width) and RCL and RCR (through carry, modulo
the width + 1), of 16 or 8 bits, against a
plain Python description. The same description is checked in test_shift_model.py against the
reference model of the instructions SHL, SHR and SAR.

Run with `python test_shifter.py` (or pytest).
"""
import random

from amaranth.sim import Simulator

from shifter import DualWidthBarrelShifter16, ShiftOp


def signed_of(v, bits):
    return v - (1 << bits) if v >> (bits - 1) else v


def spec_shift(op, a, count, bits):
    """-> result, carry (None when the count is 0: C is not updated)"""
    mask = (1 << bits) - 1
    a &= mask
    if count == 0:
        return a, None
    sa = signed_of(a, bits)
    if op == ShiftOp.SLL:
        result = a << count & mask if count < bits else 0
        carry = a >> (bits - count) & 1 if count <= bits else 0
    elif op == ShiftOp.SRL:
        result = a >> count if count < bits else 0
        carry = a >> (count - 1) & 1 if count <= bits else 0
    else:
        result = sa >> min(count, bits - 1) & mask
        carry = sa >> min(count - 1, bits - 1) & 1
    return result, carry


def spec_rotate(op, a, count, bits):
    mask = (1 << bits) - 1
    a &= mask
    c = count % bits
    if op == ShiftOp.ROL:
        result = (a << c | a >> (bits - c)) & mask if c else a
        carry = result & 1
    else:
        result = (a >> c | a << (bits - c)) & mask if c else a
        carry = result >> (bits - 1) & 1
    return result, carry


def spec_rc(op, a, count, bits, carry_in):
    """The rotate through carry: C and the data are one ring of bits + 1, by the low 4 bits of count (0 is 16)"""
    mask = (1 << bits) - 1
    width = bits + 1
    n = (count & 15 or 16) % width
    v = (carry_in << bits | a & mask)
    if op == ShiftOp.RCR:
        n = (width - n) % width
    v = (v << n | v >> (width - n)) & (1 << width) - 1
    return v & mask, v >> bits


VALUES = [
    0x0000, 0x0001, 0x0002, 0x0003, 0x007f, 0x0080, 0x0081, 0x00ff, 0x0100, 0x0101, 0x5a5a, 0x7fff,
    0x8000, 0x8001, 0x8080, 0xff00, 0xff7f, 0xff80, 0xffff, 0xaa55, 0x1234,
]

# Around the widths of both modes (8 and 16), the powers of two, and the high bits that must
# make a shift saturate whatever the low bits are
COUNTS = list(range(0, 36)) + [
    47, 48, 63, 64, 127, 128, 255, 256, 257, 0x0108, 0x0110, 0x1000, 0x1008, 0x4000, 0x7fff, 0x8000,
    0x8001, 0x8008, 0x8010, 0xfff0, 0xfff7, 0xfff8, 0xffff,
]

SHIFTS = (ShiftOp.SLL, ShiftOp.SRL, ShiftOp.SRA)
ROTATES = (ShiftOp.ROL, ShiftOp.ROR)
RCS = (ShiftOp.RCL, ShiftOp.RCR)


async def apply(ctx, dut, op, a, count, size, carry_in=0):
    ctx.set(dut.carry_in, carry_in)
    ctx.set(dut.op, op)
    ctx.set(dut.data_in, a)
    ctx.set(dut.shamt, count)
    ctx.set(dut.size, size)
    return ctx.get(dut.data_out), ctx.get(dut.carry_out), ctx.get(dut.carry_upd)


async def check(ctx, dut, op, a, count, size, carry_in=0):
    bits = 8 if size else 16
    if op in RCS:
        result, carry = spec_rc(op, a, count, bits, carry_in)
    elif op in ROTATES:
        result, carry = spec_rotate(op, a, count, bits)
    else:
        result, carry = spec_shift(op, a, count, bits)
    out, cy, upd = await apply(ctx, dut, op, a, count, size, carry_in)
    where = f'{op.name} size={size} a={a:#06x} count={count:#06x} carry_in={carry_in}'
    assert out == result, f'{where}: result {out:#06x}, expected {result:#06x}'
    assert upd == int(count != 0 or op in RCS), f'{where}: carry_upd {upd}'
    if carry is not None and upd:
        assert cy == carry, f'{where}: carry {cy}, expected {carry}'
    if size:
        # The upper byte of the input does not matter and that of the output is zero
        assert out >> 8 == 0, where
        other, cy2, _ = await apply(ctx, dut, op, a ^ 0xff00, count, size, carry_in)
        assert other == out and (cy2 == cy or not upd), f'{where}: the upper input byte matters'


def run_test(body):
    dut = DualWidthBarrelShifter16()
    sim = Simulator(dut)

    async def bench(ctx):
        await body(ctx, dut)

    sim.add_testbench(bench)
    sim.run()


def test_corners():
    async def body(ctx, dut):
        for size in (0, 1):
            for op in SHIFTS + ROTATES + RCS:
                for a in VALUES:
                    for count in COUNTS:
                        for carry_in in (0, 1):
                            await check(ctx, dut, op, a, count, size, carry_in)
    run_test(body)


def test_random():
    async def body(ctx, dut):
        rng = random.Random(1)
        for _ in range(3000):
            size = rng.randrange(2)
            op = rng.choice(SHIFTS + ROTATES + RCS)
            a = rng.randrange(1 << 16)
            count = rng.choice([rng.randrange(40), rng.randrange(1 << 16)])
            await check(ctx, dut, op, a, count, size, rng.randrange(2))
    run_test(body)


def test_every_bit_every_count():
    """One bit set at each position, every count 0..35: where each bit goes and what is shifted out"""
    async def body(ctx, dut):
        for size in (0, 1):
            for op in SHIFTS + ROTATES + RCS:
                for bit in range(16):
                    for count in range(36):
                        for carry_in in (0, 1):
                            await check(ctx, dut, op, 1 << bit, count, size, carry_in)
                            await check(ctx, dut, op, 0xffff ^ (1 << bit), count, size, carry_in)
    run_test(body)


def test_byte_saturation():
    """The bug that a count of 8..15 in 8-bit mode was taken as 0..7 stays out"""
    async def body(ctx, dut):
        for count in range(8, 17):
            out, cy, upd = await apply(ctx, dut, ShiftOp.SLL, 0x00ff, count, 1)
            assert out == 0 and upd == 1
            assert cy == int(count == 8)
            out, cy, _ = await apply(ctx, dut, ShiftOp.SRL, 0x0080, count, 1)
            assert out == 0 and cy == int(count == 8)
            out, cy, _ = await apply(ctx, dut, ShiftOp.SRA, 0x0080, count, 1)
            assert out == 0x00ff and cy == 1
            out, cy, _ = await apply(ctx, dut, ShiftOp.SRA, 0x007f, count, 1)
            assert out == 0 and cy == 0
    run_test(body)


def test_word_saturation():
    async def body(ctx, dut):
        for count in (16, 17, 0x10, 0x100, 0x8000, 0xffff):
            out, cy, _ = await apply(ctx, dut, ShiftOp.SLL, 0xffff, count, 0)
            assert out == 0 and cy == int(count == 16)
            out, cy, _ = await apply(ctx, dut, ShiftOp.SRL, 0x8000, count, 0)
            assert out == 0 and cy == int(count == 16)
            out, cy, _ = await apply(ctx, dut, ShiftOp.SRA, 0x8000, count, 0)
            assert out == 0xffff and cy == 1
            out, cy, _ = await apply(ctx, dut, ShiftOp.SRA, 0x7fff, count, 0)
            assert out == 0 and cy == 0
        # A count of 0: nothing moves and the carry is not to be updated
        out, _, upd = await apply(ctx, dut, ShiftOp.SRA, 0x8421, 0, 0)
        assert out == 0x8421 and upd == 0
    run_test(body)


if __name__ == '__main__':
    test_corners()
    test_random()
    test_every_bit_every_count()
    test_byte_saturation()
    test_word_saturation()
    print('ok')
