"""
Pure-Python side of the LD/ST/ALU/control-flow test: assembler, reference interpreter and
random program generator. No Amaranth in here.

The reference is the architecture itself: registers plus ordinary flat memory,
with the parameter stack in that memory (item k at PSP + 2*k, growing down).
It is written from the instruction descriptions, not from cpu.py.

Programs are built so that nothing depends on what the stack cache leaves in
memory it does not have to (below PSP, i.e. popped items, is dead): every
pointer access that reads stack memory only reads live items, and the final
stack is dumped through LD/ST so it can be compared.
"""
import random

PSP0 = 0xf000
PFP0 = 0xf000
FAR = 0x2000        # data area for pointer accesses
OUT = 0x3000        # result dump
TOP = 0xf008        # memory above the stack base, free to use
MAX_DEPTH = 40

OP_ADD, OP_ADC, OP_SUB, OP_SBC, OP_AND, OP_OR, OP_XOR, OP_BIC = range(8)
OP_LD = 8
OP_ST = 9
OP_SWAP = 10
OP_LEA = 11
OP_CMP = 12
OP_TST = 13
U_NOT, U_NEG, U_SXT, U_BSWAP = range(4)
SH_SHL, SH_SHR, SH_SAR, SH_ROL, SH_ROR, SH_RCL, SH_RCR = 0, 1, 2, 4, 5, 6, 7
REG_NULL = 0
REG_SP = 1
REG_MBP = 2
REG_MIDX = 3
REG_SR = 6
REG_IP = 7
SR_MASK = 0x1f


def junk(byte_addr):
    """
    Contents of memory nobody has written (the same for model and CPU test).
    """
    return (byte_addr * 2654435761 >> 7) & 0xffff


