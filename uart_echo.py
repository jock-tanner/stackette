"""
UART echo circuit for the "uart" example bundled with Amaranth 0.5.x
(https://github.com/amaranth-lang/amaranth/blob/v0.5.x/examples/basic/uart.py).
"""

from amaranth import *
from uart import UART


class UARTEcho(Elaboratable):
    """
    Wraps a UART and echoes every received byte straight back out.

    A byte is copied from rx_data to tx_data and fired off as soon as
    both rx_rdy (a byte has arrived) and tx_ack (the transmitter is idle
    and ready for a new byte) are true at the same time; if a new byte
    finishes arriving while the previous echo is still transmitting,
    it simply waits - rx_rdy stays high (backpressuring the sender)
    until tx_ack frees up. On real UART hardware, with the other end
    waiting for each echo before sending the next character (as a
    terminal typically does), this is never an issue in practice.

    `rx_err` and `rx_ovf` are exposed unmodified so you can wire them to
    an LED if you want visual confirmation of framing errors or a byte
    arriving before you handled the previous rx_ack overflow.
    """
    def __init__(self, divisor, data_bits=8):
        self.uart = UART(divisor, data_bits)

        # exposed for wiring to LEDs
        self.rx_err = Signal()
        self.rx_ovf = Signal()

    def elaborate(self, platform):
        m = Module()

        uart = m.submodules.uart = self.uart

        echo = uart.rx_rdy & uart.tx_ack
        m.d.comb += [
            uart.tx_data.eq(uart.rx_data),
            uart.tx_rdy.eq(echo),
            uart.rx_ack.eq(echo),
            self.rx_err.eq(uart.rx_err),
            self.rx_ovf.eq(uart.rx_ovf),
        ]
        return m


if __name__ == '__main__':
    from amaranth.build import Pins, Resource
    from amaranth_boards.de0nano import DE0NanoPlatform

    divisor = round(50_000_000 / 38_400)


    class Top(Elaboratable):
        def elaborate(self, platform):
            m = Module()
            m.submodules.echo = echo = UARTEcho(divisor=divisor)

            platform.add_resources([
                Resource('tx', 0, Pins('8', dir='o', conn=('gpio', 0))),  # B4
                Resource('rx', 0, Pins('10', dir='i', conn=('gpio', 0))),  # B5
            ])
            tx_pin = platform.request('tx')
            rx_pin = platform.request('rx')
            rx_err_pin = platform.request('led', 0)
            rx_ovf_pin = platform.request('led', 7)

            m.d.comb += [
                tx_pin.o.eq(echo.uart.tx_o),
                echo.uart.rx_i.eq(rx_pin.i),
                rx_err_pin.o.eq(echo.rx_err),
                rx_ovf_pin.o.eq(echo.rx_ovf),
            ]
            return m

    platform = DE0NanoPlatform()
    platform.build(Top(), do_program=True)
