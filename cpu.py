from amaranth import *
from amaranth.lib.wiring import Component, In, Out, connect, flipped

from alu import ALU, OpCodes
from memory_port import MemoryPort
from muldiv import MulDiv
from memory_select import CPU_ADDR_WIDTH, Space
from shifter import DualWidthBarrelShifter16, ShiftOp
from stack_cache import Maint, Shift, StackCache

# Initial pointer values from the memory map, for the CPU reset logic.
IP_INIT = 0x0000
RSP_INIT = 0x0000
PSP_INIT = 0xf000
PFP_INIT = 0xf000

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
OP_SWAP = 0b1010
OP_LEA = 0b1011
OP_CMP = 0b1100
OP_TST = 0b1101
FMT_IMM8 = 0
FMT_PFP = 1
FMT_GEN = 2
REG_NULL = 0
REG_SP = 1
REG_MBP = 2
REG_MIDX = 3
REG_SR = 6
REG_IP = 7

# the status register bits that exist: C Z N V P. The others read as zero
# and ignore writes.
SR_MASK = 0b11111

# unary operations, format 11 group 2: 11 10 00 dd s oooo 000
UOP_NOT = 0
UOP_NEG = 1
UOP_SXT = 2
UOP_BSWAP = 3
MISC_NOP = 0
MISC_RET_N = 1
MISC_RETI = 2
MISC_TRAP = 4
MISC_CALL_ABS = 5
MISC_JMP_ABS = 6
MISC_CALL_POP = 7
MISC_RET_VAR = 9

# The interrupt ID register tells the system context why it is running: the
# class in bits 7:6, the number in bits 5:0.
ID_RESET = 0 << 6   # power-up, the number is 0
ID_IRQ = 1 << 6     # a hardware interrupt, the number is the interrupt pin
ID_TRAP = 2 << 6    # TRAP p, the number is p
ID_FAULT = 3 << 6   # an exception, the number is one of these:
FAULT_ILLEGAL = 0   # an instruction that does not exist, or RETI in user mode
FAULT_MEM = 1       # a request of the CPU was refused
FAULT_STACK = 2     # a fill of the stack cache was refused
FAULT_WB = 3        # a write-back of the stack cache was refused (asynchronous)
FAULT_DIV = 4       # division by zero

# the registers of the inactive context and the exception data, as the CPU
# shows them on its `ctx_*` port (word numbers)
CTX_IP = 0
CTX_RSP = 1
CTX_PFP = 2
CTX_MBP = 3
CTX_MIDX = 4
CTX_SW = 5
CTX_PSP = 6
CTX_ID = 7          # read only from here on
CTX_FAULT_IP = 8
CTX_FAULT_ADDR = 9