def sext(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


# ---- assembler ----

def i_imm8(op, dd, imm):
    return [(op << 10) | (dd << 8) | (imm & 0xff)]


def i_lea(dd, f):
    return i_pfp(OP_LEA, dd, 0, f)


def i_pfp(op, dd, s, f):
    return [(1 << 14) | (op << 10) | (dd << 8) | (s << 7) | (f & 0x7f)]


def i_gen(op, dd, s, mode, x, extra=None, e=0):
    """
    An instruction of the GENERAL format, with the operand named by the
    logical mode: 0 register x, 1 2 3 [mbp+midx] and its post-increment and
    pre-decrement forms, 4 #imm16, 5 [abs], 6 [sp+k] (k = x), 7 [tos], 8 9 the
    string forms of the post-increment and pre-decrement modes, 10 the string
    form of [abs]. `e`
    (or the bit 0 of x in the modes 1 2 3 7) is the sign-extension flag of
    byte loads. The operand field is a prefix code, see gen_operand.
    """
    if mode == 0:
        f = x
    elif mode in (1, 2, 3):
        f = 0x10 | (mode - 1) << 1 | (x & 1 | e)
    elif mode == 7:
        f = 0x10 | 3 << 1 | (x & 1 | e)
    elif mode in (8, 9):
        f = 0x18 | (mode - 7) << 1 | (x & 1 | e)
    elif mode == 10:
        f = 0x24 | (x & 1 | e)
    elif mode == 5:
        f = 0x20 | (x & 1 | e)
    elif mode == 4:
        f = 0x22
    else:
        f = 0x40 | x << 1 | e
    w = (2 << 14) | (op << 10) | (dd << 8) | (s << 7) | f
    return [w] if extra is None else [w, extra & 0xffff]


def gen_operand(w):
    """
    The operand field of a GENERAL format instruction (its low 7 bits):
    (logical mode, x, e), or None if there is no such operand.
        000 0rrr    register r: 0 NULL, 1 PSP, 2 MBP, 3 MIDX, 6 SR, 7 IP
        001 0 mm e  mm: 0 [mbp+midx], 1 [mbp+midx++], 2 [mbp+--midx],
                    3 [tos]
        001 1 mm e  string forms of mm 1 and 2 (0 and 3 are reserved): the
                    instruction repeats until MIDX is 0, and is skipped
                    if MIDX is 0 already; the logical mode is that of mm
        01 0000 e   [abs16]
        01 0010 e   [abs16] again and again: MIDX is the byte count and goes
                    down by the size until 0 (skipped if it is 0); the logical
                    mode is that of [abs16]
        01 0001 0   #imm16
        1 kkkkk e   [sp+k]
    """
    f = w & 0x7f
    if f >> 4 == 0:
        return 0, f & 15, 0
    if f >> 3 == 0b0010:
        kind = (f >> 1) & 3
        return (7 if kind == 3 else kind + 1), 0, f & 1
    if f >> 3 == 0b0011 and (f >> 1) & 3 in (1, 2):
        return ((f >> 1) & 3) + 1, 0, f & 1
    if f >> 1 in (0b010000, 0b010010):
        return 5, 0, f & 1
    if f == 0b0100010:
        return 4, 0, 0
    if f >> 6:
        return 6, (f >> 1) & 31, f & 1
    return None


# control format 11
MISC_NOP = 0
MISC_RET_N = 1
MISC_CALL_ABS = 5
MISC_JMP_ABS = 6
MISC_CALL_POP = 7
MISC_RET_VAR = 9


def i_bra(rel):
    return [0xc000 | (rel & 0x7ff)]


def i_call_rel(rel):
    return [0xf000 | (rel & 0x7ff)]


def i_skip(cc):
    return [0xc800 | (cc << 6)]


def i_setb(i):
    return [0xd000 | (i & 0xff)]


def i_clrb(i):
    return [0xd100 | (i & 0xff)]


def i_unary(op, dd, s):
    return [0xe000 | dd << 8 | s << 7 | op << 3]


def i_shift(kind, dd, s, count=None):
    """count: 1..16 for the immediate one, None for the count in TOS"""
    if count is None:
        return [0xec00 | dd << 8 | s << 7 | kind << 4]
    return [0xe800 | dd << 8 | s << 7 | kind << 4 | count & 15]


def i_muldiv(div, signed, s):
    return [0xe400 | div << 9 | signed << 8 | s << 7]


def i_addp(rr, i):
    return [0xd400 | (rr << 8) | (i & 0xff)]


def i_misc(op, p=0, extra=None):
    w = 0xf800 | (op << 6) | p
    return [w] if extra is None else [w, extra & 0xffff]


# ---- reference interpreter ----

class Ref:
    def __init__(self):
        self.w = {}
        self.psp = PSP0
        self.pfp = PFP0
        self.mbp = 0
        self.midx = 0
        self.rsp = 0
        self.c = self.z = self.n = self.v = self.p = 0

    @property
    def flags(self):
        return self.c | self.z << 1 | self.n << 2 | self.v << 3 | self.p << 4

    def set_flags(self, v):
        self.c, self.z, self.n, self.v, self.p = (v >> k & 1 for k in range(5))

    def rd16(self, a):
        a &= 0xfffe
        return self.w.get(a >> 1, junk(a))

    def wr16(self, a, v):
        self.w[(a & 0xfffe) >> 1] = v & 0xffff

    def rd(self, a, s):
        if not s and a & 1:
            # a word at an odd address: two byte accesses
            return self.rd(a, 1) | self.rd((a + 1) & 0xffff, 1) << 8
        w = self.rd16(a)
        if not s:
            return w
        return (w >> 8) & 0xff if a & 1 else w & 0xff

    def wr(self, a, s, v):
        if not s and a & 1:
            self.wr(a, 1, v & 0xff)
            self.wr((a + 1) & 0xffff, 1, v >> 8 & 0xff)
            return
        if not s:
            self.wr16(a, v)
            return
        w = self.rd16(a)
        if a & 1:
            w = (w & 0x00ff) | ((v & 0xff) << 8)
        else:
            w = (w & 0xff00) | (v & 0xff)
        self.wr16(a, w)

    def item(self, k):
        return self.rd16(self.psp + 2 * k)

    def ea_ptr(self, mode, s):
        size = 1 if s else 2
        if mode == 3:
            self.midx = (self.midx - size) & 0xffff
        return (self.mbp + self.midx) & 0xffff

    def ptr_after(self, mode, s):
        if mode == 2:
            self.midx = (self.midx + (1 if s else 2)) & 0xffff

    def step(self, words, next_ip):
        """
        One instruction. The string forms (001 1 mm e) run it once and, while
        MIDX is not 0, stay on it: the IP returned is its own address.
        """
        w = words[0]
        if w >> 14 == 2 and (w >> 1) & 0x3f == 0b010010:
            # [abs16]*: the count goes down before the access
            if self.midx == 0:
                return None
            self.midx = (self.midx - (1 if (w >> 7) & 1 else 2)) & 0xffff
            target = self.step1(words, next_ip)
            if target is None and self.midx != 0:
                return (next_ip - 4) & 0xffff
            return target
        if w >> 14 != 2 or w & 0x78 != 0x18:
            return self.step1(words, next_ip)
        if self.midx == 0:
            return None
        target = self.step1(words, next_ip)
        if target is None and self.midx != 0:
            return (next_ip - 2) & 0xffff
        return target

    def step1(self, words, next_ip):
        w = words[0]
        fmt = w >> 14
        op = (w >> 10) & 15
        dd = (w >> 8) & 3
        if fmt == 3:
            return self.ctl(words, next_ip)
        s = (w >> 7) & 1 if fmt else 0
        if op == OP_LEA and fmt == 1:
            self.ld_put(dd, (self.pfp + 2 * sext(w & 0x7f, 7)) & 0xffff)
        elif op == OP_LD:
            v = self.ld_src(words, fmt, s, next_ip)
            self.ld_put(dd, v)
        elif OP_ADD <= op <= OP_BIC or op in (OP_CMP, OP_TST):
            b = self.ld_src(words, fmt, s, next_ip)
            if fmt == 0 and (OP_AND <= op <= OP_BIC or op == OP_TST):
                b = w & 0xff
            self.alu(op, dd, b, s)
        elif op == OP_SWAP and fmt != 0:
            self.swap(words, fmt, s, dd)
        elif op == OP_ST and fmt != 0:
            v = self.item(dd & 1)
            if dd == 2:
                self.psp += 2
            elif dd == 3:
                top = self.item(0)
                self.psp += 2
                self.wr16(self.psp, top)
            return self.st_dst(words, fmt, s, v)

    @staticmethod
    def length(w):
        """Words in the instruction that starts with w."""
        fmt = w >> 14
        if fmt == 2:
            return 2 if gen_operand(w) is not None and gen_operand(w)[0] in (4, 5) else 1
        if fmt == 3 and (w >> 12) & 3 == 3 and w & 0x800:
            return 2 if (w >> 6) & 31 in (MISC_CALL_ABS, MISC_JMP_ABS) else 1
        return 1

    @staticmethod
    def is_skip(w):
        return w >> 14 == 3 and (w >> 12) & 3 == 0 and w & 0xc00 == 0x800

    def cond(self, cc):
        c, z, n, v, p = self.c, self.z, self.n, self.v, self.p
        return int([
            z, not z, c, not c, n, not n, v, not v,
            c and not z, not c or z,
            n == v, n != v, not z and n == v, z or n != v,
            p, not p,
        ][cc])

    def call(self, target, ret):
        self.rsp = (self.rsp - 4) & 0xffff
        self.wr16(self.rsp, ret)
        self.wr16(self.rsp + 2, self.pfp)
        self.pfp = self.psp
        return target

    def ret(self, n):
        if n is None:
            n = self.rd16(self.pfp)
        ip = self.rd16(self.rsp)
        self.pfp = self.rd16(self.rsp + 2)
        self.rsp = (self.rsp + 4) & 0xffff
        self.psp = (self.psp + 2 * n) & 0xffff
        return ip

    def ctl(self, words, next_ip):
        """A control format instruction: returns the new IP, or None to go on."""
        w = words[0]
        grp = (w >> 12) & 3
        rel = (next_ip + 2 * sext(w & 0x7ff, 11)) & 0xffff
        if grp == 0 and not w & 0x800:
            return rel
        if grp == 1 and (w >> 10) & 3 == 0 and not (w >> 9) & 1:
            i = w & SR_MASK
            self.set_flags(self.flags & ~i if (w >> 8) & 1 else self.flags | i)
            return None
        if grp == 1 and (w >> 10) & 3 == 1:
            rr, i = (w >> 8) & 3, sext(w & 0xff, 8)
            if rr == 0:
                self.mbp = (self.mbp + i) & 0xffff
            elif rr == 1:
                self.midx = (self.midx + i) & 0xffff
            elif rr == 2:
                self.psp += i
            return None
        if grp == 2 and (w >> 10) & 3 == 0:
            self.unary((w >> 3) & 15, (w >> 8) & 3, (w >> 7) & 1)
            return None
        if grp == 2 and (w >> 10) & 3 == 1 and not w & 0x7f:
            self.muldiv((w >> 9) & 1, (w >> 8) & 1, (w >> 7) & 1)
            return None
        if grp == 2 and (w >> 11) & 1 and (w >> 4) & 7 != 3 and not (w & 15 and (w >> 10) & 1):
            self.shift(w)
            return None
        if grp == 3 and not w & 0x800:
            return self.call(rel, next_ip)
        if grp == 3:
            op = (w >> 6) & 31
            if op == MISC_RET_N:
                return self.ret(w & 63)
            if op == MISC_RET_VAR:
                return self.ret(None)
            if op == MISC_CALL_ABS:
                return self.call(words[1], next_ip)
            if op == MISC_JMP_ABS:
                return words[1]
            if op == MISC_CALL_POP:
                target = self.item(0)
                self.psp += 2
                return self.call(target, next_ip)
        return None

    def run(self, code, pc, stop):
        """Execute from pc until the IP reaches stop (the return address)."""
        for _ in range(100000):
            if pc == stop:
                return
            if self.is_skip(code[pc >> 1]):
                pc += 2
                if self.cond((code[(pc - 2) >> 1] >> 6) & 15):
                    pc += 2 * self.length(code[pc >> 1])
                continue
            n = self.length(code[pc >> 1])
            nxt = pc + 2 * n
            target = self.step(code[pc >> 1:(pc >> 1) + n], nxt)
            pc = nxt if target is None else target
        raise AssertionError('reference run does not finish')

    def muldiv(self, div, signed, s):
        """
        NOS and TOS are replaced by the primary (TOS) and the secondary (NOS)
        result. A division by zero must not get here.
        """
        bits = 8 if s else 16
        mask = (1 << bits) - 1
        b = self.item(0) & mask
        a = self.item(1) & mask
        if signed:
            a, b = sext(a, bits), sext(b, bits)
        if div:
            assert b != 0
            q = abs(a) // abs(b)
            if (a < 0) != (b < 0):
                q = -q
            r = a - q * b
            big = signed and q == 1 << (bits - 1)
            prim, sec = q & mask, r & mask
        else:
            p = a * b
            prim, sec = p & mask, p >> bits & mask
            if signed:
                big = p != sext(prim, bits)
            else:
                big = sec != 0
            self.c = int(big)
        self.v = int(big)
        self.z = int(prim == 0)
        self.n = prim >> (bits - 1) & 1
        self.p = int(bin(prim).count('1') % 2 == 0)
        self.wr16(self.psp, prim)
        self.wr16(self.psp + 2, sec)

    def shift(self, w):
        """
        Shifts and rotates: `11 10 1v dd s kkk cccc`, `kkk` of shifter.ShiftOp (0 SHL, 1 SHR,
        2 SAR, 4 ROL, 5 ROR, 6 RCL, 7 RCR through carry). With v = 0 the count is cccc, 0 for
        16, and `dd` is that of the unary instructions. With v = 1 (cccc = 0) the count is TOS,
        the whole word, and `dd` is that of the ALU instructions with TOS as the source:
        1 shifts NOS and keeps the count, 2 shifts NOS and drops the count.
        A shift by the width or more gives 0 (SAR: the sign), C is the last bit shifted out. The
        rotates take the count modulo the width. RCL and RCR take the low 4 bits of the count,
        0 for 16, and rotate the ring of C and the data (width + 1 bits) by it. With a count of 0
        (not for RCL and RCR) the item, C and V stay, otherwise V is cleared.
        """
        v = (w >> 10) & 1
        dd = (w >> 8) & 3
        s = (w >> 7) & 1
        kind = (w >> 4) & 7
        bits = 8 if s else 16
        mask = (1 << bits) - 1
        if v:
            count, pos = self.item(0), 1 if dd in (1, 2) else 0
        else:
            count, pos = (w & 15) or 16, (dd & 1) ^ (dd >> 1)
        a = self.item(pos) & mask
        sign = a >> (bits - 1) & 1
        if kind in (SH_RCL, SH_RCR):
            # always rotates: the low 4 bits of the count, 0 for 16
            count = count & 15 or 16
        if count == 0:
            r = a
        else:
            if kind == SH_SHL:
                r = a << count & mask if count < bits else 0
                self.c = a >> (bits - count) & 1 if count <= bits else 0
            elif kind == SH_SHR:
                r = a >> count if count < bits else 0
                self.c = a >> (count - 1) & 1 if count <= bits else 0
            elif kind == SH_SAR:
                fill = mask if sign else 0
                r = (fill << (bits - count) | a >> count) & mask if count < bits else fill
                self.c = a >> (count - 1) & 1 if count <= bits else sign
            elif kind in (SH_ROL, SH_ROR):
                n = count % bits
                if kind == SH_ROR:
                    n = (bits - n) % bits
                r = (a << n | a >> (bits - n)) & mask
                self.c = r & 1 if kind == SH_ROL else r >> (bits - 1)
            else:
                width = bits + 1
                n = count % width
                if kind == SH_RCR:
                    n = (width - n) % width
                full = self.c << bits | a
                full = (full << n | full >> (width - n)) & (1 << width) - 1
                r = full & mask
                self.c = full >> bits
            self.v = 0
        self.z = int(r == 0)
        self.n = r >> (bits - 1) & 1
        self.p = int(bin(r).count('1') % 2 == 0)
        if not v:
            self.ld_put(dd, r)
        elif dd == 0:
            self.wr16(self.psp, r)
        elif dd == 1:
            self.wr16(self.psp + 2, r)
        else:
            self.psp += 2
            self.wr16(self.psp, r)

    def unary(self, op, dd, s):
        # the operand is the item the ALU ops use: TOS for dd 0 and 3,
        # NOS for 1 and 2
        pos = (dd & 1) ^ (dd >> 1)
        bits = 8 if s else 16
        mask = (1 << bits) - 1
        a = self.item(pos) & mask
        if op == U_NOT:
            r = ~a & mask
        elif op == U_NEG:
            r = -a & mask
            self.c = int(a == 0)
            self.v = int(a == 1 << (bits - 1))
        elif op == U_SXT:
            r = sext(a & 0xff, 8) & 0xffff
        elif op == U_BSWAP:
            r = (a & 0xff) << 8 | a >> 8
        self.z = int(r == 0)
        self.n = r >> (bits - 1) & 1
        self.p = int(bin(r).count('1') % 2 == 0)
        self.ld_put(dd, r)

    def alu(self, op, dd, b, s):
        """
        CMP and TST are SUB and AND without the write; like ST, dd 0 and 2
        use TOS, dd 1 and 3 use NOS, and dd 2 and 3 remove that item.
        """
        bits = 8 if s else 16
        mask = (1 << bits) - 1
        cmp = op in (OP_CMP, OP_TST)
        pos = dd & 1 if cmp else 1 if dd in (1, 2) else 0
        if op == OP_CMP:
            op = OP_SUB
        elif op == OP_TST:
            op = OP_AND
        a = self.item(pos) & mask
        b &= mask
        sa, sb = sext(a, bits), sext(b, bits)
        if op in (OP_ADD, OP_ADC, OP_SUB, OP_SBC):
            if op == OP_ADD:
                cin = 0
            elif op == OP_SUB:
                cin = 1
            else:
                cin = self.c
            if op in (OP_ADD, OP_ADC):
                full = a + b + cin
                signed = sa + sb + cin
            else:
                full = a + (~b & mask) + cin
                signed = sa - sb - (1 - cin)
            r = full & mask
            self.c = full >> bits & 1
            self.v = int(not -(1 << (bits - 1)) <= signed < (1 << (bits - 1)))
        else:
            r = {
                OP_AND: a & b,
                OP_OR: a | b,
                OP_XOR: a ^ b,
                OP_BIC: a & ~b & mask,
            }[op]
        self.z = int(r == 0)
        self.n = r >> (bits - 1) & 1
        self.p = int(bin(r).count('1') % 2 == 0)
        if cmp:
            if dd == 2:
                self.psp += 2
            elif dd == 3:
                top = self.item(0)
                self.psp += 2
                self.wr16(self.psp, top)
            return
        if dd == 0:
            self.wr16(self.psp, r)
        elif dd == 1:
            self.wr16(self.psp + 2, r)
        else:
            self.psp += 2
            self.wr16(self.psp, r)

    def ld_src(self, words, fmt, s, next_ip):
        w = words[0]
        if fmt == 0:
            return sext(w & 0xff, 8) & 0xffff
        if fmt == 1:
            return self.rd((self.pfp + 2 * sext(w & 0x7f, 7)) & 0xffff, s)
        mode, x, e = gen_operand(w)
        if mode == 0:
            regs = {
                REG_MBP: self.mbp,
                REG_MIDX: self.midx,
                REG_SP: self.psp,
                REG_NULL: 0,
                REG_SR: self.flags,
                REG_IP: next_ip,
            }
            return regs[x] & (0xff if s else 0xffff)
        if mode == 4:
            return words[1] & (0xff if s else 0xffff)
        if mode in (1, 2, 3):
            ea = self.ea_ptr(mode, s)
        elif mode == 5:
            ea = words[1]
        elif mode == 7:
            ea = self.item(0)
        else:
            ea = (self.psp + 2 * x) & 0xffff
        v = self.rd(ea, s)
        if s and e and v & 0x80:
            v |= 0xff00
        if mode in (1, 2, 3):
            self.ptr_after(mode, s)
        return v

    def ld_put(self, dd, v):
        if dd == 0:
            self.wr16(self.psp, v)
        elif dd == 1:
            self.wr16(self.psp + 2, v)
        elif dd == 2:
            self.psp -= 2
            self.wr16(self.psp, v)
        else:
            top = self.item(0)
            self.psp -= 2
            self.wr16(self.psp, top)
            self.wr16(self.psp + 2, v)

    def swap(self, words, fmt, s, dd):
        """TOS or NOS (dd 0, 1) and a memory word or byte exchange values."""
        w = words[0]
        old = self.item(dd)
        mode, x, _ = (0, 0, 0) if fmt == 1 else gen_operand(w)
        if fmt == 1:
            ea = (self.pfp + 2 * sext(w & 0x7f, 7)) & 0xffff
        elif mode in (1, 2, 3):
            ea = self.ea_ptr(mode, s)
        elif mode == 5:
            ea = words[1]
        elif mode == 7:
            ea = self.item(0)
        else:
            ea = (self.psp + 2 * x) & 0xffff     # mode 6, k >= 4
        new = self.rd(ea, s)
        self.wr(ea, s, old)
        if fmt == 2 and mode in (1, 2, 3):
            self.ptr_after(mode, s)
        self.ld_put(dd, new)

    def st_dst(self, words, fmt, s, v):
        w = words[0]
        if fmt == 1:
            self.wr((self.pfp + 2 * sext(w & 0x7f, 7)) & 0xffff, s, v)
            return
        mode, x, _ = gen_operand(w)
        if mode == 0:
            v8 = v & 0xff if s else v
            if x == REG_MBP:
                self.mbp = v8
            elif x == REG_MIDX:
                self.midx = v8
            elif x == REG_SP:
                self.psp = v8
            elif x == REG_SR:
                self.set_flags(v8)
            elif x == REG_IP:
                return v8
        elif mode in (1, 2, 3):
            ea = self.ea_ptr(mode, s)
            self.wr(ea, s, v)
            self.ptr_after(mode, s)
        elif mode == 5:
            self.wr(words[1], s, v)
        elif mode == 6:
            self.wr((self.psp + 2 * x) & 0xffff, s, v)
        elif mode == 7:
            # dd 1 and 3 only: TOS is still the address after the pop
            self.wr(self.item(0), s, v)


# ---- random program generator ----

class Program:
    def __init__(self, rng, prefix=()):
        # `prefix`: words in front of the program (a stub for the system
        # context); the program starts with its JMP right after them
        self.prefix = list(prefix)
        self.rng = rng
        self.words = []
        self.ref = Ref()
        self.depth = 0

        # while False the instructions are only collected (a function body
        # is executed later, from a call)
        self.live = True
        self.max_depth = MAX_DEPTH

        # inside a function: (argument words from PFP up, variadic)
        self.frame = None
        self.funcs = []

    @property
    def addr(self):
        return 2 * len(self.words)

    def emit(self, ins, delta=0):
        self.words += ins
        self.depth += delta
        if self.live:
            self.ref.step(ins, self.addr)
            assert self.depth == (PSP0 - self.ref.psp) // 2, (
                self.depth, self.ref.psp
            )

    def emit_call(self, ins, delta):
        self.words += ins
        self.depth += delta
        if self.live:
            target = self.ref.step(ins, self.addr)
            self.ref.run(self.words, target, self.addr)
            assert self.depth == (PSP0 - self.ref.psp) // 2, (
                self.depth, self.ref.psp
            )

    def push_imm8(self, v):
        self.emit(i_imm8(OP_LD, 2, v), 1)

    def push_imm16(self, v):
        self.emit(i_gen(OP_LD, 2, 0, 4, 0, v), 1)

    def pop_to_reg(self, reg):
        self.emit(i_gen(OP_ST, 2, 0, 0, reg), -1)

    def set_far(self):
        r = self.rng
        self.push_imm16(FAR + 2 * r.randrange(0x40))
        self.pop_to_reg(REG_MBP)
        self.push_imm8(r.choice([0, 0, 2, 4, 1, 3]))
        self.pop_to_reg(REG_MIDX)
        if r.random() < 0.3:
            self.emit(i_addp(r.randrange(2), r.choice([-8, -4, -2, 2, 4, 8])))

    # dd choices that keep 0 <= depth <= MAX_DEPTH. LD takes (dd, depth change).
    def ld_dds(self):
        d = self.depth
        out = []
        if d >= 1:
            out.append(0)
        if d >= 2:
            out.append(1)
        if d < self.max_depth:
            out.append(2)
        if 1 <= d < self.max_depth:
            out.append(3)
        return out

    def st_dds(self):
        d = self.depth
        out = []
        if d >= 1:
            out += [0, 2]
        if d >= 2:
            out += [1, 3]
        return out

    def addr_far(self):
        r = self.rng
        return r.choice([FAR + r.randrange(0x100), TOP + r.randrange(0x60)])

    def src_ins(self, op, dd, s, d):
        """
        A random instruction `op` with a source operand; d = depth before it.
        """
        r = self.rng
        kind = r.choice(['imm8', 'reg', 'imm16', 'pfp', 'ptr', 'abs', 'sp'])
        if kind == 'imm8':
            return i_imm8(op, dd, r.getrandbits(8))
        if kind == 'reg':
            regs = [REG_MBP, REG_MIDX, REG_SP, REG_NULL, REG_SR, REG_IP]
            return i_gen(op, dd, s, 0, r.choice(regs))
        if kind == 'imm16':
            return i_gen(op, dd, s, 4, 0, r.getrandbits(16))
        if kind == 'pfp':
            return i_pfp(
                op,
                dd,
                s,
                r.randrange(
                    -min(d, 64), 64 if self.frame is None else max(self.frame[0], 1)
                )
            )
        if kind == 'ptr':
            return i_gen(op, dd, s, r.choice([1, 2, 3]), s and r.getrandbits(1))
        if kind == 'abs':
            return i_gen(op, dd, s, 5, 0, self.addr_far(), s and r.getrandbits(1))
        if d < 1:
            return None
        k = r.randrange(min(d, 16))
        return i_gen(op, dd, s, 6, k, None, k >= 4 and s and r.getrandbits(1))

    def ld_one(self, neutral=False):
        r = self.rng
        dds = [dd for dd in self.ld_dds() if dd < 2 or not neutral]
        if not dds:
            return False
        dd = r.choice(dds)
        ins = self.src_ins(OP_LD, dd, r.getrandbits(1), self.depth)
        if ins is None:
            return False
        self.emit(ins, 1 if dd >= 2 else 0)
        return True

    def alu_one(self, neutral=False):
        r = self.rng
        d = self.depth
        dds = [0] if d >= 1 else []
        if d >= 2:
            dds += [1] if neutral else [1, 2, 3]
        if not dds:
            return False
        dd = r.choice(dds)
        op = r.choice(list(range(OP_ADD, OP_BIC + 1)) + [OP_CMP, OP_TST] * 2)
        ins = self.src_ins(op, dd, r.getrandbits(1), d)
        if ins is None:
            return False
        self.emit(ins, -1 if dd >= 2 else 0)
        return True

    def unary_one(self):
        d = self.depth
        if d < 1:
            return False
        r = self.rng
        dds = [0]
        if d >= 2:
            dds += [1]
        if d >= 2 and d < self.max_depth:
            dds += [2]
        if d < self.max_depth:
            dds += [3]
        dd = r.choice(dds)
        op = r.randrange(4)
        s = 0 if op in (U_SXT, U_BSWAP) else r.getrandbits(1)
        self.emit(i_unary(op, dd, s), 1 if dd >= 2 else 0)
        return True

    def shift_one(self):
        d = self.depth
        if d < 1:
            return False
        r = self.rng
        kind = r.choice([SH_SHL, SH_SHR, SH_SAR, SH_ROL, SH_ROR, SH_RCL, SH_RCR])
        s = r.getrandbits(1)
        if r.getrandbits(1) or d < 2:
            dds = [0]
            if d >= 2:
                dds += [1]
            if d >= 2 and d < self.max_depth:
                dds += [2]
            if d < self.max_depth:
                dds += [3]
            dd = r.choice(dds)
            count = r.choice([r.randrange(1, 17), r.randrange(1, 17), 8, 16, 1])
            self.emit(i_shift(kind, dd, s, count), 1 if dd >= 2 else 0)
        else:
            # the count is TOS: any word, so that most of the counts saturate
            dd = r.choice([0, 1, 2, 3])
            self.emit(i_shift(kind, dd, s), -1 if dd >= 2 else 0)
        return True

    def muldiv_one(self):
        d = self.depth
        if d < 2:
            return False
        r = self.rng
        div = r.getrandbits(1)
        s = r.getrandbits(1)
        # the divisor is only known while the program runs in the reference;
        # a function body has no division
        if div and not (
            self.live and self.ref.item(0) & (0xff if s else 0xffff)
        ):
            div = 0
        self.emit(i_muldiv(div, r.getrandbits(1), s))
        return True

    def lea_one(self):
        r = self.rng
        dds = self.ld_dds()
        if not dds:
            return False
        dd = r.choice(dds)
        self.emit(i_lea(dd, r.randrange(-64, 64)), 1 if dd >= 2 else 0)
        return True

    def block_tos_ptr(self):
        """
        A pointer into the data area pushed, and one instruction through
        [tos] (the pointer stays or goes, as the dd of the instruction
        says).
        """
        r = self.rng
        if self.depth + 2 > self.max_depth:
            return False
        s = r.getrandbits(1)
        self.push_imm16(self.addr_far())
        d = self.depth      # with the pointer
        kind = r.choice(['ld', 'alu', 'st', 'swap'])
        if kind == 'ld':
            dds = [0, 2, 3] + ([1] if d >= 2 else [])
            dd = r.choice(dds)
            self.emit(i_gen(OP_LD, dd, s, 7, 0), 1 if dd >= 2 else 0)
        elif kind == 'alu':
            dds = [0] + ([1, 2, 3] if d >= 2 else [])
            dd = r.choice(dds)
            op = r.randrange(OP_ADD, OP_BIC + 1)
            self.emit(i_gen(op, dd, s, 7, 0), -1 if dd >= 2 else 0)
        elif kind == 'st' and d >= 2:
            dd = r.choice([1, 3])
            self.emit(i_gen(OP_ST, dd, s, 7, 0), -1 if dd == 3 else 0)
        else:
            dd = r.choice([0, 1] if d >= 2 else [0])
            self.emit(i_gen(OP_SWAP, dd, s, 7, 0))
        return True

    def swap_one(self):
        r = self.rng
        d = self.depth
        if d < 1:
            return False
        dd = r.choice([0, 1] if d >= 2 else [0])
        s = r.getrandbits(1)
        kinds = ['pfp', 'ptr', 'abs']
        if d >= 5:
            kinds.append('sp')
        kind = r.choice(kinds)
        if kind == 'pfp':
            n = self.pfp_store(d)
            if n is None:
                kind = 'abs'
            else:
                self.emit(i_pfp(OP_SWAP, dd, s, n))
                return True
        if kind == 'ptr':
            # the bit that makes a byte load sign-extending does nothing here
            self.emit(
                i_gen(OP_SWAP, dd, s, r.choice([1, 2, 3]), s and r.getrandbits(1))
            )
        elif kind == 'abs':
            self.emit(i_gen(OP_SWAP, dd, s, 5, 0, self.addr_far()))
        else:
            self.emit(i_gen(OP_SWAP, dd, s, 6, r.randrange(4, min(d, 16))))
        return True

    def pfp_store(self, d):
        """
        A word offset for a store relative to PFP, or None. In a function
        it must not hit the count: only the stack items below PFP and the
        arguments.
        """
        r = self.rng
        if self.frame is None:
            return r.randrange(-min(d, 64), 64)
        words, variadic = self.frame
        cands = list(
            range(-min(d, 64), 0)
        ) + list(
            range(1 if variadic else 0, words)
        )
        return r.choice(cands) if cands else None

    def st_one(self, neutral=False):
        r = self.rng
        dds = [dd for dd in self.st_dds() if dd < 2 or not neutral]
        if not dds:
            return False
        dd = r.choice(dds)

        # depth when the destination is evaluated
        d = self.depth - (1 if dd >= 2 else 0)
        s = r.getrandbits(1)
        kinds = ['pfp', 'ptr', 'abs', 'null', 'sr']
        if d >= 1:
            kinds.append('sp')
        kind = r.choice(kinds)
        if kind == 'pfp' and self.pfp_store(d) is None:
            kind = 'null'
        if kind == 'null':
            ins = i_gen(OP_ST, dd, s, 0, REG_NULL)
        elif kind == 'sr':
            ins = i_gen(OP_ST, dd, s, 0, REG_SR)
        elif kind == 'pfp':
            ins = i_pfp(OP_ST, dd, s, self.pfp_store(d))
        elif kind == 'ptr':
            ins = i_gen(OP_ST, dd, s, r.choice([1, 2, 3]), 0)
        elif kind == 'abs':
            ins = i_gen(OP_ST, dd, s, 5, 0, self.addr_far())
        else:
            ins = i_gen(OP_ST, dd, s, 6, r.randrange(min(d, 16)))
        self.emit(ins, -1 if dd >= 2 else 0)
        return True

    def block_set_sp(self):
        """
        Push SP, then pop it back into SP: PSP unchanged, but a full SET_PSP.
        """
        self.emit(i_gen(OP_LD, 2, 0, 0, REG_SP), 1)
        self.emit(i_gen(OP_ST, 2, 0, 0, REG_SP), -1)

    def block_alias(self):
        """Pointer into the live stack: PSP -> MBP, MIDX = 2j, one access."""
        r = self.rng
        d = self.depth
        if d < 3 or d > self.max_depth - 3:
            return False
        self.emit(i_gen(OP_LD, 2, 0, 0, REG_SP), 1)
        self.pop_to_reg(REG_MBP)
        j = r.randrange(min(d, 6)) if r.random() < 0.8 else r.randrange(d)
        self.push_imm8(2 * j)
        self.pop_to_reg(REG_MIDX)
        s = r.getrandbits(1)
        if r.random() < 0.5:
            mode = r.choice([1, 2, 3]) if j >= 1 else r.choice([1, 2])
            if r.random() < 0.4:
                dd = r.choice([0, 1, 2, 3])
                self.emit(
                    i_gen(
                        r.randrange(OP_ADD, OP_BIC + 1),
                        dd, s, mode, s and r.getrandbits(1)
                    ), -1 if dd >= 2 else 0
                )
            else:
                dd = r.choice(self.ld_dds())
                self.emit(
                    i_gen(
                        OP_LD, dd, s, mode, s and r.getrandbits(1)
                    ), 1 if dd >= 2 else 0
                )
        else:
            dds = [dd for dd in self.st_dds() if dd < 2 or j >= 1]
            if not dds:
                self.set_far()
                return True
            dd = r.choice(dds)
            if j >= 2 or (dd < 2 and j >= 1):
                mode = r.choice([1, 2, 3])
            else:
                mode = r.choice([1, 2])
            self.emit(i_gen(OP_ST, dd, s, mode, 0), -1 if dd >= 2 else 0)
        self.set_far()
        return True

    def block_skip(self):
        """BRA or JMP over a few junk words that must never be fetched."""
        r = self.rng
        k = r.randrange(1, 5)
        if r.random() < 0.5:
            self.emit_plain(i_bra(k))
        else:
            self.emit_plain(i_misc(MISC_JMP_ABS, extra=self.addr + 4 + 2 * k))
        self.words += [r.getrandbits(16) for _ in range(k)]

    def block_skip_cc(self):
        """
        SKIP cc and an instruction that does not change the stack depth,
        so that the depth is the same whether it runs or not.
        """
        r = self.rng
        if self.depth < 1:
            return
        cc = r.randrange(16)
        self.emit_plain(i_skip(cc))
        live = self.live
        if live and self.ref.cond(cc):
            self.live = False       # skipped: only put the words in
        while True:
            x = r.random()
            if x < 0.4:
                done = self.ld_one(neutral=True)
            elif x < 0.8:
                done = self.alu_one(neutral=True)
            else:
                done = self.st_one(neutral=True)
            if done:
                break
        self.live = live

    def emit_plain(self, ins):
        """
        Instructions the reference does not execute here:
        the jump skips the junk.
        """
        self.words += ins

    def call_func(self, func):
        r = self.rng
        addr, nargs, variadic, words = func
        for _ in range(nargs):
            self.push_imm8(r.getrandbits(8))
        if variadic:
            self.push_imm8(words)       # the count counts itself
        form = r.choice(['rel', 'abs', 'pop'])
        rel = (addr - (self.addr + 2)) // 2
        if form == 'rel' and not -1024 <= rel < 1024:
            form = 'abs'
        if form == 'rel':
            self.emit_call(i_call_rel(rel), -words)
        elif form == 'abs':
            self.emit_call(i_misc(MISC_CALL_ABS, extra=addr), -words)
        else:
            self.push_imm16(addr)
            self.emit_call(i_misc(MISC_CALL_POP), -words - 1)

    def make_func(self):
        r = self.rng
        nargs = r.randint(0, 3)
        variadic = r.random() < 0.4
        if r.random() < 0.1:
            # many arguments: exercises the whole width of RET #n
            nargs, variadic = r.randint(30, 40), False
        words = nargs + 1 if variadic else nargs
        n_locals = r.randint(0, 4)
        addr = self.addr
        saved = (self.live, self.depth, self.max_depth, self.frame)
        self.live, self.depth, self.max_depth = False, 0, 12
        self.frame = (words, variadic)
        if n_locals:
            # the locals are the top stack items: reserve them,
            # then give each a value
            self.emit(i_addp(2, -2 * n_locals), n_locals)
            for k in range(n_locals):
                self.push_imm8(r.getrandbits(8))
                self.emit(i_pfp(OP_ST, 2, 0, k - n_locals), -1)
        for _ in range(r.randint(2, 20)):
            self.random_op(allow_calls=True)
        self.set_far()
        if self.depth:
            # release the locals and what is left on the stack: PSP = PFP
            self.emit(i_addp(2, 2 * self.depth), -self.depth)
        if variadic:
            self.emit(i_misc(MISC_RET_VAR))
        else:
            self.emit(i_misc(MISC_RET_N, nargs))
        self.live, self.depth, self.max_depth, self.frame = saved
        self.funcs.append((addr, nargs, variadic, words))

    def random_op(self, allow_calls):
        r = self.rng
        x = r.random() * 1.07
        match x:
            case x if x < 0.04:
                self.block_set_sp()
            case x if x < 0.14:
                self.block_alias()
            case x if x < 0.34:
                while not self.ld_one():
                    pass
            case x if x < 0.50:
                while not self.alu_one():
                    self.ld_one()
            case x if (
                x < 0.58 and allow_calls
                and self.funcs
                and self.depth + 6 <= self.max_depth
            ):
                func = r.choice(self.funcs)
                if func[3] <= 8 or (self.frame is None and self.depth <= 4):
                    self.call_func(func)
            case x if x < 0.62:
                self.block_skip()
            case x if x < 0.70:
                self.block_skip_cc()
            case x if x < 0.74:
                self.emit(r.choice([i_setb, i_clrb])(r.getrandbits(8)))
            case x if x < 0.77:
                if not self.unary_one():
                    self.ld_one()
            case x if x < 0.82:
                if not self.shift_one():
                    self.ld_one()
            case x if x < 0.86:
                if not self.swap_one():
                    self.ld_one()
            case x if x < 0.89:
                if not self.muldiv_one():
                    self.ld_one()
            case x if x < 0.90:
                self.emit(i_misc(MISC_NOP))
            case x if x < 0.93:
                if not self.lea_one():
                    self.ld_one()
            case x if x < 0.97:
                if not self.block_tos_ptr():
                    self.ld_one()
            case _:
                while not self.st_one():
                    self.ld_one()

    def build(self, n):
        r = self.rng

        # code layout: JMP main, the functions, then main
        self.live = False
        p = len(self.prefix)
        self.words = self.prefix + [0, 0]
        for _ in range(r.randint(0, 4)):
            self.make_func()
        self.words[p:p + 2] = i_misc(MISC_JMP_ABS, extra=self.addr)
        self.live = True
        self.set_far()
        while self.depth < 4:
            self.push_imm8(r.getrandbits(8))
        for _ in range(n):
            self.random_op(allow_calls=True)
        # epilogue: dump MBP, MIDX and the whole stack to OUT...
        self.emit(i_gen(OP_LD, 2, 0, 0, REG_MBP), 1)
        self.emit(i_gen(OP_LD, 2, 0, 0, REG_MIDX), 1)
        self.push_imm16(OUT)
        self.pop_to_reg(REG_MBP)
        self.push_imm8(0)
        self.pop_to_reg(REG_MIDX)
        n_dump = self.depth
        for _ in range(n_dump):
            self.emit(i_gen(OP_ST, 2, 0, 2, 0), -1)
        assert self.depth == 0
        self.end = self.addr
        self.dump_words = n_dump
        return self


def compare_ranges(program):
    """Word-address ranges whose contents the CPU must reproduce exactly."""
    return [((FAR - 0x400) >> 1, (FAR + 0x400) >> 1),
            (OUT >> 1, (OUT >> 1) + program.dump_words + 1),
            (0xf000 >> 1, 0xf100 >> 1)]
