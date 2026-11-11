"""
Test of hwtest/hw25_enc28j60_spi.asm on the model of the system, with a model of
the ENC28J60 behind the SPI master (enc28j60_model.py): the documented values,
a revision of the PHY, wrong values, no chip, a slow chip, and a buffer that
does not keep what is written.

Run with `python test_hw25_enc28j60_spi.py` (or pytest).
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from asm.asm import assemble
from enc28j60_model import ENC28J60
from system_model import SystemModel, run


def run_program(dev, steps=6000000):
    with open(os.path.join(ROOT, 'hwtest', 'hw25_enc28j60_spi.asm')) as f:
        words, _ = assemble(f.read())
    flash = b''.join(w.to_bytes(2, 'little') for w in words)
    m = SystemModel(flash)
    m.spi_dev = dev
    assert run(m, steps) is not None, 'no halt'
    return bytes(m.out).decode()


GOOD = (
    'ENC28J60 PHID1=0083 PHID2=1400 OK\r\n'
    'EREVID=0006\r\n'
    'BUFFER 64 BYTES OK\r\n'
)


def split_time(out):
    m = re.fullmatch(
        r'(.*)READ 1024 BYTES IN ([0-9A-F]{4}) US \(HEX\)\r\n', out, re.S
    )
    assert m, out
    return m.group(1), int(m.group(2), 16)


def test_documented_values():
    head, us = split_time(run_program(ENC28J60()))
    assert head == GOOD, head
    # 512 words at 12.5 MHz: 1.28 us each, 655 us at the least
    assert 655 <= us < 3000, us


def test_revision_of_the_phy():
    head, _ = split_time(run_program(ENC28J60(phid2=0x1406, revid=4)))
    assert head == GOOD.replace('1400', '1406').replace('0006', '0004'), head


def test_wrong_values():
    head, _ = split_time(run_program(ENC28J60(phid1=0x0084)))
    assert head == GOOD.replace('0083', '0084').replace(
        '1400 OK', '1400 ERR'), head
    head, _ = split_time(run_program(ENC28J60(phid2=0x1500)))
    assert head == GOOD.replace('1400 OK', '1500 ERR'), head
    head, _ = split_time(run_program(ENC28J60(phid1=0xffff, phid2=0xffff)))
    assert head == GOOD.replace('0083', 'FFFF').replace(
        '1400 OK', 'FFFF ERR'), head


def test_no_chip():
    out = run_program(ENC28J60(present=False, idle_miso=0))
    assert out == 'ENC28J60 NO CLKRDY\r\n', out
    out = run_program(ENC28J60(present=False, idle_miso=1))
    assert out == 'ENC28J60 MII TIMEOUT\r\n', out
    assert run_program(None) == 'ENC28J60 NO CLKRDY\r\n'


def test_slow_chip():
    head, _ = split_time(run_program(ENC28J60(clkrdy_reads=40, busy_reads=60)))
    assert head == GOOD, head


def test_bad_buffer():
    class Flaky(ENC28J60):
        def wbm(self, byte):
            super().wbm(byte ^ 0x10)

    head, _ = split_time(run_program(Flaky()))
    assert head == GOOD.replace('64 BYTES OK', '64 BYTES ERR'), head


if __name__ == '__main__':
    for name, f in list(globals().items()):
        if name.startswith('test_'):
            f()
            print(name, 'ok')
