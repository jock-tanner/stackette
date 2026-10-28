"""
Test of flashtool.py against a software model of the bridge and the MX25L8005
(WEL and WIP bits, 1->0 only programming, page wrap, block protection). Plain
Python: python test_flashtool.py
"""
import os
import random
import tempfile

import flashtool


class FakeChip:
    def __init__(self, size=flashtool.CHIP_SIZE):
        self.mem = bytearray(b'\xff' * size)
        self.sr = 0
        self.busy_polls = 0
        self.cmd = bytearray()
        self.erases = 0
        self.programs = 0

    def select(self):
        self.cmd = bytearray()

    def deselect(self):
        c = self.cmd
        if c[:1] == bytes([flashtool.CMD_WREN]):
            self.sr |= 2
        elif c[:1] == bytes([flashtool.CMD_WRSR]) and self.sr & 2:
            self.sr = self.sr & ~flashtool.SR_BP | c[1] & flashtool.SR_BP
            self.sr &= ~2
            self.busy_polls = 2
        elif c[:1] == bytes([flashtool.CMD_SE]) and self.sr & 2:
            a = (c[1] << 16 | c[2] << 8 | c[3]) & ~0xfff
            assert not self.sr & flashtool.SR_BP
            self.mem[a:a + 4096] = b'\xff' * 4096
            self.sr &= ~2
            self.busy_polls = 5
            self.erases += 1
        elif c[:1] == bytes([flashtool.CMD_CE]) and self.sr & 2:
            self.mem[:] = b'\xff' * len(self.mem)
            self.sr &= ~2
            self.busy_polls = 9
        elif c[:1] == bytes([flashtool.CMD_PP]) and self.sr & 2 and len(c) > 4:
            assert not self.sr & flashtool.SR_BP
            a = c[1] << 16 | c[2] << 8 | c[3]
            page = a & ~0xff
            assert len(c) - 4 <= 256
            for i, b in enumerate(c[4:]):
                p = page + ((a + i) & 0xff)
                self.mem[p] &= b
            self.sr &= ~2
            self.busy_polls = 2
            self.programs += 1
        self.cmd = bytearray()

    def shift(self, byte):
        """One byte on MOSI -> the byte on MISO."""
        c = self.cmd
        c.append(byte)
        n = len(c)
        op = c[0]
        if op == flashtool.CMD_RDID:
            return [0, 0xc2, 0x20, 0x14][n - 1] if n <= 4 else 0xff
        if op == flashtool.CMD_RDSR:
            if n == 1:
                return 0
            sr = self.sr
            if self.busy_polls:
                sr |= flashtool.SR_WIP
                self.busy_polls -= 1
            return sr
        if op == flashtool.CMD_READ and n > 4:
            a = (c[1] << 16 | c[2] << 8 | c[3]) + n - 5
            return self.mem[a % len(self.mem)]
        return 0


class FakeSerial:
    """The bridge protocol of spi_bridge.py, byte by byte, in front of a chip."""

    def __init__(self, chip):
        self.chip = chip
        self.out = bytearray()
        self.state = 'CMD'
        self.left = 0

    def reset_input_buffer(self):
        self.out.clear()

    def write(self, data):
        for b in data:
            if self.state == 'CMD':
                if b == flashtool.CS_HIGH:
                    self.chip.deselect()
                    self.out.append(b)
                elif b == flashtool.CS_LOW:
                    self.chip.select()
                    self.out.append(b)
                elif b == flashtool.XFER:
                    self.state = 'LEN'
                else:
                    self.out.append(0xff)
            elif self.state == 'LEN':
                self.left = b or 256
                self.state = 'DATA'
            else:
                self.out.append(self.chip.shift(b))
                self.left -= 1
                if not self.left:
                    self.state = 'CMD'

    def read(self, n):
        data = bytes(self.out[:n])
        del self.out[:n]
        return data


def run(chip, *argv):
    flashtool.main(['port', *argv], port=FakeSerial(chip))


def test_all():
    rng = random.Random(5)
    chip = FakeChip()
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, 'a.bin')

    # id
    run(chip, 'id')

    # write into an erased chip: programmed, no erase
    data = bytes(rng.getrandbits(8) for _ in range(10000))
    open(path, 'wb').write(data)
    run(chip, 'write', path, '--offset', '0x1234')
    assert chip.mem[0x1234:0x1234 + len(data)] == data
    assert chip.mem[:0x1234] == b'\xff' * 0x1234
    assert chip.erases == 0

    # the same again: nothing to do
    programs = chip.programs
    run(chip, 'write', path, '--offset', '0x1234')
    assert chip.programs == programs and chip.erases == 0

    # different data over it: erased, and the neighbours of the range are kept
    data2 = bytes(rng.getrandbits(8) for _ in range(3000))
    open(path, 'wb').write(data2)
    before = bytes(chip.mem)
    run(chip, 'write', path, '--offset', '0x2100')
    assert chip.mem[0x2100:0x2100 + 3000] == data2
    assert chip.mem[:0x2100] == before[:0x2100]
    assert chip.mem[0x2100 + 3000:] == before[0x2100 + 3000:]
    assert chip.erases == 1      # 0x2100..0x2cb8 is inside one sector

    # clearing bits only: no erase
    erases = chip.erases
    open(path, 'wb').write(bytes(len(data2)))
    run(chip, 'write', path, '--offset', '0x2100')
    assert chip.erases == erases and chip.mem[0x2100:0x2100 + 3000] == bytes(3000)

    # block protection is cleared
    chip.sr |= flashtool.SR_BP
    open(path, 'wb').write(b'\x55' * 300)
    run(chip, 'write', path, '--offset', '0x80000')
    assert chip.mem[0x80000:0x80000 + 300] == b'\x55' * 300 and not chip.sr & flashtool.SR_BP

    # read back through the tool, verify
    out = os.path.join(tmp, 'b.bin')
    run(chip, 'read', out, '--offset', '0x80000', '--length', '300')
    assert open(out, 'rb').read() == b'\x55' * 300
    run(chip, 'verify', path, '--offset', '0x80000')
    chip.mem[0x80010] ^= 1
    try:
        run(chip, 'verify', path, '--offset', '0x80000')
        raise AssertionError('verify did not fail')
    except SystemExit as e:
        assert '0x080010' in str(e)

    # erase a range and the whole chip
    run(chip, 'erase', '--offset', '0x80000', '--length', '300')
    assert chip.mem[0x80000:0x81000] == b'\xff' * 4096
    run(chip, 'erase')
    assert chip.mem == b'\xff' * len(chip.mem)

    # a wrong chip is refused
    chip2 = FakeChip()
    chip2.shift = lambda byte: 0
    try:
        run(chip2, 'id')
        raise AssertionError('wrong id accepted')
    except SystemExit as e:
        assert 'unexpected flash ID' in str(e)
    print('flashtool tests passed')


if __name__ == '__main__':
    test_all()
