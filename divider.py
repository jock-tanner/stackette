from amaranth import *
from amaranth.lib.wiring import Component, In, Out


class ConfigurableRadix4Divider(Component):
    """
    An iterative radix-4 integer divider (two quotient bits per clock) with
    runtime selection of:
      - width: 8-bit or 16-bit operations (`size`),
      - sign: signed 2's complement or unsigned operations (`signed`).

    A division is started by `start` while `ready` is high. `dividend`,
    `divisor`, `size` and `signed` are sampled on that clock, and need not
    stay stable afterward. `done` is high for one clock when the result is
    there; `quotient`, `remainder`, `zero_div` and `overflow` then hold until
    the next start. `done` goes high 8 clocks (16 bits) or 4 clocks (8 bits)
    after the clock that accepted `start`, and `ready` with it.

    With 8-bit operations only the low bytes of the inputs are looked at, and
    the upper byte of the results is 0.

    The quotient is truncated toward zero, and the remainder has the sign of
    the dividend, so that dividend = quotient * divisor + remainder.

    Two cases have no proper result, and a flag tells about them:
      - `zero_div`: the divisor is 0. The quotient is all ones (0xffff, or
        0xff for 8 bits) and the remainder is the dividend.
      - `overflow`: signed, the dividend is the most negative number and the
        divisor is -1, so the quotient does not fit. The quotient is the
        most negative number (0x8000 or 0x80) and the remainder is 0.
    """
    dividend: In(16)
    divisor: In(16)
    quotient: Out(16)
    remainder: Out(16)

    # control & status
    start: In(1)
    size: In(1)  # 1 = 8-bit mode, 0 = 16-bit mode
    signed: In(1)  # 1 = signed (2's complement), 0 = unsigned
    ready: Out(1, init=1)  # 1 = ready to compute, 0 = busy
    done: Out(1)  # 1 = computation is complete
    zero_div: Out(1)  # 1 = division by zero, see above
    overflow: Out(1)  # 1 = quotient overflow, see above

    def elaborate(self, platform):
        m = Module()

        # internal engine registers
        acc = Signal(18)
        d = Signal(16)
        q = Signal(16)
        cnt = Signal(range(9))
        byte_mode = Signal()

        # sign tracking registers
        neg_quotient = Signal()
        neg_remainder = Signal()

        # divisor multiples
        d_x2 = Signal(17)
        d_x3 = Signal(18)

        m.d.comb += [
            self.ready.eq(cnt == 0),
            d_x2.eq(d << 1),
            d_x3.eq((d << 1) + d),
        ]

        # dynamic sign / width pre-processing (combinational)
        abs_dividend = Signal(16)
        abs_divisor = Signal(16)

        # signs, and what is special about the operands, by width
        dividend_msb = Signal()
        divisor_msb = Signal()
        is_zero = Signal()
        is_overflow = Signal()
        with m.If(self.size):
            m.d.comb += [
                dividend_msb.eq(self.dividend[7]),
                divisor_msb.eq(self.divisor[7]),
                is_zero.eq(self.divisor[0:8] == 0),
                is_overflow.eq(
                    self.signed
                    & (self.dividend[0:8] == 0x80)
                    & (self.divisor[0:8] == 0xff)
                ),
            ]
        with m.Else():
            m.d.comb += [
                dividend_msb.eq(self.dividend[15]),
                divisor_msb.eq(self.divisor[15]),
                is_zero.eq(self.divisor == 0),
                is_overflow.eq(
                    self.signed
                    & (self.dividend == 0x8000)
                    & (self.divisor == 0xffff)
                ),
            ]

        with m.If(self.signed & dividend_msb):
            # safe truncation sizing for 8b vs 16b 2's complement inversion
            with m.If(self.size):
                m.d.comb += abs_dividend.eq((-self.dividend[0:8])[0:8])
            with m.Else():
                m.d.comb += abs_dividend.eq(-self.dividend)
        with m.Else():
            m.d.comb += abs_dividend.eq(self.dividend)

        with m.If(self.signed & divisor_msb):
            with m.If(self.size):
                m.d.comb += abs_divisor.eq((-self.divisor[0:8])[0:8])
            with m.Else():
                m.d.comb += abs_divisor.eq(-self.divisor)
        with m.Else():
            m.d.comb += abs_divisor.eq(self.divisor)

        # one radix-4 step: shift two bits of the quotient register into the
        # remainder and pick the digit 0..3 (the multiple of the divisor that
        # fits)
        next_acc = Signal(18)
        next_q = Signal(16)
        step_acc = Signal(18)
        step_q = Signal(16)
        m.d.comb += [
            next_acc.eq(Cat(q[14:16], acc)),
            next_q.eq(Cat(C(0, 2), q[:14])),
        ]
        with m.If(next_acc >= d_x3):
            m.d.comb += [step_acc.eq(next_acc - d_x3), step_q.eq(next_q | 3)]
        with m.Elif(next_acc >= d_x2):
            m.d.comb += [step_acc.eq(next_acc - d_x2), step_q.eq(next_q | 2)]
        with m.Elif(next_acc >= d):
            m.d.comb += [step_acc.eq(next_acc - d), step_q.eq(next_q | 1)]
        with m.Else():
            m.d.comb += [step_acc.eq(next_acc), step_q.eq(next_q)]

        # the result: width and sign
        raw_q = Signal(16)
        raw_r = Signal(16)
        m.d.comb += [
            raw_q.eq(Mux(byte_mode, step_q[0:8], step_q)),
            raw_r.eq(Mux(byte_mode, step_acc[0:8], step_acc[0:16])),
        ]

        # state machine & processing loop
        with m.If(self.start & self.ready):
            # math conventions:
            # - quotient sign is negative if input signs differ
            # - remainder sign always matches the dividend's sign
            m.d.sync += [
                neg_quotient.eq(self.signed & (dividend_msb ^ divisor_msb)),
                neg_remainder.eq(self.signed & dividend_msb),
                byte_mode.eq(self.size),
                self.zero_div.eq(is_zero),
                self.overflow.eq(is_overflow),
                acc.eq(0),
                self.done.eq(0),
            ]

            with m.If(self.size):
                m.d.sync += [
                    d.eq(abs_divisor[0:8]),
                    q.eq(abs_dividend[0:8] << 8),
                    cnt.eq(4),
                ]
            with m.Else():
                m.d.sync += [
                    d.eq(abs_divisor),
                    q.eq(abs_dividend),
                    cnt.eq(8),
                ]

        with m.Elif(cnt > 0):
            m.d.sync += [
                acc.eq(step_acc),
                q.eq(step_q),
                cnt.eq(cnt - 1),
            ]

            # final cycle: apply the signs and publish
            with m.If(cnt == 1):
                m.d.sync += self.done.eq(1)

                with m.If(byte_mode):
                    m.d.sync += [
                        self.quotient.eq(
                            Mux(neg_quotient, (-raw_q[0:8])[0:8], raw_q)
                        ),
                        self.remainder.eq(
                            Mux(neg_remainder, (-raw_r[0:8])[0:8], raw_r)
                        ),
                    ]
                    with m.If(self.zero_div):
                        m.d.sync += self.quotient.eq(0xff)
                with m.Else():
                    m.d.sync += [
                        self.quotient.eq(Mux(neg_quotient, -raw_q, raw_q)),
                        self.remainder.eq(Mux(neg_remainder, -raw_r, raw_r)),
                    ]
                    with m.If(self.zero_div):
                        m.d.sync += self.quotient.eq(0xffff)
        with m.Else():
            m.d.sync += self.done.eq(0)

        return m
