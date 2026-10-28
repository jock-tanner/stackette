"""
Read, write, erase and verify the SPI flash in place, through the UART to SPI
bridge (spi_bridge.py, built by de0nano_flash_programmer.py).

    python flashtool.py PORT id
    python flashtool.py PORT write FILE [--offset N] [--no-verify]
    python flashtool.py PORT read FILE [--offset N] [--length N]
    python flashtool.py PORT verify FILE [--offset N]
    python flashtool.py PORT erase [--offset N --length N]     (no range: the whole chip)

PORT is the serial port of the USB serial adapter, e.g. /dev/ttyUSB0 or COM5.
Use --baud if the programmer design was built with another baud rate. The
`pyserial` package is needed (pip install pyserial).

`write` works on 4 KiB sectors. A sector whose content is already right is
left alone; a sector that only needs bits cleared is programmed without an
erase; otherwise it is erased and only the pages that are not all 0xff are
programmed. Data outside the written range, in the same sector, is kept.
Block protection bits are cleared if they are set.

Numbers can be written as 0x1000. The chip is the MX25L8005 (1 MiB).
"""
import argparse
import sys
import time

CS_HIGH = 0x00
CS_LOW = 0x01
XFER = 0x02

CMD_WRSR = 0x01
CMD_PP = 0x02
CMD_READ = 0x03
CMD_RDSR = 0x05
CMD_WREN = 0x06
CMD_SE = 0x20
CMD_CE = 0xc7
CMD_RDID = 0x9f

SR_WIP = 0x01
SR_BP = 0x3c

SECTOR = 4096
PAGE = 256
CHIP_SIZE = 1 << 20
EXPECTED_ID = bytes([0xc2, 0x20, 0x14])

SECTOR_ERASE_TIMEOUT = 3.0
PAGE_PROGRAM_TIMEOUT = 0.2
CHIP_ERASE_TIMEOUT = 60.0


class FlashError(Exception):
    pass


class Bridge:
    """The bridge protocol over a serial port object (write, read, reset_input_buffer)."""

    def __init__(self, port):
        self.port = port

    def sync(self):
        # the bridge forgets a half-finished command after about 20 ms
        time.sleep(0.05)
        self.port.reset_input_buffer()
        self.port.write(bytes([CS_HIGH]))
        if self.port.read(1) != bytes([CS_HIGH]):
            raise FlashError(
                'no answer from the bridge: is the programmer design loaded, '
                'and is the baud rate the same?'
            )

    def transaction(self, data):
        """CS low, the bytes of data shifted out, CS high -> the bytes shifted in."""
        packet = bytearray([CS_LOW])
        for i in range(0, len(data), 256):
            chunk = data[i:i + 256]
            packet += bytes([XFER, len(chunk) & 0xff]) + chunk
        packet.append(CS_HIGH)
        self.port.write(bytes(packet))
        answer = self.port.read(len(data) + 2)
        if len(answer) != len(data) + 2 or answer[0] != CS_LOW or answer[-1] != CS_HIGH:
            raise FlashError('the bridge answer is damaged or missing')
        return answer[1:-1]


