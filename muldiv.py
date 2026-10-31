from amaranth import *
from amaranth.lib.wiring import Component, In, Out

from divider import ConfigurableRadix4Divider


class MulDiv(Component):
    """
    The multiplier and the divider of the CPU behind one start/done handshake.

    `a` and `b` are the two operands, and the result comes as two parts:

      - multiplication (`div` low): a * b. `primary` is the low part of the
        product and `secondary` the high part.
      - division (`div` high): a / b. `primary` is the quotient and
        `secondary` the remainder.

    `size` high selects 8-bit operations (only the low bytes of `a` and `b`
    are looked at, and the parts of the result are bytes, zero-extended to
    16 bits: the low and the high byte of the 16-bit product), low selects
    16-bit ones. `signed` selects signed or unsigned numbers.

    An operation is started by `start` while `ready` is high; the inputs are
    sampled on that clock. `done` is high for one clock when the results
    are there, and they hold until the next start. A multiplication takes
    1 clock after the one that accepted `start`, a division takes what the
    divider takes (see divider.py).

    `overflow` means:
      - multiplication: the high part is significant, that is, the product
        does not fit the operand width: unsigned, the high part is not 0;
        signed, it is not the sign extension of the low part;
      - division: the divider's overflow (the most negative number divided
        by -1; the quotient is then the most negative number).
    `zero_div` tells about a division by zero (the divider's results are
    meaningless then, and the CPU does not use them).
    """
    a: In(16)
    b: In(16)
    div: In(1)
    size: In(1)
    signed: In(1)
    start: In(1)
    ready: Out(1, init=1)
    done: Out(1)
    primary: Out(16)
    secondary: Out(16)
    zero_div: Out(1)
    overflow: Out(1)

    def elaborate(self, platform):
        m = Module()

        m.submodules.divider = divider = ConfigurableRadix4Divider()

        # what the operation is, held from the start on
        is_div = Signal()
        byte_mode = Signal()
        signed_mode = Signal()
        busy = Signal()     # the multiplier has the operands, not done yet
        mul_done = Signal()

        # 17 bits hold both a signed and an unsigned 16-bit operand
        a_ext = Signal(signed(17))
        b_ext = Signal(signed(17))
        a_r = Signal(signed(17))
        b_r = Signal(signed(17))
        prod = Signal(32)
        for ext, src in ((a_ext, self.a), (b_ext, self.b)):
            with m.If(self.size):
                with m.If(self.signed):
                    m.d.comb += ext.eq(src[0:8].as_signed())
                with m.Else():
                    m.d.comb += ext.eq(src[0:8])
            with m.Else():
                with m.If(self.signed):
                    m.d.comb += ext.eq(src.as_signed())
                with m.Else():
                    m.d.comb += ext.eq(src)

        accept = Signal()
        m.d.comb += [
            accept.eq(self.start & self.ready),
            self.ready.eq(divider.ready & ~busy),
            self.done.eq(divider.done | mul_done),
            divider.dividend.eq(self.a),
            divider.divisor.eq(self.b),
            divider.size.eq(self.size),
            divider.signed.eq(self.signed),
            divider.start.eq(accept & self.div),
        ]

        m.d.sync += mul_done.eq(0)
        with m.If(accept):
            m.d.sync += [
                is_div.eq(self.div),
                byte_mode.eq(self.size),
                signed_mode.eq(self.signed),
                a_r.eq(a_ext),
                b_r.eq(b_ext),
                busy.eq(~self.div),
            ]
        with m.If(busy):
            m.d.sync += [
                prod.eq((a_r * b_r)[0:32]),
                busy.eq(0),
                mul_done.eq(1),
            ]

        # the parts of a product, and whether the high part is significant
        lo = Signal(16)
        hi = Signal(16)
        sig = Signal()
        with m.If(byte_mode):
            m.d.comb += [
                lo.eq(prod[0:8]),
                hi.eq(prod[8:16]),
                sig.eq(
                    Mux(signed_mode, hi[0:8] != Mux(prod[7], 0xff, 0), hi != 0)
                ),
            ]
        with m.Else():
            m.d.comb += [
                lo.eq(prod[0:16]),
                hi.eq(prod[16:32]),
                sig.eq(
                    Mux(signed_mode, hi != Mux(prod[15], 0xffff, 0), hi != 0)
                ),
            ]

        with m.If(is_div):
            m.d.comb += [
                self.primary.eq(divider.quotient),
                self.secondary.eq(divider.remainder),
                self.zero_div.eq(divider.zero_div),
                self.overflow.eq(divider.overflow),
            ]
        with m.Else():
            m.d.comb += [
                self.primary.eq(lo),
                self.secondary.eq(hi),
                self.overflow.eq(sig),
            ]

        return m
