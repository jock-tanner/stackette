from amaranth import *
from amaranth.lib.enum import Enum
from amaranth.lib.wiring import Component, In, Out


class OpCodes(Enum, shape=4):
    # arithmetic (adder is used)
    ADD = 0b0000
    ADC = 0b0001
    SUB = 0b0010
    SBC = 0b0011
    NEG = 0b0100

    # shifts right by one bit, the carry gets the bit that fell out
    SHR = 0b0101
    SAR = 0b0110

    # logical
    AND = 0b1000
    OR  = 0b1001
    XOR = 0b1010
    BIC = 0b1011
    NOT = 0b1100

    # word only
    SXT = 0b1101
    BSWAP = 0b1110

    # rotate right through carry, sets carry to the bit that fell out
    RRC = 0b1111


class Adder(Component):
    """
    The adder is made a distinct component to avoid the accidental synthesis
    of multiple adders.
    """
    operand_a: In(16)
    operand_b: In(16)
    carry_in: In(1)

    sum: Out(17)

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.sum.eq(self.operand_a + self.operand_b + self.carry_in)
        return m


class ALU(Component):
    operand_a: In(16)
    operand_b: In(16)
    result: Out(16)

    op_code: In(OpCodes)
    size: In(1)
    carry_in: In(1)

    carry: Out(1)
    sign: Out(1)
    zero: Out(1)
    overflow: Out(1)
    parity: Out(1)

    def elaborate(self, platform):
        m = Module()
        m.submodules.adder = adder = Adder()

        i_a = Signal(16)
        i_b = Signal(16)
        i_neg_a = Signal(16)
        i_neg_b = Signal(16)
        i_result = Signal(16)

        with m.If(self.size):
            # byte operations
            m.d.comb += [
                i_a.eq(self.operand_a & 0xff),
                i_neg_a.eq(~self.operand_a & 0xff),
                i_b.eq(self.operand_b & 0xff),
                i_neg_b.eq(~self.operand_b & 0xff),
                self.result.eq(i_result & 0xff),
                self.sign.eq(self.result[7]),
            ]
        with m.Else():
            # word operations
            m.d.comb += [
                i_a.eq(self.operand_a),
                i_neg_a.eq(~self.operand_a),
                i_b.eq(self.operand_b),
                i_neg_b.eq(~self.operand_b),
                self.result.eq(i_result),
                self.sign.eq(self.result[15]),
            ]

        m.d.comb += [
            self.zero.eq(self.result == 0),
            self.parity.eq(~self.result.xor()),
        ]

        with m.If(self.op_code.as_value()[OpCodes.as_shape().width-1]):
            # logical & other
            m.d.comb += [self.carry.eq(0), self.overflow.eq(0)]
            with m.Switch(self.op_code):
                with m.Case(OpCodes.AND):
                    m.d.comb += i_result.eq(i_a & i_b)
                with m.Case(OpCodes.OR):
                    m.d.comb += i_result.eq(i_a | i_b)
                with m.Case(OpCodes.XOR):
                    m.d.comb += i_result.eq(i_a ^ i_b)
                with m.Case(OpCodes.BIC):
                    m.d.comb += i_result.eq(i_a & i_neg_b)
                with m.Case(OpCodes.NOT):
                    m.d.comb += i_result.eq(i_neg_a)
                with m.Case(OpCodes.SXT):
                    m.d.comb += i_result.eq(Cat(i_a[:8], i_a[7].replicate(8)))
                with m.Case(OpCodes.BSWAP):
                    m.d.comb += self.result.eq(Cat(i_a[8:], i_a[:8]))
                with m.Case(OpCodes.RRC):
                    with m.If(self.size):
                        m.d.comb += i_result.eq(Cat(i_a[1:8], self.carry_in))
                    with m.Else():
                        m.d.comb += i_result.eq(Cat(i_a[1:16], self.carry_in))
                    m.d.comb += self.carry.eq(i_a[0])
        with m.Else():
            # arithmetic
            m.d.comb += i_result.eq(adder.sum[:16])
            with m.If(self.size):
                m.d.comb += [
                    self.carry.eq(adder.sum[8]),
                    self.overflow.eq(
                        (adder.operand_a[7] ^ adder.sum[7])
                        & (adder.operand_b[7] ^ adder.sum[7])
                    ),
                ]
            with m.Else():
                m.d.comb += [
                    self.carry.eq(adder.sum[16]),
                    self.overflow.eq(
                        (adder.operand_a[15] ^ adder.sum[15])
                        & (adder.operand_b[15] ^ adder.sum[15])
                    ),
                ]
            with m.Switch(self.op_code):
                with m.Case(OpCodes.ADD):
                    m.d.comb += [
                        adder.carry_in.eq(0),
                        adder.operand_a.eq(i_a),
                        adder.operand_b.eq(i_b),
                    ]
                with m.Case(OpCodes.ADC):
                    m.d.comb += [
                        adder.carry_in.eq(self.carry_in),
                        adder.operand_a.eq(i_a),
                        adder.operand_b.eq(i_b),
                    ]
                with m.Case(OpCodes.SUB):
                    m.d.comb += [
                        adder.carry_in.eq(1),
                        adder.operand_a.eq(i_a),
                        adder.operand_b.eq(i_neg_b),
                    ]
                with m.Case(OpCodes.SBC):
                    m.d.comb += [
                        adder.carry_in.eq(self.carry_in),
                        adder.operand_a.eq(i_a),
                        adder.operand_b.eq(i_neg_b),
                    ]
                with m.Case(OpCodes.NEG):
                    m.d.comb += [
                        adder.carry_in.eq(1),
                        adder.operand_a.eq(i_neg_a),
                        adder.operand_b.eq(0),
                    ]
                # the shifts do not use the adder: they override its result
                with m.Case(OpCodes.SHR, OpCodes.SAR):
                    msb = Signal()
                    m.d.comb += [
                        msb.eq(
                            Mux(
                                self.op_code == OpCodes.SAR,
                                Mux(self.size, i_a[7], i_a[15]),
                                0,
                            )
                        ),
                        self.carry.eq(i_a[0]),
                        self.overflow.eq(0),
                    ]
                    with m.If(self.size):
                        m.d.comb += i_result.eq(Cat(i_a[1:8], msb))
                    with m.Else():
                        m.d.comb += i_result.eq(Cat(i_a[1:16], msb))

        return m
