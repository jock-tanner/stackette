#!/usr/bin/env python3
"""
Tiny assembler for the LD and ST instructions of the stack CPU (cpu.py).

    python asm.py hello.asm -o hello.bin -l hello.lst

Output is raw little-endian 16-bit words, starting at address 0, ready to be
programmed into the serial flash at offset 0.

Syntax, one instruction per line; `;` starts a comment, `name:` defines a label:

    ld DD, SRC          DD: tos, nos, push, under
    st DST, DD          DD: tos, nos, pop, under
    add adc sub sbc and or xor bic  DD, SRC
                        DD: tos, nos, push, under; takes SRC like ld
    ld.b / st.b         8-bit access (ld.bs: byte load, sign-extended, only
                        from [mbp+midx], [mbp+midx++] and [mbp+--midx])
    lea DD, [pfp+n]     DD: tos, nos, push, under; the address SFP+n (n even, -128..126)
                        goes on the stack like a loaded value. The flags are not
                        changed.
    ld, st, the ALU ops and swap also take [tos] as the memory operand: the
                        address is TOS, taken before anything else happens, so
                        `ld tos, [tos]` replaces a pointer with what it points
                        to, `ld push, [tos]` keeps it, `add nos, [tos]` uses it
                        up (dd as usual). st [tos] takes its value from NOS: `st
                        [tos], nos` or `st [tos], under`; the pointer stays.
    swap DD, MEM        DD: tos, nos; MEM: [pfp+n], [mbp+midx] (with + or -
                        for the post-increment and pre-decrement forms),
                        [abs], [sp+k] with k >= 4. TOS or NOS and the memory
                        word exchange their values, and nothing else uses the
                        memory between the read and the write (a lock, for a
                        semaphore). swap.b exchanges a byte, zero-extended
                        into the stack item. The flags are not changed.

    .word EXPR, ...     raw 16-bit words

Control flow:
    bra LABEL           branch, a 11-bit word offset from the next instruction
    call LABEL          call, the same offset; call pop: target taken off the stack
    jmp EXPR            jump to an absolute address (two words)
    calla EXPR          call an absolute address (two words)
    ret #n              return, dropping n argument words (0..63) above the saved SFP
    ret                 return, taking the count from the frame ([pfp+2], it counts itself)
    addp REG, #i        REG (mbp, midx or sp) += i, a signed byte count -128..127
    skip CC             skip the next instruction (of any length) if CC holds
    not neg sxt bswap rlc rrc  DD
                        unary operation (.b for 8 bits, not for sxt and bswap). The
                        operand is TOS for tos and under, NOS for nos and push; tos
                        and nos replace it, push puts the result on top of the stack
                        and under inserts it below TOS, the operand stays.
                        neg and rlc set C Z N V P, rrc C Z N P, the others Z N P.
                        rlc and rrc rotate through the carry.
    mulu muls divu divs  (.b for 8 bits; no operands)
                        NOS and TOS are replaced by two results: TOS gets the
                        primary one, NOS the secondary one. mul: TOS * NOS, low part
                        in TOS and high part in NOS. div: NOS / TOS, quotient in TOS
                        and remainder in NOS (truncated toward zero, the remainder
                        has the sign of the dividend). Byte forms use the low bytes
                        and give zero-extended bytes. Z N P are those of the primary
                        result. mul sets C and V when the high part is significant
                        (it is not 0, or for muls not the sign extension of the low
                        part); div sets V when the quotient overflows (the most
                        negative number / -1, the quotient wraps) and keeps C.
                        A division by zero is fault 4 and nothing changes.
    setb #m / clrb #m   set / clear the status register bits in the mask m
    trap #p             a system call: the system context runs, p (0..63) is in its
                        interrupt ID (the number of class 2). One word, so a trap
                        can also be a breakpoint.
    nop                 do nothing (0xf800)
    reti                (system mode only) switch to the user context; the system
                        context continues after it the next time it runs. In user
                        mode it is an illegal instruction.
Status register `sr` (a register operand like any other): bit 0 C, 1 Z, 2 N, 3 V,
4 P. The other bits are not implemented: they read as zero and ignore writes.
Conditions: eq ne | cs cc (unsigned >= <) | mi pl | vs vc | hi ls (unsigned > <=) |
ge lt gt le (signed) | pe po (parity even, odd). After sub/sbc, C means no borrow.
Aliases: z nz, hs lo, c nc. LD and ST do not change the flags.
A call pushes the return address on the return stack and the caller's SFP on the
parameter stack; SFP then points at it. The arguments pushed before the call are
above it, for `ret` with the count word as the last one pushed.

Stack part DD (see the encoding table):
    ld tos, X   TOS <- X          st X, tos   X <- TOS
    ld nos, X   NOS <- X          st X, nos   X <- NOS
    ld push, X  push X            st X, pop   X <- pop
    ld under, X insert X under TOS   st X, under   X <- remove NOS

ALU instructions: the operand named by DD (tos nos) is combined with SRC.
    add tos, X  TOS <- TOS + X        add nos, X  NOS <- NOS + X
    add push, X NOS <- NOS + X, drop TOS
    add under, X TOS <- TOS + X, remove NOS
The same for adc sub sbc and or xor bic. An imm8 is sign-extended for
add adc sub sbc and zero-extended for and or xor bic.

Operands (st takes no immediate):
    #expr               immediate: imm8 if it is a number in -128..127, else
                        imm16 (a label is always imm16); ld only
    tos nos mbp midx sp null sr ip  register (sr: the status register, see below)
    [mbp+midx]  [mbp+midx++]  [mbp+--midx]      pointer, post-increment, pre-decrement
    [expr]              absolute 16-bit byte address
    [sp+k]              stack item k (0 = TOS), k = 0..15
    [pfp+n]             frame-relative, n = even byte offset -128..126

expr: a number (decimal, 0x hex, 'c' character) or a label.
"""
import argparse
import re
import sys

