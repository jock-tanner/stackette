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
SFP0 = 0xf000
FAR = 0x2000        # data area for pointer accesses
OUT = 0x3000        # result dump
TOP = 0xf008        # memory above the stack base, free to use
MAX_DEPTH = 40

OP_ADD, OP_ADC, OP_SUB, OP_SBC, OP_AND, OP_OR, OP_XOR, OP_BIC = range(8)
OP_LD = 8
OP_ST = 9
U_NOT, U_NEG, U_SXT, U_BSWAP, U_RLC, U_RRC = range(6)
REG_TOS, REG_NOS, REG_MBP, REG_MIDX, REG_SP, REG_NULL, REG_SR, REG_IP = 0, 1, 2, 3, 4, 5, 6, 15
SR_MASK = 0x1f


def junk(byte_addr):
    """Contents of memory nobody has written (the same for model and CPU test)."""
    return (byte_addr * 2654435761 >> 7) & 0xffff


def sext(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


# ---- assembler ----

def i_imm8(op, dd, imm):
    return [(op << 10) | (dd << 8) | (imm & 0xff)]


def i_pfp(op, dd, s, f):
    return [(1 << 14) | (op << 10) | (dd << 8) | (s << 7) | (f & 0x7f)]


def i_gen(op, dd, s, mode, x, extra=None):
    w = (2 << 14) | (op << 10) | (dd << 8) | (s << 7) | (mode << 4) | x
    return [w] if extra is None else [w, extra & 0xffff]


# control format 11
MISC_RET_N, MISC_CALL_ABS, MISC_JMP_ABS, MISC_CALL_POP, MISC_RET_VAR = 1, 5, 6, 7, 9


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
        self.sfp = SFP0
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
        w = self.rd16(a)
        if not s:
            return w
        return (w >> 8) & 0xff if a & 1 else w & 0xff

    def wr(self, a, s, v):
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
        w = words[0]
        fmt = w >> 14
        op = (w >> 10) & 15
        dd = (w >> 8) & 3
        if fmt == 3:
            return self.ctl(words, next_ip)
        s = (w >> 7) & 1 if fmt else 0
        if op == OP_LD:
            v = self.ld_src(words, fmt, s, next_ip)
            self.ld_put(dd, v)
        elif OP_ADD <= op <= OP_BIC:
            b = self.ld_src(words, fmt, s, next_ip)
            if fmt == 0 and op >= OP_AND:
                b = w & 0xff
            self.alu(op, dd, b, s)
        elif op == OP_ST and fmt != 0:
            v = self.item(dd & 1)
            if dd == 2:
                self.psp += 2
            elif dd == 3:
                top = self.item(0)
                self.psp += 2
                self.wr16(self.psp, top)
            self.st_dst(words, fmt, s, v)

    @staticmethod
    def length(w):
        """Words in the instruction that starts with w."""
        fmt = w >> 14
        if fmt == 2:
            return 2 if (w >> 4) & 7 in (4, 5) else 1
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
        self.rsp = (self.rsp - 2) & 0xffff
        self.wr16(self.rsp, ret)
        self.psp -= 2
        self.wr16(self.psp, self.sfp)
        self.sfp = self.psp
        return target

    def ret(self, n):
        if n is None:
            n = self.rd16(self.sfp + 2)
        link = self.rd16(self.sfp)
        self.psp = (self.sfp + 2 + 2 * n) & 0xffff
        self.sfp = link
        ip = self.rd16(self.rsp)
        self.rsp = (self.rsp + 2) & 0xffff
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

    def unary(self, op, dd, s):
        # the operand is the item the ALU ops use: TOS for dd 0 and 3, NOS for 1 and 2
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
        elif op == U_RLC:
            full = a << 1 | self.c
            r = full & mask
            self.c = full >> bits & 1
            self.v = (a >> (bits - 1) ^ r >> (bits - 1)) & 1
        else:
            r = self.c << (bits - 1) | a >> 1
            self.c = a & 1
        self.z = int(r == 0)
        self.n = r >> (bits - 1) & 1
        self.p = int(bin(r).count('1') % 2 == 0)
        self.ld_put(dd, r)

    def alu(self, op, dd, b, s):
        bits = 8 if s else 16
        mask = (1 << bits) - 1
        pos = 1 if dd in (1, 2) else 0
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
            r = {OP_AND: a & b, OP_OR: a | b, OP_XOR: a ^ b, OP_BIC: a & ~b & mask}[op]
        self.z = int(r == 0)
        self.n = r >> (bits - 1) & 1
        self.p = int(bin(r).count('1') % 2 == 0)
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
            return self.rd((self.sfp + 2 * sext(w & 0x7f, 7)) & 0xffff, s)
        mode = (w >> 4) & 7
        x = w & 15
        if mode == 0:
            regs = {REG_TOS: self.item(0), REG_NOS: self.item(1), REG_MBP: self.mbp,
                    REG_MIDX: self.midx, REG_SP: self.psp, REG_NULL: 0, REG_SR: self.flags, REG_IP: next_ip}
            return regs[x] & (0xff if s else 0xffff)
        if mode in (1, 2, 3):
            ea = self.ea_ptr(mode, s)
            v = self.rd(ea, s)
            if s and (x & 1) and v & 0x80:
                v |= 0xff00
            self.ptr_after(mode, s)
            return v
        if mode == 4:
            return words[1] & (0xff if s else 0xffff)
        if mode == 5:
            return self.rd(words[1], s)
        return self.rd((self.psp + 2 * x) & 0xffff, s)      # mode 6

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

    def st_dst(self, words, fmt, s, v):
        w = words[0]
        if fmt == 1:
            self.wr((self.sfp + 2 * sext(w & 0x7f, 7)) & 0xffff, s, v)
            return
        mode = (w >> 4) & 7
        x = w & 15
        if mode == 0:
            v8 = v & 0xff if s else v
            if x == REG_TOS:
                self.wr16(self.psp, v8)
            elif x == REG_NOS:
                self.wr16(self.psp + 2, v8)
            elif x == REG_MBP:
                self.mbp = v8
            elif x == REG_MIDX:
                self.midx = v8
            elif x == REG_SP:
                self.psp = v8
            elif x == REG_SR:
                self.set_flags(v8)
        elif mode in (1, 2, 3):
            ea = self.ea_ptr(mode, s)
            self.wr(ea, s, v)
            self.ptr_after(mode, s)
        elif mode == 5:
            self.wr(words[1], s, v)
        elif mode == 6:
            self.wr((self.psp + 2 * x) & 0xffff, s, v)


# ---- random program generator ----

class Program:
    def __init__(self, rng):
        self.rng = rng
        self.words = []
        self.ref = Ref()
        self.depth = 0
        # while False the instructions are only collected (a function body
        # is executed later, from a call)
        self.live = True
        self.max_depth = MAX_DEPTH
        # inside a function: (argument words above the link, variadic)
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
            assert self.depth == (PSP0 - self.ref.psp) // 2, (self.depth, self.ref.psp)

    def emit_call(self, ins, delta):
        self.words += ins
        self.depth += delta
        if self.live:
            target = self.ref.step(ins, self.addr)
            self.ref.run(self.words, target, self.addr)
            assert self.depth == (PSP0 - self.ref.psp) // 2, (self.depth, self.ref.psp)

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
        """A random instruction `op` with a source operand; d = depth before it."""
        r = self.rng
        kind = r.choice(['imm8', 'reg', 'imm16', 'pfp', 'ptr', 'abs', 'sp'])
        if kind == 'imm8':
            return i_imm8(op, dd, r.getrandbits(8))
        if kind == 'reg':
            regs = [REG_MBP, REG_MIDX, REG_SP, REG_NULL, REG_SR, REG_IP]
            if d >= 1:
                regs.append(REG_TOS)
            if d >= 2:
                regs.append(REG_NOS)
            return i_gen(op, dd, s, 0, r.choice(regs))
        if kind == 'imm16':
            return i_gen(op, dd, s, 4, 0, r.getrandbits(16))
        if kind == 'pfp':
            return i_pfp(op, dd, s, r.randrange(-min(d, 64), 64 if self.frame is None else self.frame[0] + 1))
        if kind == 'ptr':
            return i_gen(op, dd, s, r.choice([1, 2, 3]), s and r.getrandbits(1))
        if kind == 'abs':
            return i_gen(op, dd, s, 5, 0, self.addr_far())
        if d < 1:
            return None
        return i_gen(op, dd, s, 6, r.randrange(min(d, 16)))

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
        op = r.randrange(OP_ADD, OP_BIC + 1)
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
        op = r.randrange(6)
        s = 0 if op in (U_SXT, U_BSWAP) else r.getrandbits(1)
        self.emit(i_unary(op, dd, s), 1 if dd >= 2 else 0)
        return True

    def pfp_store(self, d):
        """A word offset for a store relative to PFP, or None. In a function it must not
        hit the saved PFP or the count: only the stack items below PFP and the arguments."""
        r = self.rng
        if self.frame is None:
            return r.randrange(-min(d, 64), 64)
        words, variadic = self.frame
        cands = list(range(-min(d, 64), 0)) + list(range(2 if variadic else 1, words + 1))
        return r.choice(cands) if cands else None

    def st_one(self, neutral=False):
        r = self.rng
        dds = [dd for dd in self.st_dds() if dd < 2 or not neutral]
        if not dds:
            return False
        dd = r.choice(dds)
        d = self.depth - (1 if dd >= 2 else 0)     # depth when the destination is evaluated
        s = r.getrandbits(1)
        kinds = ['pfp', 'ptr', 'abs', 'null', 'sr']
        if d >= 1:
            kinds += ['tos', 'sp']
        if d >= 2:
            kinds.append('nos')
        kind = r.choice(kinds)
        if kind == 'pfp' and self.pfp_store(d) is None:
            kind = 'null'
        if kind == 'null':
            ins = i_gen(OP_ST, dd, s, 0, REG_NULL)
        elif kind == 'sr':
            ins = i_gen(OP_ST, dd, s, 0, REG_SR)
        elif kind == 'tos':
            ins = i_gen(OP_ST, dd, s, 0, REG_TOS)
        elif kind == 'nos':
            ins = i_gen(OP_ST, dd, s, 0, REG_NOS)
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
        """push SP, then pop it back into SP: PSP unchanged, but a full SET_PSP."""
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
                self.emit(i_gen(r.randrange(OP_ADD, OP_BIC + 1), dd, s, mode, s and r.getrandbits(1)),
                          -1 if dd >= 2 else 0)
            else:
                dd = r.choice(self.ld_dds())
                self.emit(i_gen(OP_LD, dd, s, mode, s and r.getrandbits(1)), 1 if dd >= 2 else 0)
        else:
            dds = [dd for dd in self.st_dds() if dd < 2 or j >= 1]
            if not dds:
                self.set_far()
                return True
            dd = r.choice(dds)
            mode = r.choice([1, 2, 3]) if j >= 2 or (dd < 2 and j >= 1) else r.choice([1, 2])
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
        """SKIP cc and an instruction that does not change the stack depth, so that
        the depth is the same whether it runs or not."""
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
        """Instructions the reference does not execute here: the jump skips the junk."""
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
            # the locals are the top stack items: reserve them, then give each a value
            self.emit(i_addp(2, -2 * n_locals), n_locals)
            for k in range(n_locals):
                self.push_imm8(r.getrandbits(8))
                self.emit(i_pfp(OP_ST, 2, 0, k - n_locals), -1)
        for _ in range(r.randint(2, 20)):
            self.random_op(allow_calls=True)
        self.set_far()
        if variadic:
            self.emit(i_misc(MISC_RET_VAR))
        else:
            self.emit(i_misc(MISC_RET_N, nargs))
        self.live, self.depth, self.max_depth, self.frame = saved
        self.funcs.append((addr, nargs, variadic, words))

    def random_op(self, allow_calls):
        r = self.rng
        x = r.random()
        if x < 0.04:
            self.block_set_sp()
        elif x < 0.14:
            self.block_alias()
        elif x < 0.34:
            while not self.ld_one():
                pass
        elif x < 0.50:
            while not self.alu_one():
                self.ld_one()
        elif x < 0.58 and allow_calls and self.funcs and self.depth + 6 <= self.max_depth:
            func = r.choice(self.funcs)
            if func[3] <= 8 or (self.frame is None and self.depth <= 4):
                self.call_func(func)
        elif x < 0.62:
            self.block_skip()
        elif x < 0.70:
            self.block_skip_cc()
        elif x < 0.74:
            self.emit(r.choice([i_setb, i_clrb])(r.getrandbits(8)))
        elif x < 0.80:
            if not self.unary_one():
                self.ld_one()
        else:
            while not self.st_one():
                self.ld_one()

    def build(self, n):
        r = self.rng
        # code layout: JMP main, the functions, then main
        self.live = False
        self.words = [0, 0]
        for _ in range(r.randint(0, 4)):
            self.make_func()
        self.words[0:2] = i_misc(MISC_JMP_ABS, extra=self.addr)
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
