"""
Test of hwtest/hw33_string.asm on the model of the system: the string forms
`[mbp+midx++]*` and `[mbp+--midx]*` of the pointer modes (001 1 mm e), and
the encodings of the modes 0 and 3 that stay reserved.

Run with `python test_hw33_string.py` (or pytest).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from asm.asm import assemble
from system_model import SystemModel, run


def test_string():
    with open(os.path.join(ROOT, 'hwtest', 'hw33_string.asm')) as f:
        src = f.read()
    words, _ = assemble(src)
    m = SystemModel(b''.join(w.to_bytes(2, 'little') for w in words))
    run(m, 20000)
    assert bytes(m.out).decode() == 'ABCDEFGH'


def test_abs_string():
    with open(os.path.join(ROOT, 'hwtest', 'hw34_absstr.asm')) as f:
        src = f.read()
    words, _ = assemble(src)
    m = SystemModel(b''.join(w.to_bytes(2, 'little') for w in words))
    run(m, 20000)
    assert bytes(m.out).decode() == 'ABCD'


def test_deep():
    with open(os.path.join(ROOT, 'hwtest', 'hw35_deep.asm')) as f:
        src = f.read()
    words, _ = assemble(src)
    m = SystemModel(b''.join(w.to_bytes(2, 'little') for w in words))
    run(m, 20000)
    assert bytes(m.out).decode() == 'AB'


def test_reserved_modes():
    # st pop (op 9, dd 2) with the string field 001 1 mm e
    for mm, legal in ((0, False), (1, True), (2, True), (3, False)):
        for e in (0, 1):
            w = 0x8000 | 9 << 10 | 2 << 8 | 0x18 | mm << 1 | e
            assert SystemModel.legal(w) == legal, hex(w)


if __name__ == '__main__':
    test_string()
    test_abs_string()
    test_deep()
    test_reserved_modes()
    print('test_string ok')
