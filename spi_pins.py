"""
The pins of an SPI device (the ENC28J60 board) as a register: the software
drives the three outputs and reads the two inputs, and does the SPI protocol
itself (bit banging), so a bringup needs no more than this.

Register (word addresses; the port is 2 address bits x 16 data bits):

  0  PINS  write: bit 0 CS_N, bit 1 SCLK, bit 2 MOSI (only the low byte
                  counts); all three are 1, 0, 0 after the reset
           read:  bits 2..0 what was written, bit 3 MISO, bit 4 INT (as the
                  pins are: INT of the ENC28J60 is low when it asks for
                  service)
  1..3 reserved: read as 0, writes ignored

The inputs go through a 2-FF synchronizer. A read is answered one cycle after
it was accepted, like the other devices.
"""
from amaranth import Cat, Module, Signal
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.wiring import Component, In, Out

from de0nano_spi_ethernet import SPIEtherPins
from memory_port import MemoryPort

SPI_ADDR_WIDTH = 2
REG_PINS = 0
PIN_CS_N = 1 << 0
PIN_SCLK = 1 << 1
PIN_MOSI = 1 << 2
PIN_MISO = 1 << 3
PIN_INT = 1 << 4


class SPIPins(Component):
    bus: In(MemoryPort(SPI_ADDR_WIDTH))
    pins: Out(SPIEtherPins())

    def elaborate(self, platform):
        m = Module()
        req, resp = self.bus.req, self.bus.resp

        out = Signal(3, init=1)
        miso = Signal()
        int_ = Signal()
        m.submodules.sync_miso = FFSynchronizer(self.pins.miso, miso)
        m.submodules.sync_int = FFSynchronizer(self.pins.int, int_)

        m.d.comb += [
            req.ready.eq(1),
            self.pins.cs_n.eq(out[0]),
            self.pins.sclk.eq(out[1]),
            self.pins.mosi.eq(out[2]),
        ]
        with m.If(req.valid & req.we & (req.addr == REG_PINS) & req.byte_lo):
            m.d.sync += out.eq(req.wdata[0:3])
        m.d.sync += [
            resp.valid.eq(req.valid & ~req.we),
            resp.data.eq(
                Cat(out, miso, int_) & (req.addr == REG_PINS).replicate(5)
            ),
        ]
        return m
