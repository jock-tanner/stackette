"""
Test of hwtest/hw28_ide_cf.asm on the model of the system, with a model of a
CompactFlash card behind the IDE host (ide_model.py): IDENTIFY and a sector
written and read back, a card that gets ready late, no card, and a card whose
sector does not keep what is written.

Run with `python test_hw28_ide_cf.py` (or pytest).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from asm.asm import assemble
from ide_model import CFCard
from system_model import SystemModel, run


class FloatingCard(CFCard):
    """A bus with nothing on it: every read gives all ones."""
    def read(self, cs1, da):
        return 0xffff if da == 0 else 0xff


class FlakyCard(CFCard):
    """A card that loses the last word of a sector."""
    def write_data(self, v):
        super().write_data(v if len(self.buffer) != 255 else v ^ 1)


def run_program(dev, steps=20000000, polls=None):
    with open(os.path.join(ROOT, 'hwtest', 'hw28_ide_cf.asm')) as f:
        src = f.read()
    if polls:
        # fewer turns of the outer loop of the waits, to keep the test short
        src = src.replace('ld push, #100\n', 'ld push, #%d\n' % polls)
    words, _ = assemble(src)
    flash = b''.join(w.to_bytes(2, 'little') for w in words)
    m = SystemModel(flash)
    m.ide_dev = dev
    assert run(m, steps) is not None, 'no halt'
    return bytes(m.out).decode()


def test_identify_and_sector():
    card = CFCard()
    out = run_program(card)
    assert out == (
        'IDE CF\r\n'
        'MODEL: STACKETTE TEST CARD                     \r\n'
        'SECTORS=00000800\r\n'
        'WRITE/READ 1 SECTOR OK\r\n'
    ), out
    assert card.image[4 * 512] == 0x34 and card.image[4 * 512 + 1] == 0x12


def test_card_ready_late():
    out = run_program(CFCard(busy=300))
    assert out.endswith('WRITE/READ 1 SECTOR OK\r\n'), out


def test_no_card():
    out = run_program(FloatingCard(), polls=1)
    assert out == (
        'IDE CF\r\nNO CARD ST=00FF AL=00FF HS=0004 SC=00FF ST1=00FF\r\n'
    ), out


def test_bad_sector():
    out = run_program(FlakyCard())
    assert out.endswith('WRITE/READ 1 SECTOR ERR 0001\r\n'), out


if __name__ == '__main__':
    for name, f in list(globals().items()):
        if name.startswith('test_'):
            f()
            print(name, 'ok')