class Flash:
    def __init__(self, bridge, log=lambda text: None):
        self.bridge = bridge
        self.log = log

    def jedec_id(self):
        return self.bridge.transaction(bytes([CMD_RDID, 0, 0, 0]))[1:]

    def status(self):
        return self.bridge.transaction(bytes([CMD_RDSR, 0]))[1]

    def wait_ready(self, timeout):
        end = time.monotonic() + timeout
        while self.status() & SR_WIP:
            if time.monotonic() > end:
                raise FlashError('the flash stays busy')

    def write_enable(self):
        self.bridge.transaction(bytes([CMD_WREN]))

    def unprotect(self):
        if self.status() & SR_BP:
            self.log('clearing the block protection bits')
            self.write_enable()
            self.bridge.transaction(bytes([CMD_WRSR, 0]))
            self.wait_ready(PAGE_PROGRAM_TIMEOUT)
            if self.status() & SR_BP:
                raise FlashError('the block protection bits cannot be cleared (WP# low?)')

    def read(self, addr, length):
        out = bytearray()
        while length:
            n = min(length, 256)
            a = addr + len(out)
            out += self.bridge.transaction(
                bytes([CMD_READ, a >> 16 & 0xff, a >> 8 & 0xff, a & 0xff]) + bytes(n)
            )[4:]
            length -= n
        return bytes(out)

    def sector_erase(self, addr):
        self.write_enable()
        self.bridge.transaction(bytes([CMD_SE, addr >> 16 & 0xff, addr >> 8 & 0xff, addr & 0xff]))
        self.wait_ready(SECTOR_ERASE_TIMEOUT)

    def chip_erase(self):
        self.write_enable()
        self.bridge.transaction(bytes([CMD_CE]))
        self.wait_ready(CHIP_ERASE_TIMEOUT)

    def page_program(self, addr, data):
        assert 0 < len(data) <= PAGE and addr % PAGE + len(data) <= PAGE
        self.write_enable()
        self.bridge.transaction(
            bytes([CMD_PP, addr >> 16 & 0xff, addr >> 8 & 0xff, addr & 0xff]) + data
        )
        self.wait_ready(PAGE_PROGRAM_TIMEOUT)

    def erase_range(self, offset, length):
        first = offset // SECTOR * SECTOR
        for addr in range(first, offset + length, SECTOR):
            self.log(f'erase {addr:#08x}')
            self.sector_erase(addr)

    def write(self, offset, data):
        """Write data at offset, touching as little as possible. -> (erased, programmed) counts."""
        if offset < 0 or offset + len(data) > CHIP_SIZE:
            raise FlashError('the data does not fit in the chip')
        self.unprotect()
        erased = programmed = 0
        first = offset // SECTOR * SECTOR
        for base in range(first, offset + len(data), SECTOR):
            old = self.read(base, SECTOR)
            new = bytearray(old)
            lo = max(offset, base)
            hi = min(offset + len(data), base + SECTOR)
            new[lo - base:hi - base] = data[lo - offset:hi - offset]
            new = bytes(new)
            if new == old:
                self.log(f'{base:#08x} is up to date')
                continue
            # programming can only turn 1 into 0
            if all(n & ~o & 0xff == 0 for n, o in zip(new, old)):
                self.log(f'{base:#08x} programmed without an erase')
                pages = [p for p in range(0, SECTOR, PAGE) if new[p:p + PAGE] != old[p:p + PAGE]]
            else:
                self.log(f'{base:#08x} erased and programmed')
                self.sector_erase(base)
                erased += 1
                pages = [p for p in range(0, SECTOR, PAGE) if new[p:p + PAGE] != b'\xff' * PAGE]
            for p in pages:
                self.page_program(base + p, new[p:p + PAGE])
                programmed += 1
        return erased, programmed


def open_port(name, baud):
    try:
        import serial
    except ImportError:
        sys.exit('the pyserial package is needed: pip install pyserial')
    return serial.Serial(name, baud, timeout=2)


def number(text):
    return int(text, 0)


def main(argv=None, port=None):
    ap = argparse.ArgumentParser(description='SPI flash programmer')
    ap.add_argument('port')
    ap.add_argument('command', choices=['id', 'write', 'read', 'verify', 'erase'])
    ap.add_argument('file', nargs='?')
    ap.add_argument('--offset', type=number, default=0)
    ap.add_argument('--length', type=number)
    ap.add_argument('--baud', type=number, default=115200)
    ap.add_argument('--no-verify', action='store_true')
    args = ap.parse_args(argv)
    if args.command in ('write', 'read', 'verify') and not args.file:
        ap.error(f'{args.command} needs a file')

    if port is None:
        port = open_port(args.port, args.baud)
    flash = Flash(Bridge(port), log=print)
    try:
        flash.bridge.sync()
        ident = flash.jedec_id()
        if ident != EXPECTED_ID:
            raise FlashError(
                f'unexpected flash ID {ident.hex()} (expected {EXPECTED_ID.hex()}): '
                'check the wiring and the power'
            )
        if args.command == 'id':
            print(f'flash ID {ident.hex()}, status {flash.status():#04x}')
        elif args.command == 'read':
            length = args.length if args.length is not None else CHIP_SIZE - args.offset
            data = flash.read(args.offset, length)
            with open(args.file, 'wb') as f:
                f.write(data)
            print(f'read {len(data)} bytes')
        elif args.command in ('write', 'verify'):
            with open(args.file, 'rb') as f:
                data = f.read()
            if args.command == 'write':
                erased, programmed = flash.write(args.offset, data)
                print(f'{erased} sectors erased, {programmed} pages programmed')
            if args.command == 'verify' or not args.no_verify:
                back = flash.read(args.offset, len(data))
                if back != data:
                    bad = next(i for i in range(len(data)) if back[i] != data[i])
                    raise FlashError(f'verify failed at {args.offset + bad:#08x}')
                print('verified')
        elif args.command == 'erase':
            flash.unprotect()
            if args.length is None:
                flash.chip_erase()
            else:
                flash.erase_range(args.offset, args.length)
            print('erased')
    except FlashError as e:
        sys.exit(f'error: {e}')
    return 0


if __name__ == '__main__':
    main()
