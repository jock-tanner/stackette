"""
The reference model of the shifts and rotates (`11 10 1v dd s kkk cccc`) against a direct
definition, for every kind, both widths, all `dd`, immediate and TOS counts. No hardware: this
fixes the semantics the CPU and the compiler have to follow.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cpu_model import Ref, sext, PSP0

KINDS = (0, 1, 2, 4, 5, 6, 7)


def expect(kind, a, count, bits, carry):
    """-> result, carry (None: count 0, C stays)"""
    mask = (1 << bits) - 1
    a &= mask
    if kind in (6, 7):
        count = count & 15 or 16
    if count == 0:
        return a, None
    sa = sext(a, bits)
    if kind == 0:
        r = (a << count) & mask if count < bits else 0
        c = (a >> (bits - count)) & 1 if count <= bits else 0
    elif kind == 1:
        r = a >> count if count < bits else 0
        c = (a >> (count - 1)) & 1 if count <= bits else 0
    elif kind == 2:
        r = (sa >> min(count, bits - 1)) & mask
        c = (sa >> min(count - 1, bits - 1)) & 1
    elif kind in (4, 5):
        n = count % bits
        if kind == 5:
            n = (bits - n) % bits
        r = (a << n | a >> (bits - n)) & mask
        c = r & 1 if kind == 4 else r >> (bits - 1)
    else:
        width = bits + 1
        n = count % width
        if kind == 7:
            n = (width - n) % width
        v = carry << bits | a
        v = (v << n | v >> (width - n)) & (1 << width) - 1
        r, c = v & mask, v >> bits
    return r, c


def run(w, stack, c):
    m = Ref()
    m.psp = PSP0 - 2 * len(stack)
    for i, v in enumerate(stack):
        m.wr16(m.psp + 2 * i, v)
    m.c, m.v = c, 1
    m.step([w], 2)
    return m


def items(m, n):
    return [m.item(i) for i in range(n)]


def test_shifts():
    vals = [0, 1, 0x5a5a, 0x8000, 0x80, 0xffff, 0x7fff, 0x00ff, 0x1234, 0x8001]
    for kind in KINDS:
        for s in (0, 1):
            bits = 8 if s else 16
            for carry in (0, 1):
                for a in vals:
                    for count in list(range(0, 40)) + [255, 256, 0x8000, 0xffff]:
                        # the count in TOS: dd 1 keeps it, dd 2 drops it, dd 0 and 3 shift
                        # TOS by itself
                        for dd in (0, 1, 2, 3):
                            item = count if dd in (0, 3) else a
                            r, c = expect(kind, item, count, bits, carry)
                            w = 0xec00 | dd << 8 | s << 7 | kind << 4
                            m = run(w, [count, a], carry)
                            if dd == 1:
                                assert items(m, 2) == [count, r], ('var', kind, s, a, count, dd)
                            else:
                                assert items(m, 1) == [r], ('var', kind, s, a, count, dd)
                            assert m.c == (carry if c is None else c), ('var c', kind, s, a, count, dd)
                            assert m.z == int(r == 0) and m.n == r >> (bits - 1) & 1
                            assert m.v == (1 if count == 0 and kind not in (6, 7) else 0)
                # the immediate count: 1..16, cccc = 0 is 16, dd as the unary operations
                for a in vals:
                    for field in range(16):
                        count = field or 16
                        r, c = expect(kind, a, count, bits, carry)
                        for dd in (0, 1, 2, 3):
                            w = 0xe800 | dd << 8 | s << 7 | kind << 4 | field
                            stack = [0x1111, a] if dd in (1, 2) else [a, 0x2222]
                            m = run(w, stack, carry)
                            got = items(m, 3 if dd >= 2 else 2)
                            if dd == 0:
                                want = [r, 0x2222]
                            elif dd == 1:
                                want = [0x1111, r]
                            elif dd == 2:
                                want = [r, 0x1111, a]
                            else:
                                want = [a, r, 0x2222]
                            assert got == want, ('imm', kind, s, a, field, dd, got, want)
                            assert m.c == c and m.v == 0
                            assert m.z == int(r == 0) and m.n == r >> (bits - 1) & 1


if __name__ == '__main__':
    test_shifts()
    print('ok')
