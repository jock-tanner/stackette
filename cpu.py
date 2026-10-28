from amaranth import *
from amaranth.lib.wiring import Component, Out, connect, flipped

from alu import ALU, OpCodes
from memory_port import MemoryPort
from memory_select import CPU_ADDR_WIDTH, Space
from stack_cache import Maint, Shift, StackCache

# Initial pointer values from the memory map, for the CPU reset logic.
IP_INIT = 0x0000
RSP_INIT = 0x0000
PSP_INIT = 0xf000
SFP_INIT = 0xf000

# Instruction word fields (see the ISA encoding table).
OP_ADD = 0b0000
OP_ADC = 0b0001
OP_SUB = 0b0010
OP_SBC = 0b0011
OP_AND = 0b0100
OP_OR = 0b0101
OP_XOR = 0b0110
OP_BIC = 0b0111
OP_LD = 0b1000
OP_ST = 0b1001
FMT_IMM8 = 0
FMT_PFP = 1
FMT_GEN = 2
MODE_REG = 0
MODE_PTR = 1
MODE_PTR_INC = 2
MODE_PTR_DEC = 3
MODE_IMM16 = 4
MODE_ABS = 5
MODE_SP = 6
REG_TOS = 0
REG_NOS = 1
REG_MBP = 2
REG_MIDX = 3
REG_SP = 4
REG_NULL = 5
REG_IP = 15
MISC_RET_N = 1
MISC_CALL_ABS = 5
MISC_JMP_ABS = 6
MISC_CALL_POP = 7
MISC_RET_VAR = 9