REGS = {'tos': 0, 'nos': 1, 'mbp': 2, 'midx': 3, 'sp': 4, 'null': 5, 'sr': 6, 'ip': 15}
LD_DD = {'tos': 0, 'nos': 1, 'push': 2, 'under': 3}
ST_DD = {'tos': 0, 'nos': 1, 'pop': 2, 'under': 3}
ALU_OPS = ['add', 'adc', 'sub', 'sbc', 'and', 'or', 'xor', 'bic']
OP_LEA = 11
OP = {'ld': 8, 'st': 9, 'swap': 10}
OP.update({name: i for i, name in enumerate(ALU_OPS)})


class AsmError(Exception):
    pass


def strip_comment(line):
    quoted = False
    for i, c in enumerate(line):
        if c == "'":
            quoted = not quoted
        elif c == ';' and not quoted:
            return line[:i]
    return line


def split_operands(text):
    """Split on commas outside brackets and character literals."""
    out, depth, quoted, cur = [], 0, False, ''
    for c in text:
        if c == "'":
            quoted = not quoted
        if not quoted:
            if c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
            elif c == ',' and depth == 0:
                out.append(cur.strip())
                cur = ''
                continue
        cur += c
    if cur.strip():
        out.append(cur.strip())
    return out


def is_number(text):
    text = text.strip()
    return bool(re.fullmatch(r"[+-]?(0x[0-9a-fA-F]+|\d+)|'.'", text))


def number(text):
    text = text.strip()
    if text.startswith("'"):
        return ord(text[1])
    return int(text, 0)


def value(text, labels):
    if is_number(text):
        return number(text)
    if text in labels:
        return labels[text]
    raise AsmError(f'unknown symbol {text!r}')


def parse_operand(text):
    t = text.strip()
    if t.startswith('#'):
        return ('imm', t[1:].strip())
    if t.lower() in REGS:
        return ('reg', REGS[t.lower()])
    if t.startswith('[') and t.endswith(']'):
        inner = t[1:-1].replace(' ', '')
        low = inner.lower()
        if low == 'mbp+midx':
            return ('ptr', 1)
        if low == 'mbp+midx++':
            return ('ptr', 2)
        if low == 'mbp+--midx':
            return ('ptr', 3)
        if low == 'tos':
            return ('tos', None)
        if low.startswith('sp+'):
            return ('sp', inner[3:])
        if low.startswith('pfp'):
            return ('pfp', inner[3:] or '0')
        return ('abs', inner)
    raise AsmError(f'cannot parse operand {text!r}')