class CPU(Component):
    mem: Out(MemoryPort(CPU_ADDR_WIDTH))        # code and data accesses
    space: Out(Space)                           # address space of `mem` requests
    stack_mem: Out(MemoryPort(CPU_ADDR_WIDTH))  # the stack cache's spill/fill port
                                                # (always Space.PSTACK)

    # A fault is the memory's way to refuse a request (no memory there, page
    # not mapped): it is high in the cycle the request is presented, instead of
    # req.ready. The instruction is then abandoned, with the registers and the
    # stack as they were before it, and fetched again (see ABORT).
    # `stack_mem_fault` is the same for the fill reads of the stack cache;
    # write-backs must not fault.
    mem_fault: In(1)
    stack_mem_fault: In(1)
    aborted: Out(1)     # one cycle, when an instruction has been abandoned

    # SWAP reads a word and writes another one back to the same address; the
    # memory is held for the CPU from the read to the write, so that nothing
    # else (the stack cache) gets in between
    mem_lock: Out(1)

    # The user flag tells which context is active: 0 system, 1 user. No
    # instruction can read it. The registers of the other context (IP, RSP,
    # PFP, MBP, MIDX, SW and PSP) and the exception data are reachable by
    # word number through this port, which the memory-mapped context device
    # drives. Writes go to the inactive context only; the registers after
    # CTX_PSP are read only.
    user: Out(1)

    # An interrupt request, a level, from the interrupt controller with the
    # number of the line. It is taken in the user context, between two
    # instructions; in the system context it waits.
    irq: In(1)
    irq_num: In(6)

    # high while the system context is being reset (a soft reset), so that
    # the MMU puts the default map back into its system registers
    soft_reset: Out(1)
    ctx_addr: In(4)
    ctx_we: In(1)
    ctx_wdata: In(16)
    ctx_rdata: Out(16)

    def __init__(self, *, user_ip_init=IP_INIT, prefetch=True):
        """
        prefetch: fetch the next word of code while an instruction that does
        not use the memory executes, see the "prefetch" comment in
        elaborate.
        """
        super().__init__()
        self.user_ip_init = user_ip_init
        self.prefetch = prefetch

    def elaborate(self, platform):
        m = Module()

        # parameter stack: the cache sits between the CPU core and memory.
        # The core talks to it through stack.cmd; the cache's own memory port
        # is passed straight out of the CPU as `stack_mem`.
        m.submodules.stack = stack = StackCache()
        connect(m, flipped(self.stack_mem), stack.mem)
        m.d.comb += stack.mem_fault.eq(self.stack_mem_fault)
        cmd = stack.cmd

        # memory interface
        req, resp = self.mem.req, self.mem.resp

        # programming model registers
        ip = Signal(16, init=IP_INIT)
        rsp = Signal(16, init=RSP_INIT)
        pfp = Signal(16, init=PFP_INIT)
        mbp = Signal(16)
        midx = Signal(16)
        sw = Signal(16)

        # the inactive context. RETI and TRAP (and the exceptions) exchange
        # it with the registers above; PSP lives in the stack cache.
        b_ip = Signal(16, init=self.user_ip_init)
        b_rsp = Signal(16, init=RSP_INIT)
        b_pfp = Signal(16, init=PFP_INIT)
        b_mbp = Signal(16)
        b_midx = Signal(16)
        b_sw = Signal(16)
        b_psp = Signal(16, init=PSP_INIT)
        user = Signal()
        m.d.comb += self.user.eq(user)

        # why the system context runs, and what is known about an exception
        intr_id = Signal(16, init=ID_RESET)
        fault_ip = Signal(16)       # where the instruction that failed starts
        fault_addr = Signal(16)     # the byte address of the refused request
        exc_code = Signal(16)       # the ID to be delivered

        # a refused write-back of the stack cache, until it is taken
        pend_wb = Signal()
        wb_addr = Signal(16)

        # visible to testbenches
        self.intr_id = intr_id
        self.sw = sw
        self.pfp = pfp
        self.rsp = rsp

        # internal registers
        instr = Signal(16)  # the instruction word being executed
        extra = Signal(16)  # the word following the instruction
                            # (immediate value or absolute address)
        val = Signal(16)    # the operand in transit
        swapval = Signal(16)    # SWAP: the stack item that goes to memory
        ptr = Signal(16)        # [tos]: the address, taken from TOS
        lane = Signal()     # address bit 0 of a byte load
        # A word access at an odd address in the data space is made as two
        # byte accesses: the high lane of the word it starts in, then the
        # low lane of the next word. `ph2` is high during the second one,
        # `part` keeps the first byte of a load.
        ph2 = Signal()
        part = Signal(8)
        link = Signal(16)   # the caller's frame pointer, read by RET
        istart = Signal(16, init=IP_INIT)   # where the instruction being
                                            # executed starts (for a skipped
                                            # one, the SKIP before it)

        # set by SKIP: the next instruction is fetched but not executed
        skipping = Signal()

        m.d.comb += self.space.eq(Space.CODE)

        # decode
        fmt = instr[14:16]
        op = instr[10:14]
        dd = instr[8:10]
        # the operand field of the GENERAL format, the low 7 bits, is a
        # prefix code (`s` is bit 7, outside it):
        #   000 rrrr    register r
        #   001 0 mm e  mm: 0 [mbp+midx], 1 [mbp+midx++], 2 [mbp+--midx],
        #               3 [tos]; e: a byte load sign-extends
        #   01 0000 e   [abs16], e as above
        #   01 0001 0   #imm16
        #   1 kkkkk e   [sp+k]: item k of the stack, memory from k = 4 on
        #               (e as above, for k < 4 it must be 0)
        # the other codes are illegal
        x = instr[0:4]
        e = instr[0]
        s = Signal()
        m.d.comb += s.eq((fmt != FMT_IMM8) & instr[7])

        is_imm8 = fmt == FMT_IMM8
        is_pfp = fmt == FMT_PFP
        is_gen = fmt == FMT_GEN
        is_ld = (op == OP_LD) & (fmt != 3)
        is_st = (op == OP_ST) & (fmt != 3) & (fmt != FMT_IMM8)
        # CMP and TST are SUB and AND that do not write the result
        is_cmp = ((op == OP_CMP) | (op == OP_TST)) & (fmt != 3)
        is_alu = ((op < OP_LD) | is_cmp) & (fmt != 3)
        is_swap = (op == OP_SWAP) & ((fmt == FMT_PFP) | (fmt == FMT_GEN))
        # LEA: the address of a frame item, PFP + 2*simm7 as in a PFP
        # operand, goes on the stack like a loaded value (`s` must be 0)
        is_lea = (op == OP_LEA) & (fmt == FMT_PFP)

        # AND OR XOR BIC; only looked at for ALU ops, so one bit is enough
        is_logic = Mux(is_cmp, op[0], op[2])

        # control format 11: BRA, CALL rel11, ADDP, and the misc group
        is_ctl = fmt == 3
        grp = instr[12:14]
        misc_op = instr[6:11]
        is_bra = is_ctl & (grp == 0) & ~instr[11]
        is_skip = is_ctl & (grp == 0) & instr[11] & ~instr[10]

        # SETB and CLRB: 11 01 00 0o iiiiiiii
        is_setclr = is_ctl & (grp == 1) & (instr[10:12] == 0) & ~instr[9]
        is_addp = is_ctl & (grp == 1) & (instr[10:12] == 1) & (instr[8:10] != 3)

        # unary operations: 11 10 00 dd s oooo 000, `dd` and `s` as usual
        is_unary = is_ctl & (grp == 2) & (instr[10:12] == 0)
        u_op = instr[3:7]

        # shifts and rotates: 11 10 1v dd s kkk cccc. `dd` and `s` are as
        # usual and `kkk` is shifter.ShiftOp (0 SHL, 1 SHR, 2 SAR, 4 ROL,
        # 5 ROR, 6 RCL, 7 RCR). With v = 0 the count is `cccc`, 0 for 16, and
        # `dd` works as in the unary operations. With v = 1 (`cccc` = 0) the
        # count is TOS and `dd` works as in the ALU operations with TOS as the
        # source. The item is the same for both: TOS for dd 0 and 3, NOS for
        # dd 1 and 2.
        is_shift = is_ctl & (grp == 2) & instr[11]
        sh_var = instr[10]
        ok_shift = (
                is_shift & (instr[4:7] != 3) & ~(sh_var & (instr[0:4] != 0))
        )

        # multiplication and division: 11 10 01 d g s 0000000, `d` selects
        # division, `g` signed numbers and `s` bytes
        is_muldiv = (
                is_ctl & (grp == 2) & (instr[10:12] == 1) & (instr[0:7] == 0)
        )
        md_div = instr[9]
        md_signed = instr[8]
        is_call_rel = is_ctl & (grp == 3) & ~instr[11]
        is_misc = is_ctl & (grp == 3) & instr[11]

        # RET #n drops n argument words; RET takes the count from the frame
        is_ret_n = is_misc & (misc_op == MISC_RET_N)
        is_ret_var = is_misc & (misc_op == MISC_RET_VAR)
        is_ret = is_ret_n | is_ret_var
        is_call_abs = is_misc & (misc_op == MISC_CALL_ABS)
        is_jmp_abs = is_misc & (misc_op == MISC_JMP_ABS)
        is_call_pop = is_misc & (misc_op == MISC_CALL_POP)
        is_reti = is_misc & (misc_op == MISC_RETI)
        is_trap = is_misc & (misc_op == MISC_TRAP)
        # NOP is 0xf800, and nothing else of its op
        is_nop = is_misc & (misc_op == MISC_NOP) & (instr[0:6] == 0)
        is_call_direct = is_call_rel | is_call_abs
        op_field = instr[0:7]
        # registers: 000 0rrr, the codes 4 and 5 and those from 8 are reserved
        reg_mode = is_gen & (op_field[4:7] == 0b000)
        reg_ok = reg_mode & ~op_field[3] & (~op_field[2] | op_field[1])
        ptr_group = is_gen & (op_field[3:7] == 0b0010)
        ptr_kind = op_field[1:3]
        # the string forms 001 1 mm e: the same, and the instruction repeats
        # until MIDX is 0; only the modes 1 and 2 exist
        str_group = is_gen & (op_field[3:7] == 0b0011)
        str_inc = str_group & (ptr_kind == 1)
        str_dec = str_group & (ptr_kind == 2)
        str_ptr = str_inc | str_dec
        # 01 0010 e: [abs16] with the repeat: the address stays, MIDX is the
        # byte count and goes down by the size
        str_abs = is_gen & (op_field[1:7] == 0b010010)
        str_mode = str_ptr | str_abs
        ptr_mode = (ptr_group & (ptr_kind != 3)) | str_ptr
        ptr_inc = (ptr_group & (ptr_kind == 1)) | str_inc
        ptr_dec = (ptr_group & (ptr_kind == 2)) | str_dec
        # [tos]: the address is TOS
        tos_ptr = ptr_group & (ptr_kind == 3)
        abs_mode = is_gen & (op_field[1:7] == 0b010000)
        abs_any = abs_mode | str_abs
        imm16_mode = is_gen & (op_field == 0b0100010)
        sp_mode = is_gen & op_field[6]
        sp_k = op_field[1:6]
        sp_stack = sp_mode & (sp_k < 4)
        sp_mem = sp_mode & (sp_k >= 4)
        needs_extra = imm16_mode | abs_any | is_call_abs | is_jmp_abs
        mem_operand = (
                (is_pfp & ~is_lea) | ptr_mode | abs_any | sp_mem | tos_ptr
        )
        # the operands that exist: an item of the stack has no sign flag
        gen_ok = (
                reg_ok | ptr_group | str_mode | abs_mode | imm16_mode
                | (sp_mode & ~(sp_stack & e))
        )
        ok_ld = is_ld & (~is_gen | gen_ok)
        # ST has no immediate, and through [tos] it takes its value from NOS:
        # `nos` or `under`
        ok_st = is_st & (
                ~is_gen | (
                    (gen_ok & ~imm16_mode & ~tos_ptr)
                    | (tos_ptr & dd[0])
                )
        )
        ok_alu = is_alu & (~is_gen | gen_ok)
        ok_lea = is_lea & ~instr[7]
        # SWAP exchanges TOS or NOS with a word or a byte of memory
        ok_swap = is_swap & ~dd[1] & mem_operand

        # SXT and BSWAP are word operations
        ok_unary = (
                is_unary & ~u_op[2] & ~u_op[3]
                & ~(s & ((u_op == UOP_SXT) | (u_op == UOP_BSWAP)))
        )
        ok_ctl = (
                is_bra | is_skip | is_setclr | is_addp | ok_unary | is_call_rel
                | is_ret
                | is_call_abs | is_jmp_abs | is_call_pop
                # RETI is an illegal instruction in user mode
                | (is_reti & ~user) | is_trap
                | is_muldiv | is_nop | ok_shift
        )
        ok = ok_ld | ok_st | ok_alu | ok_swap | ok_lea | ok_ctl

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
            midx_eff.eq(Mux(ptr_dec, midx_pre, midx)),
            midx_next.eq(midx),
        ]
        with m.If(ptr_inc):
            m.d.comb += midx_next.eq(midx + size)
        with m.Elif(ptr_dec | str_abs):
            m.d.comb += midx_next.eq(midx_pre)

        ea = Signal(16)
        with m.If(is_pfp):
            m.d.comb += ea.eq(pfp + off)
        with m.Elif(abs_any):
            m.d.comb += ea.eq(extra)
        with m.Elif(ptr_mode):
            m.d.comb += ea.eq(mbp + midx_eff)
        with m.Elif(tos_ptr):
            m.d.comb += ea.eq(ptr)
        with m.Else():
            m.d.comb += ea.eq(stack.psp + (sp_k << 1))

        # byte distance from the cached stack window’s base to the effective
        # address: (ea - psp) modulo 2^16
        diff = Signal(16)
        m.d.comb += diff.eq(ea - stack.psp)

        # a word at an odd address (only the abs16, [mbp+midx] and [tos]
        # operands can have one: PFP and SP offsets are even)
        split = Signal()
        m.d.comb += split.eq(~s & ea[0])

        # when true, the CPU flushes the cache before a load, and flushes and
        # invalidates it before a store, so memory and the cached lines agree;
        # the second byte of an odd word may be the one at PSP
        in_window = (diff < 8) | (split & (diff == 0xffff))

        byte_lo = Signal()
        byte_hi = Signal()
        waddr = Signal(15)
        m.d.comb += [
            byte_lo.eq(Mux(split, ph2, ~s | ~ea[0])),
            byte_hi.eq(Mux(split, ~ph2, ~s | ea[0])),
            waddr.eq(ea[1:] + ph2),
        ]

        # ---- values ----
        imm8s = Signal(16)
        m.d.comb += imm8s.eq(instr[0:8].as_signed())

        reg_val = Signal(16)
        with m.Switch(x):
            with m.Case(REG_MBP):
                m.d.comb += reg_val.eq(mbp)
            with m.Case(REG_MIDX):
                m.d.comb += reg_val.eq(midx)
            with m.Case(REG_SP):
                m.d.comb += reg_val.eq(stack.psp)
            with m.Case(REG_SR):
                m.d.comb += reg_val.eq(sw & SR_MASK)
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
            sp_val.eq(lines[sp_k[0:2]]),
            sp_need.eq(need_of(sp_k[0:2])),
        ]

        # which stack item the source reads
        ld_need = Signal(4)
        with m.If(sp_stack):
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
        with m.If(is_lea):
            m.d.comb += src.eq(ea)
        with m.Elif(mem_operand):
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
            wdata.eq(Mux(
                split,
                Mux(ph2, val[8:16], Cat(C(0, 8), val[0:8])),
                Mux(s, Cat(val[0:8], val[0:8]), val),
            )),
        ]

        # what SWAP writes: byte stores go to both lanes, like ST's
        swap_wdata = Signal(16)
        m.d.comb += swap_wdata.eq(Mux(
            split,
            Mux(ph2, swapval[8:16], Cat(C(0, 8), swapval[0:8])),
            Mux(s, Cat(swapval[0:8], swapval[0:8]), swapval),
        ))

        # sign extension flag of byte loads through pointers
        sx = (ptr_mode | tos_ptr | abs_any | sp_mem) & s & e & ~is_swap
        byte = Signal(8)
        m.d.comb += byte.eq(Mux(lane, resp.data[8:16], resp.data[0:8]))

        # ST to a stack item through the cache
        st_pos = Signal(2)
        st_merge = sp_stack & s
        st_item = Signal(16)
        st_need = Signal(4)
        m.d.comb += [
            st_pos.eq(sp_k[0:2]),
            st_item.eq(lines[st_pos]),
            st_need.eq(need_of(st_pos)),
        ]
        # the position of the destination item before the pop (`st_pos` is
        # numbered after it): a byte store reads its old word, and that is
        # fetched with the pop's command, before the pop. The item that is
        # number 3 after a pop is not in the window yet (st_pre is 4); it
        # is fetched later and a fault there is undone.
        st_pre = Signal(3)
        with m.If(dd[1] & ~((dd == 3) & (st_pos == 0))):
            m.d.comb += st_pre.eq(st_pos + 1)
        with m.Else():
            m.d.comb += st_pre.eq(st_pos)

        # ---- SKIP condition ----
        # the flags in `sw`: C, Z, N, V, P (even parity)
        flag_c, flag_z, flag_n, flag_v, flag_p = sw[0], sw[1], sw[2], sw[3], sw[4]
        conditions = Array([
            flag_z,                                 # 0 EQ
            ~flag_z,                                # 1 NE
            flag_c,                                 # 2 CS (unsigned >=)
            ~flag_c,                                # 3 CC (unsigned <)
            flag_n,                                 # 4 MI
            ~flag_n,                                # 5 PL
            flag_v,                                 # 6 VS
            ~flag_v,                                # 7 VC
            flag_c & ~flag_z,                       # 8 HI (unsigned >)
            ~flag_c | flag_z,                       # 9 LS (unsigned <=)
            flag_n == flag_v,                       # 10 GE
            flag_n != flag_v,                       # 11 LT
            ~flag_z & (flag_n == flag_v),           # 12 GT
            flag_z | (flag_n != flag_v),            # 13 LE
            flag_p,                                 # 14 PE (parity even)
            ~flag_p,                                # 15 PO (parity odd)
        ])
        cond = Signal()
        m.d.comb += cond.eq(conditions[instr[6:10]])

        # ---- control flow ----
        # BRA and CALL rel11: signed offset in words from the next instruction
        rel_off = Signal(signed(12))
        m.d.comb += rel_off.eq(Cat(C(0, 1), instr[0:11]))
        addp_i = Signal(signed(8))
        m.d.comb += addp_i.eq(instr[0:8])

        # the branch or call target
        # the relative target is added up while DECODE looks at the
        # instruction (IP already points past it), so the adder is not on
        # the path from the instruction register to IP
        tgt_rel = Signal(16)
        tgt = Signal(16)
        with m.If(is_bra | is_call_rel):
            m.d.comb += tgt.eq(tgt_rel)
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
        m.submodules.muldiv = md = MulDiv()
        m.submodules.shifter = sh = DualWidthBarrelShifter16()
        md_a = Signal(16)       # MUL, DIV: NOS
        md_b = Signal(16)       # MUL, DIV: TOS

        # the destination item: TOS for dd 0 and 3, NOS for dd 1 and 2. The
        # unary operations use the same item as their operand. CMP and TST
        # compare TOS for dd 0 and 2, NOS for dd 1 and 3, like ST, and the
        # pop of dd 2 and the removal of dd 3 follow.
        alu_pos = Signal(2)
        m.d.comb += [
            alu_pos.eq(Mux(is_cmp, dd[0], dd[0] ^ dd[1])),
            alu.operand_a.eq(lines[alu_pos]),
            alu.size.eq(s),
            alu.carry_in.eq(sw[0]),
            md.a.eq(md_a),
            md.b.eq(md_b),
            md.div.eq(md_div),
            md.size.eq(s),
            md.signed.eq(md_signed),
        ]
        # the shifter: the ALU item, shifted by TOS or by the immediate count
        # (cccc, 0 for 16)
        m.d.comb += [
            sh.data_in.eq(lines[alu_pos]),
            sh.shamt.eq(Mux(sh_var, stack.d0, Cat(instr[0:4], instr[0:4] == 0))),
            sh.size.eq(s),
            sh.carry_in.eq(sw[0]),
            sh.op.eq(instr[4:7]),
        ]
        sh_n = Signal()
        m.d.comb += sh_n.eq(Mux(s, sh.data_out[7], sh.data_out[15]))
        # the unary operations are done by the same ALU: they have no source
        # operand
        with m.If(is_unary):
            m.d.comb += alu.operand_b.eq(lines[alu_pos])
        with m.Else():
            m.d.comb += alu.operand_b.eq(src)
        # which flags the operation sets besides Z N P: NEG sets C and V, NOT
        # SXT and BSWAP neither
        set_c = Signal()
        set_v = Signal()
        with m.If(is_unary):
            m.d.comb += [
                set_c.eq(u_op == UOP_NEG),
                set_v.eq(u_op == UOP_NEG),
            ]
        with m.Else():
            m.d.comb += [set_c.eq(~is_logic), set_v.eq(~is_logic)]

        # two lower bits in CPU and ALU opcodes are matched, 3rd bit of CPU
        # opcode selects arithmetic/logic ops and translates to 4th bit
        # of ALU opcode
        with m.If(is_unary):
            with m.Switch(u_op):
                with m.Case(UOP_NOT):
                    m.d.comb += alu.op_code.eq(OpCodes.NOT)
                with m.Case(UOP_NEG):
                    m.d.comb += alu.op_code.eq(OpCodes.NEG)
                with m.Case(UOP_SXT):
                    m.d.comb += alu.op_code.eq(OpCodes.SXT)
                with m.Case(UOP_BSWAP):
                    m.d.comb += alu.op_code.eq(OpCodes.BSWAP)
        with m.Elif(is_cmp):
            m.d.comb += alu.op_code.eq(Mux(op[0], OpCodes.AND, OpCodes.SUB))
        with m.Else():
            m.d.comb += alu.op_code.eq(Cat(op[:2], C(0), op[2]))

        # ---- restartable instructions ----
        # An instruction may be abandoned when a memory request or a fill of
        # the stack cache faults, and executed again later, so every
        # instruction has a commit point: a cache command (or register write)
        # that changes the registers and the stack for good, and nothing
        # that can fault comes after it. Before it only things happen that
        # a second execution does the same again (a store of the same value
        # to the same address, FLUSH of the cache), and the registers that
        # the instruction updates (MIDX, RSP, PFP, flags, PSP) are changed
        # at the commit. The exceptions are the instructions that pop the
        # stack before their last memory access (ST with a pop, CALL pop):
        # they put the popped value back (UNDO).
        # IP and SKIP are restored from `istart`: a skipped instruction is
        # abandoned together with the SKIP before it, which then runs again.
        # An abandoned instruction is an exception: see ABORT.
        def abort(code=None, addr=0, first_fetch=False):
            if code is not None:
                note_fault(code, addr, first_fetch)
            if first_fetch:
                # the first word of the instruction: IP did not move yet
                m.d.sync += ip.eq(Mux(skipping, istart, ip))
            else:
                m.d.sync += ip.eq(istart)
            m.d.sync += skipping.eq(0)
            m.next = 'ABORT'

        # what is known about the exception: the ID and the address of the
        # request, and where the instruction starts
        def note_fault(code, addr, first_fetch=False):
            m.d.sync += [
                exc_code.eq(code),
                fault_addr.eq(addr),
                fault_ip.eq(Mux(skipping, istart, ip) if first_fetch else istart),
            ]

        # the refusal of a request of the CPU, or of a fill of the stack
        # cache; `undo` for the ST and CALL pop that have popped the stack
        def mem_refused(undo=False, first_fetch=False):
            addr = Cat(C(0, 1), req.addr)
            if undo:
                note_fault(ID_FAULT | FAULT_MEM, addr)
                m.next = 'UNDO'
            else:
                abort(ID_FAULT | FAULT_MEM, addr, first_fetch)

        def stack_refused(undo=False):
            addr = Cat(C(0, 1), self.stack_mem.req.addr)
            if undo:
                note_fault(ID_FAULT | FAULT_STACK, addr)
                m.next = 'UNDO'
            else:
                abort(ID_FAULT | FAULT_STACK, addr)

        # the instruction that has been abandoned or has trapped makes the
        # system context run: from the user context by a switch, from the
        # system context by a soft reset
        def enter_system():
            with m.If(user):
                m.next = 'SW_FLUSH'
            with m.Else():
                m.next = 'RESET_SOFT'

        def data_space():
            with m.If(is_pfp | sp_mem):
                m.d.comb += self.space.eq(Space.PSTACK)
            with m.Else():
                m.d.comb += self.space.eq(Space.DATA)

        # ---- prefetch ----
        # The instructions that never use the memory port between DECODE and
        # the next FETCH (register and stack operands only, no extra word)
        # leave the port idle while they execute. The word after them, which
        # FETCH is going to read, is requested in DECODE and waits in a
        # register (or is still on its way) when FETCH comes. Only that one
        # word, and only when it is certain that FETCH wants it:
        #   * the request is made by the states DECODE, EXEC, ALU_DELIVER,
        #     LD_DELIVER, none of which drives `req` otherwise; what is not
        #     taken by FETCH is dropped by any other state (pf_kill): an
        #     abort, an interrupt, a mode switch never sees a prefetched word
        #   * FETCH takes it only for the same address, and not before the
        #     pending write-back fault and the interrupt, which come first
        #   * a refusal of the request is ignored: FETCH asks again and gets
        #     the refusal that counts
        #   * the response of a request that nobody wants any more (`pf_fly`
        #     still up) is dropped; the arbiter allows one read at a time, so
        #     it can not be mistaken for the response of another request
        # The memory does the rest: the word is in the read-ahead buffer
        # (the same block as the instruction), or the read waits for the burst
        # that is going on, while the instruction executes.
        if self.prefetch:
            pf_req = Signal()       # the request is up, not accepted yet
            pf_fly = Signal()       # accepted, the response is awaited
            pf_live = Signal()      # the next FETCH wants it
            pf_valid = Signal()     # pf_word holds it
            pf_addr = Signal(15)
            pf_word = Signal(16)
            pf_path = Signal()      # the states that leave `req` to us
            pf_decode = Signal()
            pf_exec = Signal()
            pf_fetch = Signal()
            pf_simple = (
                is_lea | ((is_ld | is_alu) & ~mem_operand) | is_unary
                | is_shift | is_skip | is_setclr | (is_addp & ~instr[9])
            )
            # a skipped instruction goes from DECODE to FETCH, the others
            # through EXEC (string instructions go back to their start)
            pf_elig = Mux(
                skipping,
                ~needs_extra,
                ok & ~needs_extra & ~str_mode & pf_simple,
            )
            # with an extra word (imm16 only, the other operands with one
            # use the memory) the request is made in EXEC, when IP is past
            # the extra word already
            pf_elig_x = needs_extra & (is_ld | is_alu) & ~mem_operand
            pf_want = Signal()
            m.d.comb += [
                pf_want.eq((pf_decode & pf_elig) | (pf_exec & pf_elig_x)),
                req.valid.eq((pf_req | pf_want) & pf_path),
                req.we.eq(0),
                req.byte_hi.eq(1),
                req.byte_lo.eq(1),
                req.addr.eq(Mux(pf_req, pf_addr, ip[1:])),
            ]

        # finally, the CPU control FSM
        with m.FSM(init='INIT') as fsm:
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
                m.d.sync += ph2.eq(0)
                # a pending asynchronous fault or an interrupt is taken here,
                # between two instructions, and the fetch is not made
                take_irq = self.irq & user
                m.d.comb += [
                    req.valid.eq(~(pend_wb | take_irq)),
                    req.we.eq(0),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq(ip[1:]),
                ]
                if self.prefetch:
                    have = pf_valid & (pf_addr == ip[1:])
                    hot = pf_live & pf_fly & (pf_addr == ip[1:])
                    with m.If(have | hot):
                        # the word is there or on its way: no request
                        m.d.comb += req.valid.eq(0)
                    with m.If(~hot | resp.valid):
                        m.d.sync += [
                            pf_req.eq(0),
                            pf_live.eq(0),
                            pf_valid.eq(0),
                        ]
                with m.If(pend_wb):
                    m.d.sync += pend_wb.eq(0)
                    abort(ID_FAULT | FAULT_WB, wb_addr, first_fetch=True)
                with m.Elif(take_irq):
                    # the user context carries on with the instruction that
                    # was to be fetched (or with the SKIP before it)
                    abort(ID_IRQ | self.irq_num, 0, first_fetch=True)
                with m.Elif(have | (hot & resp.valid) if self.prefetch else C(0)):
                    m.d.sync += [
                        ip.eq(ip + 2),
                        instr.eq(Mux(have, pf_word, resp.data)),
                    ]
                    with m.If(~skipping):
                        m.d.sync += istart.eq(ip)
                    m.next = 'DECODE'
                with m.Elif(hot if self.prefetch else C(0)):
                    pass    # the response is coming
                with m.Elif(self.mem_fault):
                    mem_refused(first_fetch=True)
                with m.Elif(req.ready):
                    m.next = 'FETCH_WAIT'

            with m.State('FETCH_WAIT'):
                with m.If(resp.valid):
                    m.d.sync += [
                        ip.eq(ip + 2),
                        instr.eq(resp.data),
                    ]
                    with m.If(~skipping):
                        m.d.sync += istart.eq(ip)
                    m.next = 'DECODE'

            with m.State('DECODE'):
                m.d.sync += tgt_rel.eq(ip + rel_off)
                with m.If(skipping):
                    # fetch the skipped instruction's extra word, if it has
                    # one, to get past it, and do not execute it
                    with m.If(needs_extra):
                        m.next = 'EXTRA'
                    with m.Else():
                        m.d.sync += skipping.eq(0)
                        m.next = 'FETCH'
                with m.Elif(~ok):
                    # the fault is raised in the next state, to keep the
                    # wide decode of `ok` away from the fault registers
                    m.next = 'ILLEGAL'
                with m.Elif(str_mode & (midx == 0) & ~needs_extra):
                    # a string instruction with nothing to do
                    m.next = 'FETCH'
                with m.Elif(needs_extra):
                    m.next = 'EXTRA'
                with m.Else():
                    m.next = 'EXEC'

            with m.State('ILLEGAL'):
                abort(ID_FAULT | FAULT_ILLEGAL)

            # the extra word of imm16 and abs16 follows the instruction
            with m.State('EXTRA'):
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(0),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq(ip[1:]),
                ]
                with m.If(self.mem_fault):
                    mem_refused()
                with m.Elif(req.ready):
                    m.next = 'EXTRA_WAIT'

            with m.State('EXTRA_WAIT'):
                with m.If(resp.valid):
                    m.d.sync += [
                        ip.eq(ip + 2),
                        extra.eq(resp.data),
                    ]
                    with m.If(skipping):
                        m.d.sync += skipping.eq(0)
                        m.next = 'FETCH'
                    with m.Elif(str_mode & (midx == 0)):
                        # a string instruction with nothing to do
                        m.next = 'FETCH'
                    with m.Else():
                        m.next = 'EXEC'

            with m.State('EXEC'):
                with m.If(tos_ptr & (is_ld | is_alu | is_st | is_swap)):
                    # the address in TOS is taken before anything else
                    m.next = 'PTR_TAKE'
                with m.Elif(is_lea):
                    m.next = 'LD_DELIVER'
                with m.Elif(is_ld | is_alu):
                    # the source is fetched the same way for LD and the ALU
                    with m.If(mem_operand):
                        m.next = 'LD_COH'
                    with m.Elif(is_alu):
                        m.next = 'ALU_DELIVER'
                    with m.Else():
                        m.next = 'LD_DELIVER'
                with m.Elif(is_swap):
                    m.next = 'SWAP_TAKE'
                with m.Elif(is_unary | is_shift):
                    m.next = 'ALU_DELIVER'
                with m.Elif(is_muldiv):
                    m.next = 'MD_TAKE'
                with m.Elif(is_st):
                    m.next = 'ST_POP'
                with m.Elif(is_bra | is_jmp_abs):
                    m.d.sync += ip.eq(tgt)
                    m.next = 'FETCH'
                with m.Elif(is_skip):
                    m.d.sync += skipping.eq(cond)
                    m.next = 'FETCH'
                with m.Elif(is_setclr):
                    with m.If(instr[8]):
                        m.d.sync += sw.eq(sw & ~(instr[0:8] & SR_MASK))
                    with m.Else():
                        m.d.sync += sw.eq(sw | (instr[0:8] & SR_MASK))
                    m.next = 'FETCH'
                with m.Elif(is_addp):
                    with m.Switch(instr[8:10]):
                        with m.Case(0):
                            m.d.sync += mbp.eq(mbp + addp_i)
                            m.next = 'FETCH'
                        with m.Case(1):
                            m.d.sync += midx.eq(midx + addp_i)
                            m.next = 'FETCH'
                        with m.Case(2):
                            m.next = 'ADDP_SP'
                with m.Elif(is_call_pop):
                    m.next = 'CALL_POP'
                with m.Elif(is_call_direct):
                    m.next = 'CALL_RA'
                with m.Elif(is_ret):
                    with m.If(is_ret_n):
                        m.d.sync += val.eq(instr[0:6])
                        m.next = 'RET_IP_RD'
                    with m.Else():
                        m.next = 'RET_FLUSH'
                with m.Elif(is_reti):
                    # only in system mode (the user mode RETI is not `ok`)
                    m.next = 'SW_FLUSH'
                with m.Elif(is_trap):
                    m.d.sync += [
                        exc_code.eq(ID_TRAP | instr[0:6]),
                        fault_ip.eq(istart),
                    ]
                    enter_system()
                with m.Else():
                    m.next = 'FETCH'

            # ---- LD: memory source ----
            # keep the cache coherent with the read: flush if the address is
            # in the cached window, then wait until its write buffer is empty
            with m.State('LD_COH'):
                with m.If(in_window):
                    m.d.comb += cmd.valid.eq(1)
                    # SWAP writes: the cached copy must go too
                    with m.If(is_swap):
                        m.d.comb += cmd.maint.eq(Maint.FLUSH_INVALIDATE)
                    with m.Else():
                        m.d.comb += cmd.maint.eq(Maint.FLUSH)
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
                    req.addr.eq(waddr),
                    self.mem_lock.eq(is_swap),
                ]
                with m.If(self.mem_fault):
                    mem_refused()
                with m.Elif(req.ready):
                    m.d.sync += lane.eq(ea[0])
                    m.next = 'LD_RD_WAIT'

            with m.State('LD_RD_WAIT'):
                m.d.comb += self.mem_lock.eq(is_swap)
                with m.If(resp.valid & split & ~ph2):
                    # an odd word: the first byte is in, the second follows
                    m.d.sync += [
                        part.eq(resp.data[8:16]),
                        ph2.eq(1),
                    ]
                    m.next = 'LD_RD'
                with m.Elif(resp.valid):
                    with m.If(split):
                        m.d.sync += [
                            val.eq(Cat(part, resp.data[0:8])),
                            ph2.eq(0),
                        ]
                    with m.Elif(s):
                        m.d.sync += val.eq(
                            Cat(byte, Mux(sx, byte[7], 0).replicate(8))
                        )
                    with m.Else():
                        m.d.sync += val.eq(resp.data)
                    with m.If(is_alu):
                        m.next = 'ALU_DELIVER'
                    with m.Elif(is_swap):
                        m.next = 'SWAP_WR'
                    with m.Else():
                        m.next = 'LD_DELIVER'

            # ---- SWAP ----
            # TOS or NOS and a memory word exchange their values. The stack
            # item is taken first (a cache command that waits until the line
            # is there, and a refused fill abandons the instruction with
            # nothing done), then the usual steps of a memory read: the
            # cache is flushed and invalidated if it holds the address, and
            # has to be idle. The read and the write that follows go out one
            # after the other with `mem_lock` up (LD_RD, LD_RD_WAIT and
            # SWAP_WR), and the cache is not given a command in between, so
            # nothing else touches the memory. The word that was read then
            # goes to the stack item like an LD does, which cannot fault
            # because it needs no line from memory: so after the write
            # nothing can abandon the instruction.
            # [tos]: the address is TOS, taken into `ptr` before the
            # instruction does anything to the stack, and then it goes on as
            # with any other memory operand
            with m.State('PTR_TAKE'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.need.eq(0b0001),
                ]
                with m.If(stack.fault):
                    stack_refused()
                with m.Elif(cmd.ready):
                    m.d.sync += ptr.eq(stack.d0)
                    with m.If(is_st):
                        m.next = 'ST_POP'
                    with m.Elif(is_swap):
                        m.next = 'SWAP_TAKE'
                    with m.Else():
                        m.next = 'LD_COH'

            with m.State('SWAP_TAKE'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.need.eq(Mux(dd[0], 0b0010, 0b0001)),
                ]
                with m.If(stack.fault):
                    stack_refused()
                with m.Elif(cmd.ready):
                    m.d.sync += swapval.eq(Mux(dd[0], stack.d1, stack.d0))
                    m.next = 'LD_COH'

            with m.State('SWAP_WR'):
                data_space()
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(1),
                    req.byte_hi.eq(byte_hi),
                    req.byte_lo.eq(byte_lo),
                    req.addr.eq(waddr),
                    req.wdata.eq(swap_wdata),
                    self.mem_lock.eq(1),
                ]
                with m.If(self.mem_fault):
                    with m.If(split & ph2):
                        # the first byte is written already: put the old
                        # one back before the instruction is abandoned
                        note_fault(ID_FAULT | FAULT_MEM, Cat(C(0, 1), req.addr))
                        m.next = 'SWAP_UNDO'
                    with m.Else():
                        mem_refused()
                with m.Elif(req.ready):
                    with m.If(split & ~ph2):
                        m.d.sync += ph2.eq(1)
                    with m.Else():
                        m.d.sync += ph2.eq(0)
                        m.next = 'LD_DELIVER'

            # an odd SWAP whose second write was refused: the old first byte
            # goes back (the page was written a moment ago, so this does not
            # fault; if it does, it is tried again), then the abort
            with m.State('SWAP_UNDO'):
                data_space()
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(1),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(0),
                    req.addr.eq(ea[1:]),
                    req.wdata.eq(Cat(C(0, 8), part)),
                    self.mem_lock.eq(1),
                ]
                with m.If(~self.mem_fault & req.ready):
                    abort()

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
                with m.If(stack.fault):
                    stack_refused()
                with m.Elif(cmd.ready):
                    # the commit: MIDX steps for the post-increment and
                    # pre-decrement modes
                    m.d.sync += midx.eq(midx_next)
                    # a string instruction goes on: IP back to its start
                    with m.If(str_mode & (midx_next != 0)):
                        m.d.sync += ip.eq(istart)
                    m.next = 'FETCH'

            # ---- ALU: result and flags in one cache command ----
            # the write uses the numbering before the shift: dd 2 writes NOS
            # and then the pop moves it to TOS, dd 3 writes TOS and then NOS
            # is removed
            with m.State('ALU_DELIVER'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.need.eq(ld_need | need_of(alu_pos)),
                    cmd.w0_en.eq(~is_cmp),
                    cmd.w0_pos.eq(alu_pos),
                    cmd.w0_data.eq(alu.result),
                ]
                with m.If(is_shift):
                    m.d.comb += cmd.w0_data.eq(sh.data_out)
                    # the count in TOS is read
                    with m.If(sh_var):
                        m.d.comb += cmd.need.eq(need_of(alu_pos) | 0b0001)
                with m.If(is_unary | (is_shift & ~sh_var)):
                    # dd 0 and 1 replace the operand, 2 and 3 keep it and
                    # push the result on top or under TOS
                    with m.Switch(dd):
                        with m.Case(2):
                            m.d.comb += [
                                cmd.w0_en.eq(0),
                                cmd.shift.eq(Shift.PUSH),
                                cmd.arg.eq(Mux(is_shift, sh.data_out, alu.result)),
                            ]
                        with m.Case(3):
                            m.d.comb += [
                                cmd.w0_en.eq(0),
                                cmd.shift.eq(Shift.PUSH_UNDER),
                                cmd.arg.eq(Mux(is_shift, sh.data_out, alu.result)),
                            ]
                with m.Else():
                    with m.Switch(dd):
                        with m.Case(2):
                            m.d.comb += cmd.shift.eq(Shift.POP)
                        with m.Case(3):
                            m.d.comb += cmd.shift.eq(Shift.POP_UNDER)
                with m.If(stack.fault):
                    stack_refused()
                with m.Elif(cmd.ready):
                    # a string instruction goes on: IP back to its start
                    with m.If(str_mode & (midx_next != 0)):
                        m.d.sync += ip.eq(istart)
                    m.d.sync += [
                        midx.eq(midx_next),
                        sw[1].eq(alu.zero),
                        sw[2].eq(alu.sign),
                        sw[4].eq(alu.parity),
                    ]
                    with m.If(is_shift):
                        # Z N P from the result; a real shift sets C and
                        # clears V, a count of 0 leaves both
                        m.d.sync += [
                            sw[1].eq(sh.data_out == 0),
                            sw[2].eq(sh_n),
                            sw[4].eq(~sh.data_out.xor()),
                        ]
                        with m.If(sh.carry_upd):
                            m.d.sync += [
                                sw[0].eq(sh.carry_out),
                                sw[3].eq(0),
                            ]
                    with m.If(set_c & ~is_shift):
                        m.d.sync += sw[0].eq(alu.carry)
                    with m.If(set_v & ~is_shift):
                        m.d.sync += sw[3].eq(alu.overflow)
                    m.next = 'FETCH'

            # ---- MUL and DIV ----
            # NOS and TOS go in, two results come out in their place: the
            # primary one (the low part of a product, the quotient) in TOS
            # and the secondary one (the high part, the remainder) in NOS.
            # The operands are taken into registers, the unit works, and
            # one cache command writes both results and the flags. A
            # division by zero abandons the instruction before that, with
            # nothing changed.
            with m.State('MD_TAKE'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.need.eq(0b0011),
                ]
                with m.If(stack.fault):
                    stack_refused()
                with m.Elif(cmd.ready):
                    m.d.sync += [
                        md_a.eq(stack.d1),
                        md_b.eq(stack.d0),
                    ]
                    m.next = 'MD_START'

            with m.State('MD_START'):
                m.d.comb += md.start.eq(1)
                m.next = 'MD_WAIT'

            with m.State('MD_WAIT'):
                with m.If(md.done):
                    with m.If(md.zero_div):
                        abort(ID_FAULT | FAULT_DIV)
                    with m.Else():
                        m.next = 'MD_DELIVER'

            with m.State('MD_DELIVER'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.need.eq(0b0011),
                    cmd.w0_en.eq(1),
                    cmd.w0_pos.eq(0),
                    cmd.w0_data.eq(md.primary),
                    cmd.w1_en.eq(1),
                    cmd.w1_pos.eq(1),
                    cmd.w1_data.eq(md.secondary),
                ]
                with m.If(stack.fault):
                    stack_refused()
                with m.Elif(cmd.ready):
                    m.d.sync += [
                        sw[1].eq(md.primary == 0),
                        sw[2].eq(Mux(s, md.primary[7], md.primary[15])),
                        sw[3].eq(md.overflow),
                        sw[4].eq(~md.primary.xor()),
                    ]
                    with m.If(~md_div):
                        m.d.sync += sw[0].eq(md.overflow)
                    m.next = 'FETCH'

            # ---- ADDP SP: the same as storing to SP ----
            with m.State('ADDP_SP'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.SET_PSP),
                    cmd.arg.eq(stack.psp + addp_i),
                ]
                with m.If(cmd.ready):
                    m.next = 'FETCH'

            # ---- CALL ----
            # The return address and the caller's PFP go on the return stack
            # (the address on top: at rsp-4, PFP at rsp-2), and PFP becomes PSP: the frame is empty
            # and the arguments the caller pushed start at [pfp]. Nothing is
            # written to the parameter stack. RSP moves only after both writes
            # have been accepted.
            # CALL pop takes its target off the stack first.
            with m.State('CALL_POP'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.need.eq(0b0001),
                    cmd.shift.eq(Shift.POP),
                ]
                with m.If(stack.fault):
                    stack_refused()
                with m.Elif(cmd.ready):
                    m.d.sync += val.eq(stack.d0)
                    m.next = 'CALL_RA'

            with m.State('CALL_RA'):
                rstack_space()
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(1),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq((rsp - 4)[1:]),
                    req.wdata.eq(ip),
                ]
                with m.If(self.mem_fault):
                    # CALL pop has already popped its target
                    with m.If(is_call_pop):
                        mem_refused(undo=True)
                    with m.Else():
                        mem_refused()
                with m.Elif(req.ready):
                    m.next = 'CALL_LINK'

            with m.State('CALL_LINK'):
                rstack_space()
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(1),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq((rsp - 2)[1:]),
                    req.wdata.eq(pfp),
                ]
                with m.If(self.mem_fault):
                    with m.If(is_call_pop):
                        mem_refused(undo=True)
                    with m.Else():
                        mem_refused()
                with m.Elif(req.ready):
                    m.d.sync += [
                        rsp.eq(rsp - 4),
                        pfp.eq(stack.psp),
                        ip.eq(tgt),
                    ]
                    m.next = 'FETCH'

            # ---- RET ----
            # Takes the return address and the caller's PFP off the return
            # stack and adds 2 * n to PSP: the callee has already released its
            # locals and left its result in the top n-th word of the frame
            # area (see isa.md). RET #n has n in the instruction. The variadic
            # RET reads n from the word at [pfp], so the cache is flushed
            # first.
            with m.State('RET_FLUSH'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.FLUSH),
                ]
                with m.If(cmd.ready):
                    m.next = 'RET_IDLE'

            with m.State('RET_IDLE'):
                with m.If(stack.idle):
                    m.next = 'RET_CNT_RD'

            with m.State('RET_CNT_RD'):
                pstack_space()
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(0),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq(pfp[1:]),
                ]
                with m.If(self.mem_fault):
                    mem_refused()
                with m.Elif(req.ready):
                    m.next = 'RET_CNT_WAIT'

            with m.State('RET_CNT_WAIT'):
                with m.If(resp.valid):
                    m.d.sync += val.eq(resp.data)
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
                with m.If(self.mem_fault):
                    mem_refused()
                with m.Elif(req.ready):
                    m.next = 'RET_IP_WAIT'

            # IP is not a committed value yet: an abort restores it
            with m.State('RET_IP_WAIT'):
                with m.If(resp.valid):
                    m.d.sync += ip.eq(resp.data)
                    m.next = 'RET_LNK_RD'

            # the last read; nothing else is changed until RET_SP
            with m.State('RET_LNK_RD'):
                rstack_space()
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(0),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq((rsp + 2)[1:]),
                ]
                with m.If(self.mem_fault):
                    mem_refused()
                with m.Elif(req.ready):
                    m.next = 'RET_LNK_WAIT'

            with m.State('RET_LNK_WAIT'):
                with m.If(resp.valid):
                    m.d.sync += link.eq(resp.data)
                    m.next = 'RET_SP'

            # the commit. SET_PSP writes the dirty lines back first: a refused
            # write-back is asynchronous, so the instruction does not fault
            with m.State('RET_SP'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.SET_PSP),
                    cmd.arg.eq(stack.psp + Cat(C(0, 1), val)),
                ]
                with m.If(cmd.ready):
                    m.d.sync += [
                        pfp.eq(link),
                        rsp.eq(rsp + 4),
                    ]
                    m.next = 'FETCH'

            # ---- ST: take the value off the stack ----
            with m.State('ST_POP'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.need.eq(
                        Mux(dd[0], 0b0010, 0b0001)
                        | Mux(
                            st_merge,
                            Cat(st_pre == 0, st_pre == 1, st_pre == 2, st_pre == 3),
                            0,
                        )
                    ),
                ]
                with m.Switch(dd):
                    with m.Case(2):
                        m.d.comb += cmd.shift.eq(Shift.POP)
                    with m.Case(3):
                        m.d.comb += cmd.shift.eq(Shift.POP_UNDER)
                with m.If(stack.fault):
                    stack_refused()
                with m.Elif(cmd.ready):
                    m.d.sync += val.eq(Mux(dd[0], stack.d1, stack.d0))
                    m.next = 'ST_DEST'

            # ---- ST: write the destination ----
            with m.State('ST_DEST'):
                with m.If(reg_mode):
                    with m.Switch(x):
                        with m.Case(REG_MBP):
                            m.d.sync += mbp.eq(val_sz)
                            m.next = 'FETCH'
                        with m.Case(REG_MIDX):
                            m.d.sync += midx.eq(val_sz)
                            m.next = 'FETCH'
                        with m.Case(REG_SP):
                            m.next = 'ST_SP'
                        with m.Case(REG_SR):
                            m.d.sync += sw.eq(val_sz & SR_MASK)
                            m.next = 'FETCH'
                        with m.Case(REG_IP):
                            m.d.sync += ip.eq(val_sz)
                            m.next = 'FETCH'
                        with m.Default():
                            m.next = 'FETCH'
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
                with m.If(stack.fault):
                    # only the item 3 of a byte store after a pop gets here
                    with m.If(dd[1]):
                        stack_refused(undo=True)
                    with m.Else():
                        stack_refused()
                with m.Elif(cmd.ready):
                    m.next = 'FETCH'

            with m.State('ST_SP'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.SET_PSP),
                    cmd.arg.eq(val_sz),
                ]
                with m.If(cmd.ready):
                    m.next = 'FETCH'

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
                    req.addr.eq(waddr),
                    req.wdata.eq(wdata),
                ]
                with m.If(self.mem_fault):
                    with m.If(dd[1]):
                        mem_refused(undo=True)
                    with m.Else():
                        mem_refused()
                with m.Elif(req.ready):
                    with m.If(split & ~ph2):
                        m.d.sync += ph2.eq(1)
                    with m.Else():
                        m.d.sync += midx.eq(midx_next)
                        # a string instruction goes on: IP back to its start
                        with m.If(str_mode & (midx_next != 0)):
                            m.d.sync += ip.eq(istart)
                        m.next = 'FETCH'

            # ---- abandoning an instruction ----
            # ST and CALL pop the stack before their last access. Put the
            # popped value back: PUSH for a pop, PUSH_UNDER for ST with dd 3.
            with m.State('UNDO'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.arg.eq(val),
                ]
                with m.If(is_st & (dd == 3)):
                    m.d.comb += cmd.shift.eq(Shift.PUSH_UNDER)
                with m.Else():
                    m.d.comb += cmd.shift.eq(Shift.PUSH)
                with m.If(cmd.ready):
                    abort()

            # The instruction has left no trace (IP is where it starts) and
            # the exception is known (exc_code, fault_ip, fault_addr). The
            # system context takes over.
            with m.State('ABORT'):
                m.d.comb += self.aborted.eq(1)
                enter_system()

            # ---- changing the context ----
            # RETI (system to user), TRAP and the exceptions of the user
            # context (user to system) exchange the registers of the two
            # contexts. Each has its own parameter stack: the stack cache
            # writes its lines back, and SET_PSP switches it to the other
            # stack, which is read back as needed. IP is the one of the
            # next instruction for RETI and TRAP and the one of the
            # instruction that failed for an exception, so the user context
            # carries on with it, and the system context carries on after
            # its RETI.
            with m.State('SW_FLUSH'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.FLUSH),
                ]
                with m.If(cmd.ready):
                    m.next = 'SW_WAIT'

            # wait until all the write-backs are done; a refused one belongs
            # to the context that is leaving
            with m.State('SW_WAIT'):
                with m.If(stack.idle):
                    with m.If(pend_wb):
                        m.d.sync += [
                            pend_wb.eq(0),
                            exc_code.eq(ID_FAULT | FAULT_WB),
                            fault_addr.eq(wb_addr),
                            fault_ip.eq(istart),
                        ]
                        with m.If(user):
                            m.next = 'SW_PSP'
                        with m.Else():
                            m.next = 'RESET_SOFT'
                    with m.Else():
                        m.next = 'SW_PSP'

            with m.State('SW_PSP'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.SET_PSP),
                    cmd.arg.eq(b_psp),
                ]
                with m.If(cmd.ready):
                    m.d.sync += [
                        ip.eq(b_ip), b_ip.eq(ip),
                        rsp.eq(b_rsp), b_rsp.eq(rsp),
                        pfp.eq(b_pfp), b_pfp.eq(pfp),
                        mbp.eq(b_mbp), b_mbp.eq(mbp),
                        midx.eq(b_midx), b_midx.eq(midx),
                        sw.eq(b_sw), b_sw.eq(sw),
                        b_psp.eq(stack.psp),
                        user.eq(~user),
                        skipping.eq(0),
                    ]
                    with m.If(user):
                        m.d.sync += intr_id.eq(exc_code)
                    m.next = 'FETCH'

            # ---- soft reset: an exception in the system context ----
            # the system context gets its power-up values (the user context
            # is left as it is), and the ID tells the reason. The dirty
            # lines of the system stack are dropped.
            with m.State('RESET_SOFT'):
                m.d.comb += [
                    self.soft_reset.eq(1),
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.INVALIDATE),
                ]
                with m.If(cmd.ready):
                    m.d.sync += [
                        ip.eq(IP_INIT),
                        rsp.eq(RSP_INIT),
                        pfp.eq(PFP_INIT),
                        mbp.eq(0),
                        midx.eq(0),
                        sw.eq(0),
                        skipping.eq(0),
                        pend_wb.eq(0),
                        intr_id.eq(exc_code),
                    ]
                    m.next = 'RESET_PSP'

            with m.State('RESET_PSP'):
                m.d.comb += [
                    cmd.valid.eq(1),
                    cmd.maint.eq(Maint.SET_PSP),
                    cmd.arg.eq(PSP_INIT),
                ]
                with m.If(cmd.ready):
                    m.next = 'FETCH'

        # ---- the context port ----
        ctx_regs = Array([
            b_ip, b_rsp, b_pfp, b_mbp, b_midx, b_sw, b_psp,
            intr_id, fault_ip, fault_addr,
        ])
        with m.If(self.ctx_addr < 10):
            m.d.comb += self.ctx_rdata.eq(ctx_regs[self.ctx_addr])
        with m.If(self.ctx_we):
            with m.Switch(self.ctx_addr):
                with m.Case(CTX_IP):
                    m.d.sync += b_ip.eq(self.ctx_wdata)
                with m.Case(CTX_RSP):
                    m.d.sync += b_rsp.eq(self.ctx_wdata)
                with m.Case(CTX_PFP):
                    m.d.sync += b_pfp.eq(self.ctx_wdata)
                with m.Case(CTX_MBP):
                    m.d.sync += b_mbp.eq(self.ctx_wdata)
                with m.Case(CTX_MIDX):
                    m.d.sync += b_midx.eq(self.ctx_wdata)
                with m.Case(CTX_SW):
                    m.d.sync += b_sw.eq(self.ctx_wdata & SR_MASK)
                with m.Case(CTX_PSP):
                    m.d.sync += b_psp.eq(self.ctx_wdata)

        # a refused write-back, latched until the next instruction boundary.
        # This comes last so that it wins over the clearing above.
        with m.If(stack.wb_fault):
            m.d.sync += [
                pend_wb.eq(1),
                wb_addr.eq(Cat(C(0, 1), self.stack_mem.req.addr)),
            ]

        if self.prefetch:
            m.d.comb += [
                pf_path.eq(
                    fsm.ongoing('DECODE') | fsm.ongoing('EXEC')
                    | fsm.ongoing('ALU_DELIVER') | fsm.ongoing('LD_DELIVER')
                ),
                pf_decode.eq(fsm.ongoing('DECODE')),
                pf_exec.eq(fsm.ongoing('EXEC')),
                pf_fetch.eq(fsm.ongoing('FETCH')),
            ]
            pf_acc = req.valid & req.ready & pf_path
            pf_refused = req.valid & self.mem_fault & pf_path
            # the response
            with m.If(pf_fly & resp.valid):
                m.d.sync += pf_fly.eq(0)
                with m.If(pf_live & pf_path):
                    m.d.sync += [
                        pf_word.eq(resp.data),
                        pf_valid.eq(1),
                    ]
            # the request
            with m.If(pf_want):
                m.d.sync += [pf_live.eq(1), pf_addr.eq(ip[1:])]
            with m.If(pf_acc):
                m.d.sync += [pf_fly.eq(1), pf_req.eq(0)]
            with m.Elif(pf_refused):
                m.d.sync += [pf_req.eq(0), pf_live.eq(0)]
            with m.Elif(pf_want):
                m.d.sync += pf_req.eq(1)
            # anything else than the instruction's own states and FETCH:
            # nothing is kept
            with m.If(~(pf_path | pf_fetch)):
                m.d.sync += [pf_req.eq(0), pf_live.eq(0), pf_valid.eq(0)]

        # for the profiling test (tests/test_memory_profile.py): no logic
        self.dbg_state = fsm.state
        self.dbg_encoding = fsm.encoding

        return m