class CPU(Component):
    mem: Out(MemoryPort(CPU_ADDR_WIDTH))        # code and data accesses
    space: Out(Space)                           # address space of `mem` requests
    stack_mem: Out(MemoryPort(CPU_ADDR_WIDTH))  # the stack cache's spill/fill port
                                                # (always Space.PSTACK)

    # for display purpose
    high_addr: Out(4)

    def __init__(self, *, crawl=0xfff):
        super().__init__()
        self.crawl = crawl

    def elaborate(self, platform):
        m = Module()

        # parameter stack: the cache sits between the CPU core and memory.
        # The core talks to it through stack.cmd; the cache's own memory port
        # is passed straight out of the CPU as `stack_mem`.
        m.submodules.stack = stack = StackCache()
        connect(m, flipped(self.stack_mem), stack.mem)
        cmd = stack.cmd

        # memory interface
        req, resp = self.mem.req, self.mem.resp

        # programming model registers
        ip = Signal(16, init=IP_INIT)
        rsp = Signal(16, init=RSP_INIT)
        sfp = Signal(16, init=SFP_INIT)
        mbp = Signal(16)
        midx = Signal(16)
        sw = Signal(16)
        # visible to testbenches
        self.sw = sw
        self.sfp = sfp
        self.rsp = rsp

        # internal registers
        instr = Signal(16)  # the instruction word being executed
        extra = Signal(16)  # the word following the instruction
                            # (immediate value or absolute address)
        val = Signal(16)    # the operand in transit
        lane = Signal()     # address bit 0 of a byte load
        link = Signal(16)   # the caller's frame pointer, read by RET

        m.d.comb += self.space.eq(Space.CODE)

        # only for display
        m.d.comb += self.high_addr.eq(ip[12:])

        # decode
        fmt = instr[14:16]
        op = instr[10:14]
        dd = instr[8:10]
        mode = instr[4:7]
        x = instr[0:4]
        s = Signal()
        m.d.comb += s.eq((fmt != FMT_IMM8) & instr[7])

        is_imm8 = fmt == FMT_IMM8
        is_pfp = fmt == FMT_PFP
        is_gen = fmt == FMT_GEN
        is_ld = (op == OP_LD) & (fmt != 3)
        is_st = (op == OP_ST) & (fmt != 3) & (fmt != FMT_IMM8)
        is_alu = (op < OP_LD) & (fmt != 3)
        # AND OR XOR BIC; only looked at for ALU ops, so one bit is enough
        is_logic = op[2]
        # control format 11: BRA, CALL rel11, ADDP, and the misc group
        is_ctl = fmt == 3
        grp = instr[12:14]
        misc_op = instr[6:11]
        is_bra = is_ctl & (grp == 0) & ~instr[11]
        is_addp = is_ctl & (grp == 1) & (instr[10:12] == 1) & (instr[8:10] != 3)
        is_call_rel = is_ctl & (grp == 3) & ~instr[11]
        is_misc = is_ctl & (grp == 3) & instr[11]
        # RET #n drops n argument words; RET takes the count from the frame
        is_ret_n = is_misc & (misc_op == MISC_RET_N)
        is_ret_var = is_misc & (misc_op == MISC_RET_VAR)
        is_ret = is_ret_n | is_ret_var
        is_call_abs = is_misc & (misc_op == MISC_CALL_ABS)
        is_jmp_abs = is_misc & (misc_op == MISC_JMP_ABS)
        is_call_pop = is_misc & (misc_op == MISC_CALL_POP)
        is_call_direct = is_call_rel | is_call_abs
        reg_mode = is_gen & (mode == MODE_REG)
        ptr_mode = is_gen & (
                (mode == MODE_PTR)
                | (mode == MODE_PTR_INC)
                | (mode == MODE_PTR_DEC)
        )
        imm16_mode = is_gen & (mode == MODE_IMM16)
        abs_mode = is_gen & (mode == MODE_ABS)
        sp_mode = is_gen & (mode == MODE_SP)
        sp_stack = sp_mode & (x < 4)
        sp_mem = sp_mode & (x >= 4)
        needs_extra = imm16_mode | abs_mode | is_call_abs | is_jmp_abs
        mem_operand = is_pfp | ptr_mode | abs_mode | sp_mem
        ok_ld = is_ld & ~(is_gen & (mode == 7))
        ok_st = is_st & ~(is_gen & ((mode == MODE_IMM16) | (mode == 7)))
        ok_alu = is_alu & ~(is_gen & (mode == 7))
        ok_ctl = (
                is_bra | is_addp | is_call_rel | is_ret
                | is_call_abs | is_jmp_abs | is_call_pop
        )
        ok = ok_ld | ok_st | ok_alu | ok_ctl

        size = Signal(2)  # value to step midx
        m.d.comb += size.eq(Mux(s, 1, 2))

        # ---- effective address ----
        off = Signal(signed(8))
        m.d.comb += off.eq(Cat(C(0, 1), instr[0:7]))
        midx_pre = Signal(16)
        midx_eff = Signal(16)
        midx_next = Signal(16)
        m.d.comb += [
            midx_pre.eq(midx - size),
            midx_eff.eq(Mux(mode == MODE_PTR_DEC, midx_pre, midx)),
            midx_next.eq(midx),
        ]
        with m.If(ptr_mode & (mode == MODE_PTR_INC)):
            m.d.comb += midx_next.eq(midx + size)
        with m.Elif(ptr_mode & (mode == MODE_PTR_DEC)):
            m.d.comb += midx_next.eq(midx_pre)

        ea = Signal(16)
        with m.If(is_pfp):
            m.d.comb += ea.eq(sfp + off)
        with m.Elif(abs_mode):
            m.d.comb += ea.eq(extra)
        with m.Elif(ptr_mode):
            m.d.comb += ea.eq(mbp + midx_eff)
        with m.Else():
            m.d.comb += ea.eq(stack.psp + (x << 1))

        # byte distance from the cached stack window’s base to the effective
        # address: (ea - psp) modulo 2^16
        diff = Signal(16)
        m.d.comb += diff.eq(ea - stack.psp)

        # when true, the CPU flushes the cache before a load, and flushes and
        # invalidates it before a store, so memory and the cached lines agree
        in_window = diff < 8

        byte_lo = Signal()
        byte_hi = Signal()
        m.d.comb += [
            byte_lo.eq(~s | ~ea[0]),
            byte_hi.eq(~s | ea[0]),
        ]

        # ---- values ----
        imm8s = Signal(16)
        m.d.comb += imm8s.eq(instr[0:8].as_signed())

        reg_val = Signal(16)
        with m.Switch(x):
            with m.Case(REG_TOS):
                m.d.comb += reg_val.eq(stack.d0)
            with m.Case(REG_NOS):
                m.d.comb += reg_val.eq(stack.d1)
            with m.Case(REG_MBP):
                m.d.comb += reg_val.eq(mbp)
            with m.Case(REG_MIDX):
                m.d.comb += reg_val.eq(midx)
            with m.Case(REG_SP):
                m.d.comb += reg_val.eq(stack.psp)
            with m.Case(REG_IP):
                m.d.comb += reg_val.eq(ip)

        # a cached stack item by position: its value, and the `need` mask
        # that makes the cache command wait until that line is valid
        lines = Array([stack.d0, stack.d1, stack.d2, stack.d3])

        def need_of(pos):
            return Cat(pos == 0, pos == 1, pos == 2, pos == 3)

        sp_val = Signal(16)

        # one-hot mask for the stack item picked by [sp+2k] with k from 0 to 3
        sp_need = Signal(4)
        m.d.comb += [
            sp_val.eq(lines[x[0:2]]),
            sp_need.eq(need_of(x[0:2])),
        ]

        # which stack item the source reads
        ld_need = Signal(4)
        with m.If(reg_mode & (x == REG_TOS)):
            m.d.comb += ld_need.eq(0b0001)
        with m.Elif(reg_mode & (x == REG_NOS)):
            m.d.comb += ld_need.eq(0b0010)
        with m.Elif(sp_stack):
            m.d.comb += ld_need.eq(sp_need)

        src_raw = Signal(16)
        with m.If(is_imm8):
            # imm8 is sign-extended, except for the logical ops
            m.d.comb += src_raw.eq(Mux(is_logic, instr[0:8], imm8s))
        with m.Elif(imm16_mode):
            m.d.comb += src_raw.eq(extra)
        with m.Elif(reg_mode):
            m.d.comb += src_raw.eq(reg_val)
        with m.Elif(sp_stack):
            m.d.comb += src_raw.eq(sp_val)

        # the value LD delivers: a memory value was already formed (and
        # sign-extended if asked); the others are masked for 8-bit
        src = Signal(16)
        with m.If(mem_operand):
            m.d.comb += src.eq(val)
        with m.Elif(s):
            m.d.comb += src.eq(src_raw & 0xff)
        with m.Else():
            m.d.comb += src.eq(src_raw)

        # the value ST holds: for register targets zero-extended, for memory
        # replicated into both lanes so the enabled lane carries it
        val_sz = Signal(16)
        wdata = Signal(16)
        m.d.comb += [
            val_sz.eq(Mux(s, val & 0xff, val)),
            wdata.eq(Mux(s, Cat(val[0:8], val[0:8]), val)),
        ]

        # sign extension flag of byte loads through pointers
        sx = ptr_mode & s & x[0]
        byte = Signal(8)
        m.d.comb += byte.eq(Mux(lane, resp.data[8:16], resp.data[0:8]))

        # ST to a stack item through the cache
        st_pos = Signal(2)
        st_merge = sp_stack & s
        st_item = Signal(16)
        st_need = Signal(4)
        m.d.comb += [
            st_pos.eq(Mux(reg_mode, x[0], x[0:2])),
            st_item.eq(lines[st_pos]),
            st_need.eq(need_of(st_pos)),
        ]

        # ---- control flow ----
        # BRA and CALL rel11: signed offset in words from the next instruction
        rel_off = Signal(signed(12))
        m.d.comb += rel_off.eq(Cat(C(0, 1), instr[0:11]))
        addp_i = Signal(signed(8))
        m.d.comb += addp_i.eq(instr[0:8])

        # the branch or call target
        tgt = Signal(16)
        with m.If(is_bra | is_call_rel):
            m.d.comb += tgt.eq(ip + rel_off)
        with m.Elif(is_call_abs | is_jmp_abs):
            m.d.comb += tgt.eq(extra)
        with m.Else():
            m.d.comb += tgt.eq(val)

        def rstack_space():
            m.d.comb += self.space.eq(Space.RSTACK)

        def pstack_space():
            m.d.comb += self.space.eq(Space.PSTACK)

        # ---- ALU ----
        # ADD ADC SUB SBC AND OR XOR BIC take their source like LD. `dd` picks
        # the other operand and what happens to the stack: 00 TOS op= src,
        # 01 NOS op= src, 10 NOS op= src and drop TOS, 11 TOS op= src and
        # remove NOS. Arithmetic ops set C Z N V P, logical ops only Z N P.
        # A byte result is zero-extended into the destination word.
        m.submodules.alu = alu = ALU()
        # the destination item: TOS for dd 0 and 3, NOS for dd 1 and 2
        alu_pos = Signal(2)
        m.d.comb += [
            alu_pos.eq(dd[0] ^ dd[1]),
            alu.operand_a.eq(lines[alu_pos]),
            alu.operand_b.eq(src),
            alu.size.eq(s),
            alu.carry_in.eq(sw[0]),
        ]

        # two lower bits in CPU and ALU opcodes are matched, 3rd bit of CPU
        # opcode selects arithmetic/logic ops and translates to 4th bit
        # of ALU opcode
        m.d.comb += alu.op_code.eq(Cat(op[:2], C(0), op[2]))

        # delay for display purpose
        after = 'WAIT' if self.crawl else 'FETCH'

        def data_space():
            with m.If(is_pfp | sp_mem):
                m.d.comb += self.space.eq(Space.PSTACK)
            with m.Else():
                m.d.comb += self.space.eq(Space.DATA)

        # finally, the CPU control FSM
        with m.FSM(reset='INIT') as fsm:
            with m.State('INIT'):
                # the cache does not know PSP at power-up: set it once
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.SET_PSP),
                    cmd.arg.eq(PSP_INIT),
                ]
                with m.If(cmd.ready):
                    m.next = 'FETCH'

            # ---- fetch ----
            with m.State('FETCH'):
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(0),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq(ip[1:]),
                ]
                with m.If(req.ready):
                    m.next = 'FETCH_WAIT'

            with m.State('FETCH_WAIT'):
                with m.If(resp.valid):
                    m.d.sync += [
                        ip.eq(ip + 2),
                        instr.eq(resp.data),
                    ]
                    m.next = 'DECODE'

            with m.State('DECODE'):
                with m.If(~ok):
                    m.next = after
                with m.Elif(needs_extra):
                    m.next = 'EXTRA'
                with m.Else():
                    m.next = 'EXEC'

            # the extra word of imm16 and abs16 follows the instruction
            with m.State('EXTRA'):
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(0),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq(ip[1:]),
                ]
                with m.If(req.ready):
                    m.next = 'EXTRA_WAIT'

            with m.State('EXTRA_WAIT'):
                with m.If(resp.valid):
                    m.d.sync += [
                        ip.eq(ip + 2),
                        extra.eq(resp.data),
                    ]
                    m.next = 'EXEC'

            with m.State('EXEC'):
                with m.If(is_ld | is_alu):
                    # the source is fetched the same way for LD and the ALU
                    with m.If(mem_operand):
                        m.next = 'LD_COH'
                    with m.Elif(is_alu):
                        m.next = 'ALU_DELIVER'
                    with m.Else():
                        m.next = 'LD_DELIVER'
                with m.Elif(is_st):
                    m.next = 'ST_POP'
                with m.Elif(is_bra | is_jmp_abs):
                    m.d.sync += ip.eq(tgt)
                    m.next = after
                with m.Elif(is_addp):
                    with m.Switch(instr[8:10]):
                        with m.Case(0):
                            m.d.sync += mbp.eq(mbp + addp_i)
                            m.next = after
                        with m.Case(1):
                            m.d.sync += midx.eq(midx + addp_i)
                            m.next = after
                        with m.Case(2):
                            m.next = 'ADDP_SP'
                with m.Elif(is_call_pop):
                    m.next = 'CALL_POP'
                with m.Elif(is_call_direct):
                    m.next = 'CALL_RA'
                with m.Elif(is_ret):
                    m.next = 'RET_FLUSH'
                with m.Else():
                    m.next = after

            # ---- LD: memory source ----
            # keep the cache coherent with the read: flush if the address is
            # in the cached window, then wait until its write buffer is empty
            with m.State('LD_COH'):
                with m.If(in_window):
                    m.d.comb += [
                        cmd.valid.eq(1),
                        cmd.maint.eq(Maint.FLUSH),
                    ]
                    with m.If(cmd.ready):
                        m.next = 'LD_IDLE'
                with m.Else():
                    m.next = 'LD_IDLE'

            with m.State('LD_IDLE'):
                with m.If(stack.idle):
                    m.next = 'LD_RD'

            with m.State('LD_RD'):
                data_space()
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(0),
                    req.byte_hi.eq(byte_hi),
                    req.byte_lo.eq(byte_lo),
                    req.addr.eq(ea[1:]),
                ]
                with m.If(req.ready):
                    m.d.sync += [
                        lane.eq(ea[0]),
                        midx.eq(midx_next),
                    ]
                    m.next = 'LD_RD_WAIT'

            with m.State('LD_RD_WAIT'):
                with m.If(resp.valid):
                    with m.If(s):
                        m.d.sync += val.eq(
                            Cat(byte, Mux(sx, byte[7], 0).replicate(8))
                        )
                    with m.Else():
                        m.d.sync += val.eq(resp.data)
                    with m.If(is_alu):
                        m.next = 'ALU_DELIVER'
                    with m.Else():
                        m.next = 'LD_DELIVER'

            # ---- LD: put the value on the stack ----
            with m.State('LD_DELIVER'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.need.eq(ld_need),
                ]
                with m.Switch(dd):
                    with m.Case(0):
                        m.d.comb += [
                            cmd.w0_en.eq(1),
                            cmd.w0_pos.eq(0),
                            cmd.w0_data.eq(src),
                        ]
                    with m.Case(1):
                        m.d.comb += [
                            cmd.w0_en.eq(1),
                            cmd.w0_pos.eq(1),
                            cmd.w0_data.eq(src),
                        ]
                    with m.Case(2):
                        m.d.comb += [
                            cmd.shift.eq(Shift.PUSH),
                            cmd.arg.eq(src),
                        ]
                    with m.Case(3):
                        m.d.comb += [
                            cmd.shift.eq(Shift.PUSH_UNDER),
                            cmd.arg.eq(src),
                        ]
                with m.If(cmd.ready):
                    m.next = after

            # ---- ALU: result and flags in one cache command ----
            # the write uses the numbering before the shift: dd 2 writes NOS
            # and then the pop moves it to TOS, dd 3 writes TOS and then NOS
            # is removed
            with m.State('ALU_DELIVER'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.need.eq(ld_need | need_of(alu_pos)),
                    cmd.w0_en.eq(1),
                    cmd.w0_pos.eq(alu_pos),
                    cmd.w0_data.eq(alu.result),
                ]
                with m.Switch(dd):
                    with m.Case(2):
                        m.d.comb += cmd.shift.eq(Shift.POP)
                    with m.Case(3):
                        m.d.comb += cmd.shift.eq(Shift.POP_UNDER)
                with m.If(cmd.ready):
                    m.d.sync += [
                        sw[1].eq(alu.zero),
                        sw[2].eq(alu.sign),
                        sw[4].eq(alu.parity),
                    ]
                    with m.If(~is_logic):
                        m.d.sync += [
                            sw[0].eq(alu.carry),
                            sw[3].eq(alu.overflow),
                        ]
                    m.next = after

            # ---- ADDP SP: the same as storing to SP ----
            with m.State('ADDP_SP'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.SET_PSP),
                    cmd.arg.eq(stack.psp + addp_i),
                ]
                with m.If(cmd.ready):
                    m.next = after

            # ---- CALL ----
            # The return address goes on the return stack, the caller's SFP
            # becomes the first word of the new frame on the parameter stack,
            # and SFP points at it: [sfp] is the saved SFP, [sfp+2] the
            # argument count the caller pushed (it counts itself).
            # CALL pop takes its target off the stack first.
            with m.State('CALL_POP'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.need.eq(0b0001),
                    cmd.shift.eq(Shift.POP),
                ]
                with m.If(cmd.ready):
                    m.d.sync += val.eq(stack.d0)
                    m.next = 'CALL_RA'

            with m.State('CALL_RA'):
                rstack_space()
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(1),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq((rsp - 2)[1:]),
                    req.wdata.eq(ip),
                ]
                with m.If(req.ready):
                    m.d.sync += rsp.eq(rsp - 2)
                    m.next = 'CALL_LINK'

            with m.State('CALL_LINK'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.shift.eq(Shift.PUSH),
                    cmd.arg.eq(sfp),
                ]
                with m.If(cmd.ready):
                    m.d.sync += [
                        sfp.eq(stack.psp - 2),
                        ip.eq(tgt),
                    ]
                    m.next = after

            # ---- RET ----
            # Reads the saved SFP (and for the variadic RET the count) from the
            # frame in memory, so the cache is flushed first. It drops the
            # frame and the arguments by moving PSP to sfp + 2 + 2 * count,
            # restores SFP and takes the return address off the return stack.
            # RET #n has no count word in the frame: count = n, from the
            # instruction.
            with m.State('RET_FLUSH'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.FLUSH),
                ]
                with m.If(cmd.ready):
                    m.next = 'RET_IDLE'

            with m.State('RET_IDLE'):
                with m.If(stack.idle):
                    with m.If(is_ret_n):
                        m.d.sync += val.eq(instr[0:6])
                        m.next = 'RET_LNK_RD'
                    with m.Else():
                        m.next = 'RET_CNT_RD'

            with m.State('RET_CNT_RD'):
                pstack_space()
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(0),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq((sfp + 2)[1:]),
                ]
                with m.If(req.ready):
                    m.next = 'RET_CNT_WAIT'

            with m.State('RET_CNT_WAIT'):
                with m.If(resp.valid):
                    m.d.sync += val.eq(resp.data)
                    m.next = 'RET_LNK_RD'

            with m.State('RET_LNK_RD'):
                pstack_space()
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(0),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq(sfp[1:]),
                ]
                with m.If(req.ready):
                    m.next = 'RET_LNK_WAIT'

            with m.State('RET_LNK_WAIT'):
                with m.If(resp.valid):
                    m.d.sync += link.eq(resp.data)
                    m.next = 'RET_SP'

            with m.State('RET_SP'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.SET_PSP),
                    cmd.arg.eq(sfp + 2 + Cat(C(0, 1), val)),
                ]
                with m.If(cmd.ready):
                    m.d.sync += sfp.eq(link)
                    m.next = 'RET_IP_RD'

            with m.State('RET_IP_RD'):
                rstack_space()
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(0),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq(rsp[1:]),
                ]
                with m.If(req.ready):
                    m.d.sync += rsp.eq(rsp + 2)
                    m.next = 'RET_IP_WAIT'

            with m.State('RET_IP_WAIT'):
                with m.If(resp.valid):
                    m.d.sync += ip.eq(resp.data)
                    m.next = after

            # ---- ST: take the value off the stack ----
            with m.State('ST_POP'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.need.eq(Mux(dd[0], 0b0010, 0b0001)),
                ]
                with m.Switch(dd):
                    with m.Case(2):
                        m.d.comb += cmd.shift.eq(Shift.POP)
                    with m.Case(3):
                        m.d.comb += cmd.shift.eq(Shift.POP_UNDER)
                with m.If(cmd.ready):
                    m.d.sync += val.eq(Mux(dd[0], stack.d1, stack.d0))
                    m.next = 'ST_DEST'

            # ---- ST: write the destination ----
            with m.State('ST_DEST'):
                with m.If(reg_mode):
                    with m.Switch(x):
                        with m.Case(REG_TOS, REG_NOS):
                            m.next = 'ST_STK'
                        with m.Case(REG_MBP):
                            m.d.sync += mbp.eq(val_sz)
                            m.next = after
                        with m.Case(REG_MIDX):
                            m.d.sync += midx.eq(val_sz)
                            m.next = after
                        with m.Case(REG_SP):
                            m.next = 'ST_SP'
                        with m.Case(REG_IP):
                            m.d.sync += ip.eq(val_sz)
                            m.next = after
                        with m.Default():
                            m.next = after
                with m.Elif(sp_stack):
                    m.next = 'ST_STK'
                with m.Else():
                    m.next = 'ST_COH'

            # a stack item: its own command, numbered after any pop
            with m.State('ST_STK'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.w0_en.eq(1),
                    cmd.w0_pos.eq(st_pos),
                    cmd.w0_data.eq(Mux(st_merge, Cat(val[0:8], st_item[8:16]), val_sz)),
                    cmd.need.eq(Mux(st_merge, st_need, 0)),
                ]
                with m.If(cmd.ready):
                    m.next = after

            with m.State('ST_SP'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.SET_PSP),
                    cmd.arg.eq(val_sz),
                ]
                with m.If(cmd.ready):
                    m.next = after

            # memory destination: as for LD, but the stores must also drop the
            # cached copy
            with m.State('ST_COH'):
                with m.If(in_window):
                    m.d.comb += [
                        cmd.valid.eq(1),
                        cmd.maint.eq(Maint.FLUSH_INVALIDATE),
                    ]
                    with m.If(cmd.ready):
                        m.next = 'ST_IDLE'
                with m.Else():
                    m.next = 'ST_IDLE'

            with m.State('ST_IDLE'):
                with m.If(stack.idle):
                    m.next = 'ST_WR'

            with m.State('ST_WR'):
                data_space()
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(1),
                    req.byte_hi.eq(byte_hi),
                    req.byte_lo.eq(byte_lo),
                    req.addr.eq(ea[1:]),
                    req.wdata.eq(wdata),
                ]
                with m.If(req.ready):
                    m.d.sync += midx.eq(midx_next)
                    m.next = after

            if self.crawl:
                # delay for display purpose
                wait_cnt = Signal(12, init=self.crawl)
                with m.State('WAIT'):
                    with m.If(wait_cnt == 0):
                        m.d.sync += wait_cnt.eq(self.crawl)
                        m.next = 'FETCH'
                    with m.Else():
                        m.d.sync += wait_cnt.eq(wait_cnt - 1)

        return m


