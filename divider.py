from amaranth import *
from amaranth.lib.wiring import Component, In, Out


class ConfigurableRadix4Divider(Component):
    """
    An iterative Radix-4 integer divider with runtime selection of:
      - width: 8-bit or 16-bit operations (`sel_8b`),
      - sign: signed 2's complement or unsigned operations (`is_signed`).
    """
    dividend: In(16)
    divisor: In(16)
    quotient: Out(16)
    remainder: Out(16)

    # control & Status
    start: In(1)
    size: In(1)  # 1 = 8-bit mode, 0 = 16-bit mode
    signed: In(1)  # 1 = signed (2's complement), 0 = unsigned
    ready: Out(1, init=1)  # 1 = ready to compute, 0 = busy
    done: Out(1)  # 1 = computation is complete
    zero_div: Out(1)  # 1 = division by zero attempted

    def elaborate(self, platform):
        m = Module()

        # internal engine registers
        acc = Signal(18)
        d = Signal(16)
        q = Signal(16)
        cnt = Signal(range(10))

        # sign tracking registers
        neg_quotient = Signal()
        neg_remainder = Signal()

        # precompute divisor multiples
        d_x2 = Signal(17)
        d_x3 = Signal(18)

        m.d.comb += [
            self.zero_div.eq(self.divisor == 0),
            self.ready.eq(cnt == 0),
            d_x2.eq(d << 1),
            d_x3.eq((d << 1) + d)
        ]

        # dynamic sign / width pre-processing (combinational)
        abs_dividend = Signal(16)
        abs_divisor = Signal(16)

        # determine signs based on mode and width configuration
        dividend_msb = Signal()
        divisor_msb = Signal()
        m.d.comb += [
            dividend_msb.eq(Mux(self.size, self.dividend[7], self.dividend[15])),
            divisor_msb.eq(Mux(self.size, self.divisor[7], self.divisor[15])),
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

        # state machine & processing loop
        with m.If(self.start & self.ready):
            # math conventions:
            # - quotient sign is negative if input signs differ
            # - remainder sign always matches the dividend's sign
            m.d.sync += [
                neg_quotient.eq(self.signed & (dividend_msb ^ divisor_msb)),
                neg_remainder.eq(self.signed & dividend_msb),
                acc.eq(0),
                self.done.eq(0),
            ]

            with m.If(self.size):
                m.d.sync += [
                    d.eq(abs_divisor[0:8]),
                    q.eq(abs_dividend[0:8] << 8),
                    cnt.eq(4)
                ]
            with m.Else():
                m.d.sync += [
                    d.eq(abs_divisor),
                    q.eq(abs_dividend),
                    cnt.eq(8)
                ]

        with m.Elif(cnt > 0):
            next_acc = Cat(q[14:16], acc)[:18]
            next_q = Cat(0, 0, q[:14])

            # radix-4 Digit Selection Table
            with m.If(next_acc >= d_x3):
                m.d.sync += [acc.eq(next_acc - d_x3), q.eq(next_q | 3)]
            with m.Elif(next_acc >= d_x2):
                m.d.sync += [acc.eq(next_acc - d_x2), q.eq(next_q | 2)]
            with m.If((next_acc >= d) & (next_acc < d_x2)):
                m.d.sync += [acc.eq(next_acc - d), q.eq(next_q | 1)]
            with m.Elif(next_acc < d):
                m.d.sync += [acc.eq(next_acc), q.eq(next_q)]

            m.d.sync += cnt.eq(cnt - 1)

            # final cycle: correct, format, and apply output signed arithmetic
            # signs
            with m.If(cnt == 1):
                m.d.sync += self.done.eq(1)

                fin_acc = Signal(18)
                fin_q = Signal(16)

                with m.If(next_acc >= d_x3):
                    m.d.comb += [fin_acc.eq(next_acc - d_x3), fin_q.eq(next_q | 3)]
                with m.Elif(next_acc >= d_x2):
                    m.d.comb += [fin_acc.eq(next_acc - d_x2), fin_q.eq(next_q | 2)]
                with m.If((next_acc >= d) & (next_acc < d_x2)):
                    m.d.comb += [fin_acc.eq(next_acc - d), fin_q.eq(next_q | 1)]
                with m.Elif(next_acc < d):
                    m.d.comb += [fin_acc.eq(next_acc), fin_q.eq(next_q)]

                # unsigned variant shapes
                raw_q = Signal(16)
                raw_r = Signal(16)
                m.d.comb += [
                    raw_q.eq(Mux(self.size, fin_q[0:8], fin_q)),
                    raw_r.eq(Mux(self.size, fin_acc[0:8], fin_acc[0:16])),
                ]

                # apply post-processing negation routines
                with m.If(self.size):
                    m.d.sync += [
                        self.quotient.eq(
                            Mux(neg_quotient, (-raw_q[0:8])[0:8], raw_q)
                        ),
                        self.remainder.eq(
                            Mux(neg_remainder, (-raw_r[0:8])[0:8], raw_r)
                        ),
                    ]
                with m.Else():
                    m.d.sync += [
                        self.quotient.eq(Mux(neg_quotient, -raw_q, raw_q)),
                        self.remainder.eq(Mux(neg_remainder, -raw_r, raw_r)),
                    ]
        with m.Else():
            m.d.sync += self.done.eq(0)

        return m
