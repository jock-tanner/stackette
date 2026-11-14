"""
Model of a CompactFlash card (an ATA device) for the tests of ide_pio.py: the
task file, IDENTIFY DEVICE, READ SECTORS and WRITE SECTORS (28-bit LBA, PIO),
SET FEATURES, the software reset and the interrupt request.

The model is in two layers: `read(cs1, da)` and `write(cs1, da, v)` are the
cycles of the bus (the registers; a read of the data port takes the next word
of the buffer), and `pins(...)` is used by the test of the hardware to run
those cycles from the pins and to hold IORDY low.

`busy` is the number of reads of the status register that show BSY after a
command, to test the polling; `data_waits` the number of clocks that IORDY is
low after the strobe of a data cycle starts, for the test of the hardware.
"""

BSY = 0x80
DRDY = 0x40
DSC = 0x10
DRQ = 0x08
ERR = 0x01

SECTOR = 512


def ata_string(text, n):
    """A string of n words in the IDENTIFY data: the first character in the
    high byte of the word, padded with spaces."""
    text = text.ljust(2 * n)[:2 * n].encode()
    return [text[i] << 8 | text[i + 1] for i in range(0, 2 * n, 2)]


class CFCard:
    def __init__(self, sectors=2048, model='STACKETTE TEST CARD', busy=0,
                 data_waits=0):
        self.image = bytearray(sectors * SECTOR)
        self.sectors = sectors
        self.model = model
        self.busy_reads = busy
        self.data_waits = data_waits
        self.reset()
        # the pins
        self.intrq = 0
        self.iordy = 1
        self.dd = 0
        self.prev = (1, 1, 1, 1)
        self.wait_left = 0
        self.log = []

    def reset(self):
        self.features = 0
        self.count = 1
        self.lba = [1, 0, 0, 0]
        self.head = 0xa0
        self.nien = 0
        self.status = DRDY | DSC
        self.error = 1
        self.buffer = []
        self.pos = 0
        self.writing = False
        self.busy = 0
        self.irq = False

    @property
    def lba28(self):
        return (
            self.lba[0] | self.lba[1] << 8 | self.lba[2] << 16
            | (self.head & 15) << 24
        )

    def identify(self):
        w = [0] * 256
        w[0] = 0x848a       # CompactFlash
        w[1] = self.sectors // 16 // 63
        w[3] = 16
        w[6] = 63
        w[10:20] = ata_string('0001', 10)
        w[23:27] = ata_string('1.0', 4)
        w[27:47] = ata_string(self.model, 20)
        w[49] = 0x0200      # LBA
        w[60] = self.sectors & 0xffff
        w[61] = self.sectors >> 16
        return w

    def command(self, cmd):
        self.error = 0
        self.irq = False
        self.busy = self.busy_reads
        if cmd == 0xec:
            self.buffer = self.identify()
            self.start_data(False)
        elif cmd == 0x20 or cmd == 0x30:
            n = self.count or 256
            if self.lba28 + n > self.sectors:
                self.status = DRDY | DSC | ERR
                self.error = 0x04
                self.irq = True
                return
            base = self.lba28 * SECTOR
            if cmd == 0x20:
                data = self.image[base:base + n * SECTOR]
                self.buffer = [
                    data[i] | data[i + 1] << 8
                    for i in range(0, len(data), 2)
                ]
                self.start_data(False)
            else:
                self.buffer = []
                self.want = n * SECTOR // 2
                self.start_data(True)
        elif cmd == 0xef:
            self.finish()
        else:
            self.status = DRDY | DSC | ERR
            self.error = 0x04
            self.irq = True

    def start_data(self, writing):
        self.pos = 0
        self.writing = writing
        self.status = DRDY | DSC | DRQ
        # the first sector of a read comes with an interrupt; a write waits
        # for its data first
        self.irq = not writing

    def finish(self):
        self.writing = False
        self.status = DRDY | DSC
        self.irq = True

    def read(self, cs1, da):
        if cs1:
            if da == 6:
                return self.status_now()
            return 0 if da == 7 else 0xff
        if da == 0:
            return self.read_data()
        if da == 1:
            return self.error
        if da == 2:
            return self.count
        if da in (3, 4, 5):
            return self.lba[da - 3]
        if da == 6:
            return self.head
        self.irq = False
        return self.status_now()

    def status_now(self):
        if self.busy:
            self.busy -= 1
            return BSY
        return self.status

    def read_data(self):
        if not (self.status & DRQ) or self.writing:
            return 0xffff
        v = self.buffer[self.pos]
        self.pos += 1
        if self.pos == len(self.buffer):
            self.finish()
        elif self.pos % (SECTOR // 2) == 0:
            self.irq = True
        return v

    def write_data(self, v):
        if not (self.status & DRQ) or not self.writing:
            return
        self.buffer.append(v)
        if len(self.buffer) % (SECTOR // 2) == 0 and len(self.buffer) < self.want:
            self.irq = True
        if len(self.buffer) == self.want:
            base = self.lba28 * SECTOR
            for i, w in enumerate(self.buffer):
                self.image[base + 2 * i] = w & 0xff
                self.image[base + 2 * i + 1] = w >> 8
            self.finish()

    def write(self, cs1, da, v):
        if cs1:
            if da == 6:
                if v & 4:
                    self.reset()
                self.nien = v >> 1 & 1
            return
        if da == 0:
            self.write_data(v)
        elif da == 1:
            self.features = v & 0xff
        elif da == 2:
            self.count = v & 0xff
        elif da in (3, 4, 5):
            self.lba[da - 3] = v & 0xff
        elif da == 6:
            self.head = v & 0xff
        else:
            self.command(v & 0xff)

    @property
    def intrq_level(self):
        return 1 if self.irq and not self.nien else 0

    def pins(self, cs0_n, cs1_n, da, dior_n, diow_n, dd_o, dd_oe):
        """The bus, after every change of the pins. Sets `dd`, `intrq` and
        `iordy` (where the card makes the host wait). A read happens at the
        falling edge of DIOR#, a write at the rising edge of DIOW#."""
        prev_dior, prev_diow = self.prev[2], self.prev[3]
        self.prev = (cs0_n, cs1_n, dior_n, diow_n)
        selected = not (cs0_n and cs1_n)
        cs1 = not cs1_n
        if selected and prev_dior and not dior_n:
            self.dd = self.read(cs1, da)
            self.log.append(('r', cs1, da, self.dd))
        if selected and not prev_diow and diow_n:
            assert dd_oe, 'a write without the bus driven'
            v = dd_o if (not cs1 and da == 0) else dd_o & 0xff
            self.log.append(('w', cs1, da, v))
            self.write(cs1, da, v)
        self.intrq = self.intrq_level
        strobe_start = (prev_dior and not dior_n) or (prev_diow and not diow_n)
        if selected and strobe_start and not cs1 and da == 0 \
                and self.data_waits:
            self.iordy = 0
            self.wait_left = self.data_waits
        elif self.wait_left:
            self.wait_left -= 1
            if not self.wait_left:
                self.iordy = 1