def w_imm8(op, dd, imm):
    return [(op << 10) | (dd << 8) | (imm & 0xff)]


def w_pfp(op, dd, s, f):
    return [(1 << 14) | (op << 10) | (dd << 8) | (s << 7) | (f & 0x7f)]


def w_gen(op, dd, s, mode, x, extra=None):
    w = (2 << 14) | (op << 10) | (dd << 8) | (s << 7) | (mode << 4) | x
    return [w] if extra is None else [w, extra & 0xffff]


UNARY = {'not': 0, 'neg': 1, 'sxt': 2, 'bswap': 3, 'rlc': 4, 'rrc': 5}
# multiplication and division take no operands: NOS and TOS are replaced by
# the primary result (TOS) and the secondary one (NOS)
MULDIV = {'mulu': 0xe400, 'muls': 0xe500, 'divu': 0xe600, 'divs': 0xe700}
CTL = ('bra', 'call', 'calla', 'jmp', 'ret', 'addp', 'skip', 'setb', 'clrb', 'reti', 'trap', 'nop')
CONDITIONS = {
    'eq': 0, 'z': 0, 'ne': 1, 'nz': 1,
    'cs': 2, 'hs': 2, 'c': 2, 'cc': 3, 'lo': 3, 'nc': 3,
    'mi': 4, 'pl': 5, 'vs': 6, 'vc': 7,
    'hi': 8, 'ls': 9, 'ge': 10, 'lt': 11, 'gt': 12, 'le': 13,
    'pe': 14, 'po': 15,
}
MISC = 0xf800
MISC_NOP, MISC_RET_N, MISC_RETI, MISC_TRAP, MISC_CALL_ABS, MISC_JMP_ABS, MISC_CALL_POP, MISC_RET_VAR = 0, 1, 2, 4, 5, 6, 7, 9


