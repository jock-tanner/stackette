from amaranth import *
from amaranth.lib import enum, wiring
from amaranth.lib.wiring import In, Out


class ShiftOp(enum.Enum, shape=3):
    SLL = 0b000  # Shift Left Logical
    SRL = 0b001  # Shift Right Logical
    SRA = 0b010  # Shift Right Arithmetic
    ROL = 0b100  # Rotate Left
    ROR = 0b101  # Rotate Right


class DualWidthBarrelShifter16(wiring.Component):
    data_in: In(16)
    shamt: In(4)
    op: In(ShiftOp)
    size: In(1)      # 0 = 16-bit mode, 1 = 8-bit mode
    data_out: Out(16)

    def elaborate(self, platform):
        m = Module()

        is_left = (self.op == ShiftOp.SLL) | (self.op == ShiftOp.ROL)
        is_rotate = (self.op == ShiftOp.ROL) | (self.op == ShiftOp.ROR)
        is_arith = self.op == ShiftOp.SRA

        # 1. Select sign bit (bit 7 in 8-bit mode, bit 15 in 16-bit mode)
        sign_bit = Mux(self.size, self.data_in[7], self.data_in[15])
        fill_bit = Mux(is_arith, sign_bit, 0)

        # 2. Mask upper byte in 8-bit mode so zeros fill cleanly during right shifts
        masked_in = Signal(16)
        m.d.comb += masked_in.eq(Mux(self.size, Cat(self.data_in[:8], Const(0, 8)), self.data_in))

        # 3. Reverse only active bits for left shifts/rotations
        stage_in = Signal(16)
        with m.If(is_left):
            m.d.comb += stage_in.eq(
                Mux(self.size, Cat(masked_in[:8][::-1], Const(0, 8)), masked_in[::-1])
            )
        with m.Else():
            m.d.comb += stage_in.eq(masked_in)

        # 4. Mask shamt[3] in 8-bit mode so max shift is 7
        eff_shamt = Signal(4)
        m.d.comb += eff_shamt.eq(Mux(self.size, Cat(self.shamt[:3], Const(0, 1)), self.shamt))

        # 5. 4-stage logarithmic shift cascade (1, 2, 4, and 8 bits)
        stages = [stage_in]
        for i in range(4):
            shift_amt = 1 << i
            prev = stages[-1]
            curr = Signal(16, name=f"stage_{shift_amt}")

            wrap_bits = prev[:shift_amt]
            fill_bits = fill_bit.replicate(shift_amt)
            upper = Mux(is_rotate, wrap_bits, fill_bits)

            # In 8-bit mode, wrap bits enter at bits [8 - shift_amt : 8]
            if shift_amt < 8:
                shifted_8 = Cat(prev[shift_amt:8], upper, Const(0, 8))
            else:
                shifted_8 = prev  # Bypassed via eff_shamt[3] == 0

            shifted_16 = Cat(prev[shift_amt:], upper)
            shifted = Mux(self.size, shifted_8, shifted_16)

            m.d.comb += curr.eq(Mux(eff_shamt[i], shifted, prev))
            stages.append(curr)

        # 6. Restore bit orientation on output
        final_stage = stages[-1]
        with m.If(is_left):
            m.d.comb += self.data_out.eq(
                Mux(self.size, Cat(final_stage[:8][::-1], Const(0, 8)), final_stage[::-1])
            )
        with m.Else():
            m.d.comb += self.data_out.eq(final_stage)

        return m


if __name__ == '__main__':
    from amaranth.sim import Simulator

    dut = DualWidthBarrelShifter16()
    sim = Simulator(dut)

    async def testbench(ctx):
        # 8-bit SRA: 0x0080 >>> 2 = 0x00E0
        ctx.set(dut.data_in, 0xFF80)  # Upper byte ignored in 8-bit mode
        ctx.set(dut.shamt, 2)
        ctx.set(dut.op, ShiftOp.SRA)
        ctx.set(dut.size, 1)
        assert ctx.get(dut.data_out) == 0x00E0

        # 8-bit ROL: 0x0081 rol 1 = 0x0003
        ctx.set(dut.data_in, 0x0081)
        ctx.set(dut.shamt, 1)
        ctx.set(dut.op, ShiftOp.ROL)
        assert ctx.get(dut.data_out) == 0x0003

        # 16-bit ROL: 0x8001 rol 1 = 0x0003 (bit 15 wraps to bit 0)
        ctx.set(dut.data_in, 0x8001)
        ctx.set(dut.size, 0)
        assert ctx.get(dut.data_out) == 0x0003

    sim.add_testbench(testbench)
    sim.run()
