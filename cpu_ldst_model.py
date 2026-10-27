"""
Pure-Python side of the LD/ST/ALU test: assembler, reference interpreter and
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
REG_TOS, REG_NOS, REG_MBP, REG_MIDX, REG_SP, REG_NULL, REG_IP = 0, 1, 2, 3, 4, 5, 15


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


# ---- reference interpreter ----

class Ref:
    def __init__(self):
        self.w = {}
        self.psp = PSP0
        self.sfp = SFP0
        self.mbp = 0
        self.midx = 0
        self.c = self.z = self.n = self.v = self.p = 0

    @property
    def flags(self):
        return self.c | self.z << 1 | self.n << 2 | self.v << 3 | self.p << 4

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
            return
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
                    REG_MIDX: self.midx, REG_SP: self.psp, REG_NULL: 0, REG_IP: next_ip}
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

    @property
    def addr(self):
        return 2 * len(self.words)

    def emit(self, ins, delta=0):
        self.words += ins
        self.ref.step(ins, self.addr)
        self.depth += delta
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

    # dd choices that keep 0 <= depth <= MAX_DEPTH. LD takes (dd, depth change).
    def ld_dds(self):
        d = self.depth
        out = []
        if d >= 1:
            out.append(0)
        if d >= 2:
            out.append(1)
        if d < MAX_DEPTH:
            out.append(2)
        if 1 <= d < MAX_DEPTH:
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
            regs = [REG_MBP, REG_MIDX, REG_SP, REG_NULL, REG_IP]
            if d >= 1:
                regs.append(REG_TOS)
            if d >= 2:
                regs.append(REG_NOS)
            return i_gen(op, dd, s, 0, r.choice(regs))
        if kind == 'imm16':
            return i_gen(op, dd, s, 4, 0, r.getrandbits(16))
        if kind == 'pfp':
            return i_pfp(op, dd, s, r.randrange(-min(d, 64), 64))
        if kind == 'ptr':
            return i_gen(op, dd, s, r.choice([1, 2, 3]), s and r.getrandbits(1))
        if kind == 'abs':
            return i_gen(op, dd, s, 5, 0, self.addr_far())
        if d < 1:
            return None
        return i_gen(op, dd, s, 6, r.randrange(min(d, 16)))

    def ld_one(self):
        r = self.rng
        dds = self.ld_dds()
        if not dds:
            return False
        dd = r.choice(dds)
        ins = self.src_ins(OP_LD, dd, r.getrandbits(1), self.depth)
        if ins is None:
            return False
        self.emit(ins, 1 if dd >= 2 else 0)
        return True

    def alu_one(self):
        r = self.rng
        d = self.depth
        dds = [0] if d >= 1 else []
        if d >= 2:
            dds += [1, 2, 3]
        if not dds:
            return False
        dd = r.choice(dds)
        op = r.randrange(OP_ADD, OP_BIC + 1)
        ins = self.src_ins(op, dd, r.getrandbits(1), d)
        if ins is None:
            return False
        self.emit(ins, -1 if dd >= 2 else 0)
        return True

    def st_one(self):
        r = self.rng
        dds = self.st_dds()
        if not dds:
            return False
        dd = r.choice(dds)
        d = self.depth - (1 if dd >= 2 else 0)     # depth when the destination is evaluated
        s = r.getrandbits(1)
        kinds = ['pfp', 'ptr', 'abs', 'null']
        if d >= 1:
            kinds += ['tos', 'sp']
        if d >= 2:
            kinds.append('nos')
        kind = r.choice(kinds)
        if kind == 'null':
            ins = i_gen(OP_ST, dd, s, 0, REG_NULL)
        elif kind == 'tos':
            ins = i_gen(OP_ST, dd, s, 0, REG_TOS)
        elif kind == 'nos':
            ins = i_gen(OP_ST, dd, s, 0, REG_NOS)
        elif kind == 'pfp':
            ins = i_pfp(OP_ST, dd, s, r.randrange(-min(d, 64), 64))
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
        if d < 3 or d > MAX_DEPTH - 3:
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

    def build(self, n):
        r = self.rng
        self.set_far()
        while self.depth < 4:
            self.push_imm8(r.getrandbits(8))
        for _ in range(n):
            x = r.random()
            if x < 0.05:
                self.block_set_sp()
            elif x < 0.17:
                self.block_alias()
            elif x < 0.40:
                while not self.ld_one():
                    pass
            elif x < 0.58:
                while not self.alu_one():
                    self.ld_one()
            else:
                while not self.st_one():
                    self.ld_one()
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
