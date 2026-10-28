"""
Simple test for alu.py: every op, 8- and 16-bit, against a plain-Python model.

Run with `python test_alu.py` (or pytest). The ALU is combinational, so each
vector is: set the inputs, let the simulation settle, compare all outputs.

Checked, per op (see ref() for the exact definitions):
  * result      (8-bit results are zero-extended; the ALU must ignore the
                 upper byte of its operands in 8-bit mode, so the test fills
                 that byte with random garbage)
  * carry       ADD/ADC/SUB/SBC/NEG only; see CARRY_IS_NOT_BORROW below
  * overflow    same ops as carry (signed overflow)
  * zero, sign, parity   all ops (parity = 1 when the number of set bits in
                 the result is even, over the full operand length)

RRC (rotate right through carry) also checks carry: it is the bit that fell out.

Not checked: carry and overflow for the logical ops (AND, OR, XOR, BIC, NOT,
SXT, BSWAP), and overflow for RRC. The ISA leaves C and V unchanged for those, which is decided
outside the ALU, so any value is fine.

SXT and BSWAP are word operations: they are only tested with size = word, and
their flags are those of the 16-bit result. (In byte mode the ALU's SXT is the
identity and BSWAP is meaningless, so decode should issue them as word ops.)
"""
import itertools
import random
from collections import defaultdict

from amaranth.sim import Simulator

from alu import ALU, OpCodes

# ISA decision: after SUB/SBC/NEG, C = NOT borrow (as on ARM / 6502), so that
# SBC is ADC with the operand inverted and the HI / LS conditions are
# "C & !Z" / "!C | Z". Set to False for x86-style borrow (C = borrow; SBC
# subtracts carry_in directly) -- then swap the HI / LS definitions too.
CARRY_IS_NOT_BORROW = True
SIZE_BYTE = 1  # alu.size: 1 selects 8-bit operations, 0 selects 16-bit
CHECK_PARITY = True

LOGIC = {
    OpCodes.AND, OpCodes.OR, OpCodes.XOR, OpCodes.BIC, OpCodes.NOT,
    OpCodes.SXT, OpCodes.BSWAP, OpCodes.RRC
}
WORD_ONLY = {OpCodes.SXT, OpCodes.BSWAP}
FIELDS = ('result', 'carry', 'overflow', 'zero', 'sign', 'parity')


def ref(op, a, b, cin, bits):
    """Expected outputs as a dict (carry/overflow are None when don't-care)."""
    mask = (1 << bits) - 1
    msb = 1 << (bits - 1)
    a &= mask
    b &= mask
    carry = overflow = 0

    if op in (OpCodes.ADD, OpCodes.ADC):
        t = a + b + (cin if op == OpCodes.ADC else 0)
        res = t & mask
        carry = (t >> bits) & 1
        overflow = int(bool((a ^ res) & (b ^ res) & msb))
    elif op in (OpCodes.SUB, OpCodes.SBC):
        if op == OpCodes.SUB:
            borrow_in = 0
        else:
            borrow_in = (1 - cin) if CARRY_IS_NOT_BORROW else cin
        t = a - b - borrow_in
        res = t & mask
        borrow = int(t < 0)
        carry = (1 - borrow) if CARRY_IS_NOT_BORROW else borrow
        overflow = int(bool((a ^ b) & (a ^ res) & msb))
    elif op == OpCodes.AND:
        res = a & b
    elif op == OpCodes.OR:
        res = a | b
    elif op == OpCodes.XOR:
        res = a ^ b
    elif op == OpCodes.BIC:
        res = a & ~b & mask
    elif op == OpCodes.NOT:
        res = ~a & mask
    elif op == OpCodes.SXT:
        res = (a & 0xFF) | (0xFF00 if a & 0x80 else 0)
    elif op == OpCodes.BSWAP:
        res = ((a & 0xFF) << 8) | (a >> 8)
    elif op == OpCodes.RRC:
        res = (cin << (bits - 1)) | (a >> 1)
        carry = a & 1
    elif op == OpCodes.NEG:
        res = (-a) & mask
        zero_src = int(a == 0)
        carry = zero_src if CARRY_IS_NOT_BORROW else 1 - zero_src
        overflow = int(a == msb)
    else:
        raise ValueError(op)

    return {
        'result': res,
        'carry': carry if op == OpCodes.RRC or op not in LOGIC else None,
        'overflow': None if op in LOGIC else overflow,
        'zero': int(res == 0),
        'sign': int(bool(res & msb)),
        'parity': int(bin(res).count('1') % 2 == 0) if CHECK_PARITY else None,
    }


def vectors(rng, n_random=3000):
    """Yield (bits, op, a, b, carry_in): corner cases first, then random."""
    for bits in (16, 8):
        mask = (1 << bits) - 1
        msb = 1 << (bits - 1)
        corners = sorted({
            0, 1, 2, msb - 1, msb, msb + 1, mask - 1, mask,
            0x5555 & mask, 0xAAAA & mask,
        })

        def widen(v):
            # 8-bit mode must ignore bits 15:8, so put garbage there
            return v if bits == 16 else (rng.getrandbits(8) << 8) | v

        for op in OpCodes:
            if bits == 8 and op in WORD_ONLY:
                continue
            for a, b, cin in itertools.product(corners, corners, (0, 1)):
                yield bits, op, widen(a), widen(b), cin
            for _ in range(n_random // len(OpCodes)):
                yield (bits, op, rng.getrandbits(16), rng.getrandbits(16),
                       rng.getrandbits(1))


def test_alu():
    rng = random.Random(1234)
    alu = ALU()
    sim = Simulator(alu)
    failures = defaultdict(list)    # (op name, bits, field) -> messages
    count = 0

    async def bench(ctx):
        nonlocal count
        for bits, op, a, b, cin in vectors(rng):
            ctx.set(alu.operand_a, a)
            ctx.set(alu.operand_b, b)
            ctx.set(alu.carry_in, cin)
            ctx.set(alu.op_code, op.value)
            ctx.set(alu.size, SIZE_BYTE if bits == 8 else 1 - SIZE_BYTE)
            await ctx.delay(1e-9)

            expected = ref(op, a, b, cin, bits)
            for field in FIELDS:
                if expected[field] is None:
                    continue
                got = ctx.get(getattr(alu, field))
                if got != expected[field]:
                    failures[(op.name, bits, field)].append(
                        f'a={a:#06x} b={b:#06x} cin={cin}: '
                        f'expected {expected[field]:#x}, got {got:#x}'
                    )
            count += 1

    sim.add_testbench(bench)
    sim.run()

    if failures:
        print(
            f'FAILED: {sum(map(len, failures.values()))} mismatches '
            f'in {count} vectors'
        )
        for (name, bits, field), msgs in sorted(failures.items()):
            print(
                f'  {name:3} {bits:2}-bit {field:8} x{len(msgs):<5} '
                f'first: {msgs[0]}'
            )
        raise AssertionError('ALU does not match the model!')
    print(f'ok: {count} vectors')


if __name__ == '__main__':
    test_alu()