def encode_ctl(name, operands, labels, pc):
    lab = labels if labels is not None else {}

    def val(text):
        if labels is None and not is_number(text):
            return 0
        return value(text, lab)

    if name in ('bra', 'call', 'calla', 'jmp'):
        if len(operands) != 1:
            raise AsmError(f'{name} takes one operand')
        if name == 'call' and operands[0].lower() == 'pop':
            return [MISC | MISC_CALL_POP << 6]
        target = val(operands[0])
        if name in ('calla', 'jmp'):
            op = MISC_CALL_ABS if name == 'calla' else MISC_JMP_ABS
            return [MISC | op << 6, target & 0xffff]
        off = target - (pc + 2)
        if labels is None:
            return [0]
        if off % 2 or not -2048 <= off <= 2046:
            raise AsmError(f'{name}: target {target:#06x} is not reachable (use {"calla" if name == "call" else "jmp"})')
        return [(0xc000 if name == 'bra' else 0xf000) | (off // 2 & 0x7ff)]
    if name in ('setb', 'clrb'):
        if len(operands) != 1 or not operands[0].startswith('#'):
            raise AsmError(f'{name} takes #mask')
        i = val(operands[0][1:])
        if not 0 <= i <= 255:
            raise AsmError(f'{name}: the mask must be 0..255')
        return [0xd000 | (name == 'clrb') << 8 | i]
    if name == 'skip':
        if len(operands) != 1 or operands[0].lower() not in CONDITIONS:
            raise AsmError('skip takes a condition: ' + ' '.join(CONDITIONS))
        return [0xc800 | CONDITIONS[operands[0].lower()] << 6]
    if name == 'reti':
        if operands:
            raise AsmError('reti takes no operand')
        return [MISC | MISC_RETI << 6]
    if name == 'trap':
        if len(operands) != 1 or not operands[0].startswith('#'):
            raise AsmError('trap takes #p')
        p = val(operands[0][1:])
        if not 0 <= p <= 63:
            raise AsmError('trap #p: p must be 0..63')
        return [MISC | MISC_TRAP << 6 | p]
    if name == 'nop':
        if operands:
            raise AsmError('nop takes no operands')
        return [MISC | MISC_NOP << 6]
    if name == 'ret':
        if not operands:
            return [MISC | MISC_RET_VAR << 6]
        if len(operands) != 1 or not operands[0].startswith('#'):
            raise AsmError('ret takes no operand or #n')
        n = val(operands[0][1:])
        if not 0 <= n <= 63:
            raise AsmError('ret #n: n must be 0..63')
        return [MISC | MISC_RET_N << 6 | n]
    if len(operands) != 2 or not operands[1].startswith('#'):
        raise AsmError('addp takes a register and #i')
    rr = {'mbp': 0, 'midx': 1, 'sp': 2}.get(operands[0].lower())
    if rr is None:
        raise AsmError('addp: the register is mbp, midx or sp')
    i = val(operands[1][1:])
    if not -128 <= i <= 127:
        raise AsmError('addp: i must be -128..127')
    return [0xd400 | rr << 8 | (i & 0xff)]


def encode(mnemonic, operands, labels, pc=0):
    """-> list of words. With labels == None only the length is meaningful."""
    if mnemonic.lower() == '.word':
        if not operands:
            raise AsmError('.word takes values')
        return [0 if labels is None and not is_number(x) else value(x, labels or {}) & 0xffff
                for x in operands]
    name, _, suffix = mnemonic.lower().partition('.')
    if name in CTL:
        if suffix:
            raise AsmError(f'{name} has no size suffix')
        return encode_ctl(name, operands, labels, pc)
    if name in MULDIV:
        if suffix not in ('', 'b'):
            raise AsmError(f'unknown size suffix .{suffix}')
        if operands:
            raise AsmError(f'{name} takes no operands')
        return [MULDIV[name] | (suffix == 'b') << 7]
    if name in UNARY:
        if suffix not in ('', 'b'):
            raise AsmError(f'unknown size suffix .{suffix}')
        dds = {'tos': 0, 'nos': 1, 'push': 2, 'under': 3}
        if len(operands) != 1 or operands[0].lower() not in dds:
            raise AsmError(f'{name} takes tos, nos, push or under')
        if suffix and name in ('sxt', 'bswap'):
            raise AsmError(f'{name} is a word operation')
        dd = dds[operands[0].lower()]
        return [0xe000 | dd << 8 | (suffix == 'b') << 7 | UNARY[name] << 3]
    if name == 'lea':
        if suffix:
            raise AsmError('lea has no size suffix')
        if len(operands) != 2:
            raise AsmError('lea takes two operands')
        dd_name, (kind, arg) = operands[0].lower(), parse_operand(operands[1])
        if dd_name not in LD_DD:
            raise AsmError(f'{dd_name!r} is not a stack operand of lea ({", ".join(LD_DD)})')
        if kind != 'pfp':
            raise AsmError('lea takes a [pfp+n] operand')
        n = 0 if labels is None and not is_number(arg) else value(arg, labels or {})
        if n % 2 or not -128 <= n <= 126:
            raise AsmError('[pfp+n]: n must be an even byte offset -128..126')
        return w_pfp(OP_LEA, LD_DD[dd_name], 0, n // 2)
    if name not in OP:
        raise AsmError(f'unknown instruction {mnemonic!r}')
    if suffix not in ('', 'b', 'bs'):
        raise AsmError(f'unknown size suffix .{suffix}')
    op = OP[name]
    s = 1 if suffix else 0
    sx = 1 if suffix == 'bs' else 0
    if len(operands) != 2:
        raise AsmError(f'{name} takes two operands')
    if name in ALU_OPS and suffix == 'bs':
        raise AsmError('ALU instructions have no .bs form, use .b')
    if name == 'swap' and suffix == 'bs':
        raise AsmError('swap has no .bs form, use .b')
    lab = labels if labels is not None else {}

    def val(text):
        if labels is None and not is_number(text):
            return 0
        return value(text, lab)

    if name != 'st':
        dd_name, operand = operands[0].lower(), parse_operand(operands[1])
        dds = LD_DD
    else:
        dd_name, operand = operands[1].lower(), parse_operand(operands[0])
        dds = ST_DD
    if dd_name not in dds:
        raise AsmError(f'{dd_name!r} is not a stack operand of {name} ({", ".join(dds)})')
    dd = dds[dd_name]
    kind, arg = operand
    if suffix == 'bs' and (name != 'ld' or kind != 'ptr'):
        raise AsmError(
            'the .bs form exists only for ld with [mbp+midx], '
            '[mbp+midx++] and [mbp+--midx]'
        )
    if name == 'swap':
        if dd > 1:
            raise AsmError('swap takes tos or nos')
        if kind in ('imm', 'reg') or (kind == 'sp' and val(arg) < 4):
            raise AsmError('swap needs a memory operand')
    if kind == 'imm':
        if name == 'st':
            raise AsmError('st has no immediate form')
        v = val(arg)
        lo = 0 if name in ALU_OPS[4:] else -128
        hi = 255 if name in ALU_OPS[4:] else 127
        if is_number(arg) and not s and lo <= v <= hi:
            return w_imm8(op, dd, v)
        return w_gen(op, dd, s, 4, 0, v)
    if kind == 'reg':
        return w_gen(op, dd, s, 0, arg)
    if kind == 'ptr':
        return w_gen(op, dd, s, arg, sx if name == 'ld' else 0)
    if kind == 'tos':
        if name == 'st' and dd not in (1, 3):
            raise AsmError('st [tos] takes its value from nos or under')
        return w_gen(op, dd, s, 7, 0)
    if kind == 'abs':
        return w_gen(op, dd, s, 5, 0, val(arg))
    if kind == 'sp':
        k = val(arg)
        if not 0 <= k <= 15:
            raise AsmError('[sp+k]: k must be 0..15')
        return w_gen(op, dd, s, 6, k)
    if kind == 'pfp':
        n = val(arg)
        if n % 2 or not -128 <= n <= 126:
            raise AsmError('[pfp+n]: n must be an even byte offset -128..126')
        return w_pfp(op, dd, s, n // 2)
    raise AsmError('bad operand')


def assemble(source):
    """-> (words, listing lines)"""
    items = []      # (line number, label list, mnemonic, operands, text)
    pending = []
    for n, raw in enumerate(source.splitlines(), 1):
        line = strip_comment(raw).strip()
        while True:
            m = re.match(r'([A-Za-z_]\w*)\s*:\s*(.*)', line)
            if not m:
                break
            pending.append(m.group(1))
            line = m.group(2)
        if not line:
            continue
        mnemonic, _, rest = line.partition(' ')
        items.append((n, pending, mnemonic, split_operands(rest), line))
        pending = []
    if pending:
        items.append((0, pending, None, [], ''))

    labels, pc = {}, 0
    for n, labs, mnemonic, operands, text in items:
        for name in labs:
            if name in labels:
                raise AsmError(f'line {n}: label {name!r} defined twice')
            labels[name] = pc
        if mnemonic:
            try:
                pc += 2 * len(encode(mnemonic, operands, None, pc))
            except AsmError as e:
                raise AsmError(f'line {n}: {e}') from None

    words, listing, pc = [], [], 0
    for n, labs, mnemonic, operands, text in items:
        if not mnemonic:
            continue
        try:
            enc = encode(mnemonic, operands, labels, pc)
        except AsmError as e:
            raise AsmError(f'line {n}: {e}') from None
        listing.append(f'{pc:04x}  {" ".join(f"{w:04x}" for w in enc):<10}  {text}')
        words += enc
        pc += 2 * len(enc)
    return words, listing


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('source')
    ap.add_argument('-o', '--output', required=True)
    ap.add_argument('-l', '--listing')
    args = ap.parse_args()
    try:
        words, listing = assemble(open(args.source).read())
    except AsmError as e:
        sys.exit(f'{args.source}: {e}')
    with open(args.output, 'wb') as f:
        for w in words:
            f.write(w.to_bytes(2, 'little'))
    if args.listing:
        with open(args.listing, 'w') as f:
            f.write('\n'.join(listing) + '\n')
    print(f'{len(words)} words, {2 * len(words)} bytes -> {args.output}')


if __name__ == '__main__':
    main()
