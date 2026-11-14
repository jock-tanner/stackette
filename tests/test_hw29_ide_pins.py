"""
Test of hwtest/hw29_ide_pins.asm on the model of the system: the pins by hand
(BB_CTRL, BB_DD_OUT, BB_DD_IN, BB_PINS of ide_pio.py).

Run with `python test_hw29_ide_pins.py` (or pytest).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from asm.asm import assemble
from system_model import SystemModel, run


def test_pins():
    with open(os.path.join(ROOT, 'hwtest', 'hw29_ide_pins.asm')) as f:
        src = f.read()
    # no two seconds of waiting in the model
    src = src.replace('ld push, #40\n', 'ld push, #1\n')
    words, _ = assemble(src)
    m = SystemModel(b''.join(w.to_bytes(2, 'little') for w in words))
    run(m, 6000000)
    out = bytes(m.out).decode()
    assert out.startswith(
        'HW29 IDE PINS\r\n'
        'DD UNDRIVEN=0080  INTRQ,IORDY=0002\r\n'
        '  DRIVEN 5555->5555\r\n'
        '  DRIVEN AAAA->AAAA\r\n'
        '  DRIVEN FFFF->FFFF\r\n'
        '  DRIVEN 0000->0000\r\n'
        'RESET# ACTIVE\r\nRESET# IDLE\r\n'
        'CS0# ACTIVE\r\nCS0# IDLE\r\n'
        'CS1# ACTIVE\r\nCS1# IDLE\r\n'
        'DIOR# ACTIVE\r\nDIOR# IDLE\r\n'
        'DIOW# ACTIVE\r\nDIOW# IDLE\r\n'
        'DA0 ACTIVE\r\nDA0 IDLE\r\n'
        'DA1 ACTIVE\r\nDA1 IDLE\r\n'
        'DA2 ACTIVE\r\nDA2 IDLE\r\n'
        'DD0000 ACTIVE\r\nDD0001 ACTIVE\r\n'
    ), out
    assert m.ide_bb[0] in (0x21f, 0x31f)


if __name__ == '__main__':
    test_pins()
    print('test_pins ok')