class CPUTop(Elaboratable):
    def elaborate(self, platform):
        from amaranth.build import Pins, Resource
        from amaranth_boards.de0nano import DE0NanoPlatform

        from de0nano_sdram import DE0NanoSDRAM
        from de0nano_spi_flash import DE0NanoSPIFlash
        from memory_arbiter import MemoryArbiter
        from memory_select import MemorySelect
        from uart import UART

        m = Module()
        m.submodules.cpu = cpu = CPU()

        # connect memory selector to memories
        m.submodules.mem_sel = mem_sel = MemorySelect()
        m.submodules.sdram = sdram = DE0NanoSDRAM()
        m.submodules.flash = flash = DE0NanoSPIFlash()
        m.submodules.uart = uart = UART(divisor=round(50_000_000 / 38_400))
        connect(m, flash.bus, mem_sel.flash)
        connect(m, sdram.bus, mem_sel.ram)
        connect(m, uart.bus, mem_sel.periph)

        # UART pins hookup
        platform.add_resources([
                Resource('tx', 0, Pins('8', dir='o', conn=('gpio', 0))),  # B4
                Resource('rx', 0, Pins('10', dir='i', conn=('gpio', 0))),  # B5
            ])
        tx_pin = platform.request('tx')
        rx_pin = platform.request('rx')
        m.d.comb += [tx_pin.o.eq(uart.pins.tx), uart.pins.rx.eq(rx_pin.i)]

        # Two initiators share the one MemorySelect port: the stack cache
        # (port0, wins) and the CPU's code/data accesses (port1). Each comes
        # with the address space its requests are made in.
        m.submodules.arb = arb = MemoryArbiter(CPU_ADDR_WIDTH, n=2)
        connect(m, cpu.stack_mem, arb.port0)
        m.d.comb += arb.space0.eq(Space.PSTACK)
        connect(m, cpu.mem, arb.port1)
        m.d.comb += arb.space1.eq(cpu.space)
        connect(m, arb.mem, mem_sel.cpu)
        m.d.comb += mem_sel.space.eq(arb.space)

        m.d.comb += [
            platform.request('led', 0).o.eq(cpu.high_addr[0]),
            platform.request('led', 1).o.eq(cpu.high_addr[1]),
            platform.request('led', 2).o.eq(cpu.high_addr[2]),
            platform.request('led', 3).o.eq(cpu.high_addr[3]),
        ]

        return m


if __name__ == '__main__':
    from amaranth_boards.de0nano import DE0NanoPlatform

    DE0NanoPlatform().build(CPUTop(), do_program=True)
