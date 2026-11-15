from amaranth import *
from amaranth.lib import enum, wiring
from amaranth.lib.wiring import In, Out


class ShiftOp(enum.Enum, shape=3):
    SLL = 0b000
    SRL = 0b001
    SRA = 0b010
    ROL = 0b100
    ROR = 0b101
    RCL = 0b110
    RCR = 0b111


class DualWidthBarrelShifter16(wiring.Component):
    """
    The shifts of the SHL, SHR and SAR instructions (and the rotates) of 16 or 8 bits.

    The count is the whole word: an immediate zero-extended, or TOS. A shift by the width
    (16, or 8 with `size`) or more saturates: the result is 0, for SRA the sign repeated.
    `carry_out` is the last bit shifted out (for SRA by the width or more the sign), and is
    to be taken into C when `carry_upd` is set, that is when the count is not 0 (and always for
    RCL and RCR). For the rotates the count is taken modulo the width and the carry is the bit
    that wrapped.
    RCL and RCR rotate through carry: the 17 bits (9 with `size`) of C and the data are
    rotated by the low 4 bits of the count, 1..16 with 0 for 16 (modulo 9 with `size`);
    `carry_in` is C and `carry_out` the new C.
    In 8-bit mode the upper byte of the input is ignored and the output is zero-extended.
    """
    data_in: In(16)
    shamt: In(16)
    op: In(ShiftOp)
    size: In(1)
    carry_in: In(1)
    data_out: Out(16)
    carry_out: Out(1)
    carry_upd: Out(1)

    def elaborate(self, platform):
        m = Module()

        is_left = (self.op == ShiftOp.SLL) | (self.op == ShiftOp.ROL)
        is_rotate = (self.op == ShiftOp.ROL) | (self.op == ShiftOp.ROR)
        is_arith = self.op == ShiftOp.SRA

        # The sign bit is bit 7 in 8-bit mode and bit 15 in 16-bit mode
        sign_bit = Mux(self.size, self.data_in[7], self.data_in[15])
        fill_bit = Mux(is_arith, sign_bit, 0)

        # The upper byte is masked in 8-bit mode so that zeros fill cleanly
        masked_in = Signal(16)
        m.d.comb += masked_in.eq(Mux(self.size, Cat(self.data_in[:8], Const(0, 8)), self.data_in))

        # Left shifts and rotates are right ones of the reversed bits
        stage_in = Signal(16)
        with m.If(is_left):
            m.d.comb += stage_in.eq(Mux(self.size, Cat(masked_in[:8][::-1], Const(0, 8)), masked_in[::-1]))
        with m.Else():
            m.d.comb += stage_in.eq(masked_in)

        # The count of the cascade: 0..15 (0..7 in 8-bit mode)
        eff_shamt = Signal(4)
        m.d.comb += eff_shamt.eq(Mux(self.size, Cat(self.shamt[:3], Const(0, 1)), self.shamt[:4]))

        # A shift by the width or more saturates
        sat = Signal()
        m.d.comb += sat.eq(Mux(self.size, self.shamt[3:] != 0, self.shamt[4:] != 0))
        at_width = Signal()
        m.d.comb += at_width.eq(Mux(self.size, self.shamt == 8, self.shamt == 16))

        # 4-stage logarithmic cascade (1, 2, 4 and 8 bits)
        stages = [stage_in]
        for i in range(4):
            shift_amt = 1 << i
            prev = stages[-1]
            curr = Signal(16, name=f'stage_{shift_amt}')

            wrap_bits = prev[:shift_amt]
            fill_bits = fill_bit.replicate(shift_amt)
            upper = Mux(is_rotate, wrap_bits, fill_bits)

            # In 8-bit mode the bits enter at [8 - shift_amt : 8]
            if shift_amt < 8:
                shifted_8 = Cat(prev[shift_amt:8], upper, Const(0, 8))
            else:
                # Never selected: eff_shamt[3] is 0 in 8-bit mode
                shifted_8 = prev

            shifted_16 = Cat(prev[shift_amt:], upper)
            shifted = Mux(self.size, shifted_8, shifted_16)

            m.d.comb += curr.eq(Mux(eff_shamt[i], shifted, prev))
            stages.append(curr)

        # Restore the bit order
        final_stage = stages[-1]
        core = Signal(16)
        with m.If(is_left):
            m.d.comb += core.eq(Mux(self.size, Cat(final_stage[:8][::-1], Const(0, 8)), final_stage[::-1]))
        with m.Else():
            m.d.comb += core.eq(final_stage)

        # Rotates through carry: the left rotation of 17 bits (9 in 8-bit mode) by n, a right
        # one by n is the left one by the width minus n
        is_rc = (self.op == ShiftOp.RCL) | (self.op == ShiftOp.RCR)
        count16 = Mux(self.shamt[:4] == 0, 16, self.shamt[:4])
        n_rc = Mux(self.size, Mux(count16 >= 9, count16 - 9, count16), count16)
        left_n = Mux(
            self.op == ShiftOp.RCL,
            n_rc,
            Mux(n_rc == 0, 0, Mux(self.size, 9, 17) - n_rc),
        )

        rc_word = Cat(self.data_in, self.carry_in)
        for i in range(5):
            k = 1 << i
            nxt = Signal(17, name=f'rc_word_{k}')
            m.d.comb += nxt.eq(Mux(left_n[i], Cat(rc_word[17 - k:], rc_word[:17 - k]), rc_word))
            rc_word = nxt
        rc_byte = Cat(self.data_in[:8], self.carry_in)
        for i in range(4):
            k = 1 << i
            nxt = Signal(9, name=f'rc_byte_{k}')
            m.d.comb += nxt.eq(Mux(left_n[i], Cat(rc_byte[9 - k:], rc_byte[:9 - k]), rc_byte))
            rc_byte = nxt
        rc_data = Mux(self.size, Cat(rc_byte[:8], Const(0, 8)), rc_word[:16])
        rc_carry = Mux(self.size, rc_byte[8], rc_word[16])

        # The result of a saturated shift: zeros, or the sign for SRA
        sat_value = Mux(
            is_arith,
            Mux(self.size, Cat(sign_bit.replicate(8), Const(0, 8)), sign_bit.replicate(16)),
            0,
        )
        m.d.comb += self.data_out.eq(Mux(is_rc, rc_data, Mux(sat & ~is_rotate, sat_value, core)))

        # The last bit shifted out is bit count - 1 to the right, bit width - count to the left
        neg = Signal(4)
        m.d.comb += neg.eq(-eff_shamt)
        left_index = Mux(self.size, Cat(neg[:3], Const(0, 1)), neg)
        right_index = Signal(4)
        m.d.comb += right_index.eq(eff_shamt - 1)
        index = Mux(is_left, left_index, right_index)
        out_bit = masked_in.bit_select(index, 1)

        # With a count of the width the bit is bit 0 to the left, the sign (bit width - 1) to the right
        sat_carry = Mux(is_arith, sign_bit, Mux(at_width, Mux(is_left, self.data_in[0], sign_bit), 0))
        top_out = Mux(self.size, self.data_out[7], self.data_out[15])
        rotate_carry = Mux(is_left, self.data_out[0], top_out)
        m.d.comb += self.carry_out.eq(
            Mux(is_rc, rc_carry, Mux(is_rotate, rotate_carry, Mux(sat, sat_carry, out_bit)))
        )
        m.d.comb += self.carry_upd.eq((self.shamt != 0) | is_rc)

        return m