class CPUTop(Elaboratable):
    def __init__(self, *, mmu_sync=False):
        # the MMU with synchronous read ports: block RAM instead of LUTs, one
        # more clock for every memory access (see mmu.py)
        self.mmu_sync = mmu_sync

    def elaborate(self, platform):
        from amaranth.build import Pins, Resource

        from de0nano_ide import DE0NanoIDEPins
        from de0nano_sdram import DE0NanoSDRAM
        from de0nano_spi_ethernet import DE0NanoSPIEtherPins
        from de0nano_spi_flash import DE0NanoSPIFlash
        from ide_pio import IDEPio
        from system import System
        from spi_master import SPIMaster
        from uart import UART

        m = Module()
        m.submodules.system = system = System(mmu_sync=self.mmu_sync)

        # connect the system to the memories and the UART
        m.submodules.sdram = sdram = DE0NanoSDRAM()
        m.submodules.flash = flash = DE0NanoSPIFlash()
        m.submodules.uart = uart = UART(divisor=round(50_000_000 / 38_400))
        connect(m, flash.bus, system.flash)
        connect(m, sdram.bus, system.ram)
        connect(m, uart.bus, system.uart)
        m.submodules.spi = spi = SPIMaster()
        m.submodules.ether = ether = DE0NanoSPIEtherPins()
        connect(m, spi.bus, system.spi)
        connect(m, spi.pins, ether.spi)
        m.submodules.ide = ide = IDEPio()
        m.submodules.cf = cf = DE0NanoIDEPins()
        connect(m, ide.bus, system.ide)
        connect(m, ide.pins, cf.ide)
        m.d.comb += system.irq_ext.eq(Cat(uart.rx_avail, spi.irq, ide.irq))

        # UART pins hookup
        platform.add_resources([
                Resource('tx', 0, Pins('8', dir='o', conn=('gpio', 0))),  # B4
                Resource('rx', 0, Pins('10', dir='i', conn=('gpio', 0))),  # B5
            ])
        tx_pin = platform.request('tx')
        rx_pin = platform.request('rx')
        m.d.comb += [tx_pin.o.eq(uart.pins.tx), uart.pins.rx.eq(rx_pin.i)]

        return m


if __name__ == '__main__':
    from amaranth_boards.de0nano import DE0NanoPlatform

    # the system clock comes from the PLL, so the timing analyzer has to be
    # told to derive the clocks of the PLL outputs from its settings:
    # without that they have no constraints (derive_pll_clocks), and Quartus
    # warns about it (332056)
    DE0NanoPlatform().build(
        CPUTop(),
        do_program=True,
        add_constraints='derive_pll_clocks\nderive_clock_uncertainty\n',
    )
