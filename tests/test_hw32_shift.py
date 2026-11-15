"""
Test of hwtest/hw32_shift.asm on the model of the system: SHL, SHR and SAR by a
count in the instruction or in TOS, with the flags.

Run with `python test_hw32_shift.py` (or pytest).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from asm.asm import assemble
from system_model import SystemModel, run


def test_shift():
    with open(os.path.join(ROOT, 'hwtest', 'hw32_shift.asm')) as f:
        src = f.read()
    words, _ = assemble(src)
    m = SystemModel(b''.join(w.to_bytes(2, 'little') for w in words))
    run(m, 20000)
    assert bytes(m.out).decode() == 'ABCDEFGHIJKLMNOPQR'


if __name__ == '__main__':
    test_shift()
    print('test_shift ok')
