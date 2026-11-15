# Programming Model and ISA

**Stackette** is a 16-bit, little-endian, byte-addressing, 16-bit instruction
words (some are followed by one 16-bit extra word), stack-oriented machine.
8-bit operations (`.b`, `.bs`) use the low byte and zero- or sign-extend the
result into the 16-bit destination.

## Programming model
As is often the case with modern stack-based architectures, **Stackette**
has two separate stacks: return stack, and parameter stack. Stacks are
word-sized and growing down, with pointer pointing to the word last pushed.

Instead of general purpose registers, **Stackette** uses a small set of
highly specialized registers, with accumulators doubling as a parameter stack
elements, namely: Top-of-Stack (TOS) and Next-of-Stack (NOS).

Currently, TOS and NOS implemented as a part of 4-word cache that follows the
stack pointer (PSP), so a lot of stack primitives don't do memory access and/or
really boils down to cache lines renaming.  

Return stack is used only for backing up IP and PSP between `CALL` and `RET`.

### Registers summary

| Mnemonic name | Purpose                                             | Value on reset  |
|---------------|-----------------------------------------------------|-----------------|
| IP            | instruction pointer                                 | 0               |
| RSP           | return stack pointer                                | 0               |
| PSP           | parameter stack pointer; contains words; grows down | 0xf000          |
| SFP           | frame pointer, set by CALL/RET                      | 0xf000          |
| TOS, NOS      | the top two parameter stack items (accumulators)    | _not available_ |
| MBP, MIDX     | memory base and index: `[mbp+midx]`                 | 0               |
| SR            | status: bit 0 C, 1 Z, 2 N, 3 V, 4 P (even parity)   | 0               |

### Memory Model

**Stackette**'s memory is byte- and word-addressed. Stacks are word-aligned,
but otherwise memory access is free-form. Unaligned word access results in two
consecutive byte accesses, similar to x86 (and unlike PDP-11 or MIPS, that
generate special exceptions).

### Memory Management Unit

MMU is tightly coupled with the other elements of **Stackette**'s architecture.

The machine has four address spaces, up to 64 KiB each:
- code (addressed by IP),
- return stack (RSP),
- parameter stack (PSP, SFP, and TOS),
- data (MBP, with or without MIDX, or by absolute addresses).
 
The MMU maps 4 KiB pages to 28-bit physical addresses, with separate maps for the 
system and the user context.

Default virtual memory map of this SoC: page 0 is flash, data page 15 is the
peripherals, the other pages are RAM.

The physical page layout is this:
- SDRAM is pages 0..0x1fff,
- flash is pages 0x2000..0x20ff,
- peripherals are pages 0x8000..0xffff.

Pages 0x2100..0x7fff have no memory, so a request to them is a fault. The CPU takes
it as fault number 1 (request refused), or number 2 for a stack-cache fill.

## Instruction formats

Instructions are split into four formats depending on bits 15:14 of the instruction
word.

### Dyadic instructions

Dyadic instructions takes 3/4 of instruction encoding space (00, 01, and 02).  Common
parts of the dyadic instructions are operation code (`oooo`) and destination (`dd`).
Depending on the destination (`dd`) format, they can be destructive or non-destructive,
i.e. operate directly on stack elements or push the result.

| Format | Bit layout             | Source                                                |
|--------|------------------------|-------------------------------------------------------|
| 00     | `00 oooo dd iiiiiiii`  | 8-bit immediate (zero-extended)                       |
| 01     | `01 oooo dd s fffffff` | stack frame operand `[sfp + 2*simm7]`                 |
| 10     | `10 oooo dd s vvvvvvv` | general addressing, variable-encoded (see below)      |
| 11     |                        | monadic, control, and various instruction (see below) |

Format 01 is used to address local variables (positive offsets) and function parameters
(negative offsets). In the absence of general purpose registers, it makes the most sense
to use this mode for returning values as well.

Available destinations, or stack operands (`dd`), are:

| dd          | LD and ALU instructions                            | ST instruction   |
|-------------|----------------------------------------------------|------------------|
| 00 tos      | TOS = src (op)                                     | src = TOS        |
| 01 nos      | NOS = src (op)                                     | src = NOS        |
| 10 push/pop | LD: push; ALU: NOS op= src, drop TOS               | src = pop        |
| 11 under    | LD: insert under TOS; ALU: TOS op= src, remove NOS | src = remove NOS |

Some dyadic instructions may treat destination differently.

- `ST` (store) instruction reverses the source and destination, so instead of popping
  the result off the stack it's pushing it on.
- `SWAP` writes to both source and destination, so it cannot push or pop the result.
  (`SWAP` with `dd`=10 or 11 are illegal instructions),
- `CMP` and `TST` do not change destination, but remove TOS (`dd`=10) or NOS (`dd`=11)
  similar to `ST`.

All dyadic operation codes (`oooo`) are encoded as follows:

| Code | Mnemonic | Meaning                                                                                         |
|------|----------|-------------------------------------------------------------------------------------------------|
| 0    | ADD      | Arithmetical addition                                                                           |
| 1    | ADC      | Addition with Carry bit                                                                         |
| 2    | SUB      | Subtraction                                                                                     |
| 3    | SBC      | Subtraction with ~Carry (Borrow)                                                                |
| 4    | AND      | Logical AND                                                                                     |
| 5    | OR       | Logical OR                                                                                      |
| 6    | XOR      | Logical XOR                                                                                     |
| 7    | BIC      | Bit Clear (X And Not Y)                                                                         |
| 8    | LD       | Load or push                                                                                    |
| 9    | ST       | Store or pop (target and source reversed)                                                       | 
| 10   | SWAP     | Atomically exchange target with source                                                          |
| 11   | LEA      | Calculates the absolute address of an operand in parameter stack. Format 01 and word-size only. |
| 12   | CMP      | Arithmetic compare (subtracts source from dest, sets flags and throws away the result)          |
| 13   | TST      | Bit test (similar to AND, but discards result)                                                  |

Bit `s` set signifies the byte form of the instruction. Mnemonic suffix `.b`
zero-extends the byte operand, and `.bs` sign-extends it. Only LD in
indexed modes provides the ability to choose between zero and sign. Other
instructions extends byte operands with zero.

### Format 10 variable-encoded fields (`vvvvvvv`)

This is the most complex part of dyadic instruction variants in both function
and encoding.

| Bit layout             | Addressing mode                                                                                                                                     |
|------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| `000 rrrr`             | register, encoded in `r`                                                                                                                            |
| `0010 mm e`            | `mm`=0: index `[MBP + MIDX]`<br/>`mm`=1: post-increment `[MBP + MIDX++]`<br/>`mm`=2: pre-decrement `[MBP + --MIDX]`<br/>`mm`=3: TOS-related `[TOS]` |
| `0011 xxx`             | reserved                                                                                                                                            |
| `010000 e`             | absolute `[abs16]`, address word follows                                                                                                            |
| `0100010`              | immediate `#imm16`, source word follows (illegal with ST)                                                                                           |
| `0100011` to `0111111` | reserved                                                                                                                                            | 
| `1 kkkkk e`            | PSP-relative `[PSP+2k]`, `kkkkk` is unsigned.<br/>`[PSP+0]` is TOS, and `[PSP+1]` is NOS                                                                |

Byte operand extension for LD: `e`=0 operand is zero-extended (`ld.b`), `e`=1 operand
is sign-extended (`ld.bs`). May not be supported for PSP-relative addressing with operand
in stack cache (`2k` < 4).

### Register operand codes (`rrrr`)

| Encoded | Mnemonic name | Additional info                        |
|---------|---------------|----------------------------------------|
| 0000    | NULL          | reads as 0, written value is discarded |
| 0001    | PSP           |                                        |
| 0010    | MBP           |                                        |
| 0011    | MIDX          |                                        |
| 0100    |               | reserved                               |
| 0101    |               | reserved                               |
| 0110    | SR            |                                        |
| 0111    | IP            |                                        |
| 1xxx    |               | reserved                               |

### Monadic, Control, and Various Instructions (format 11)

| Instruction                                     | Encoding                  | Effect                                                                                  |
|-------------------------------------------------|---------------------------|-----------------------------------------------------------------------------------------|
| `BRA l`                                         | `11 00 0 lllllllllll`     | branch (with 11-bit signed offset)                                                      |
| `SKIP cc`                                       | `11 00 10 cccc 000000`    | skip the next instruction (any length) if condition (`cc`) holds                        |
| `SETB #m`                                       | `11 01 0000 iiiiiiii`     | set SR bits                                                                             |
| `CLRB #m`                                       | `11 01 1000 iiiiiiii`     | clear SR bits                                                                           |
| `ADDP MBP\|MIDX\|PSP, #i`                       | `11 01 01 rr iiiiiiii`    | register (`rr`: 0 MBP, 1 MIDX, 2 PSP) += signed byte                                    |
| `NOT\|NEG\|SXT\|BSWAP dd`                       | `11 10 00 dd s oooo 000`  | monadic instructions (see below)                                                        |
| `MULU\|MULS\|DIVU\|DIVS` (`.b`)                 | `11 10 01 d g s 0000000`  | signed/unsigned (`g`) division/subtraction (`d`) over words/bytes (`s`) on stack        |
| `SHL`\|`SHR`\|`SAR`\|`ROL`\|`ROR`\|`RCL`\|`RCR` | `11 10 1 v dd s kkk cccc` | variable shifts and rotates (see below)                                                 |
| `CALL l`                                        | `11 11 0 lllllllllll`     | call (with 11-bit word offset)                                                          |
| `NOP`                                           | `11 11 100000 000000`     | no-op                                                                                   |
| `RET #n`                                        | `11 11 100001 iiiiii`     | return and subtract #n words from PSP                                                   |
| `RETI`                                          | `11 11 100010 000000`     | enter (return to) user mode                                                             |
| `TRAP #p`                                       | `11 11 100100 iiiiii`     | system call                                                                             |
| `CALLA abs`                                     | `11 11 100101 000000`     | call (address in next word)                                                             |
| `JMP abs`                                       | `11 11 100110 000000`     | jump (address in next word)                                                             |
| `CALL POP`                                      | `11 11 100111 000000`     | call (pop address from stack)                                                           |
| `RET [SFP]`                                     | `11 11 101001 000000`     | return and subtract the number of words, supplied by caller in last parameter, from PSP |

Monadic instructions are performed on stack destination (`dd`). `SXT`
and `BSWAP` are word-only, other instructions have byte- and word-size
variants (bit `s`).

| Opcode (`oooo`) | Mnemonic   | Effect                                         |
|-----------------|------------|------------------------------------------------|
| 0000            | `NOT dd`   | logical inversion of `dd`                      |
| 0001            | `NEG dd`   | sign change in two's complement representation |
| 0010            | `SXT dd`   | extend sign of lower byte to high byte         |
| 0011            | `BSWAP dd` | swap low and high bytes                        |
| 0100..1111      |            | reserved                                       |

**MUL/DIV** operates on TOS and NOS with stack depth unchanged. Location of
operands and result: 

| MUL | Before       | After               |
|-----|--------------|---------------------|
| TOS | multiplicand | product (low word)  |
| NOS | multiplicand | product (high word) |

| DIV | Before   | After                            |
|-----|----------|----------------------------------|
| TOS | divisor  | quotient                         |
| NOS | dividend | remainder (sign of the dividend) |
 
Division by zero is fault 4, NOS and TOS remain unchanged.

Variable **Shifts and rolls** have two forms, defined by `v` bit:

- `v`==0: with 4-bit immediate counter in lower 4 bits of the instruction
  (`c` field),
- `v`==1: with counter in TOS.

Counter value 0 is interpreted as 16, similar to 32-bit ARM.
Shifts by 16 or more positions (8 or more if `s`==1) are saturated.

The list of all kinds of shift/roll (`k` field).

| Kind | Mnemonic | Meaning                       |
|------|----------|-------------------------------|
| 000  | `SHL`    | Left shift                    |
| 001  | `SHR`    | Right logical shift           |
| 010  | `SAR`    | Right arithmetic shift        |
| 011  | reserved |                               |
| 100  | `ROL`    | Left roll                     |
| 101  | `ROR`    | Right roll                    |
| 110  | `RCL`    | Left roll through carry flag  |
| 111  | `RCR`    | Right roll through carry flag |

Everything else is an illegal instruction.

## Flags and conditions

LD, ST, SWAP do not change flags. ALU ops set Z, N, P, and C, V for arithmetic.
MUL sets C and V when the high part is significant; DIV sets V on signed
overflow and keeps C. After `SUB`/`SBC`, C means no borrow, unlike PDP-11 or x86,
but similar to MOS 6502, ARM, or PowerPC.

| `cc` | Mnemonic name | Holds when...   |
|------|---------------|-----------------|
| 0    | EQ, Z         | Z               |
| 1    | NE, NZ        | not Z           |
| 2    | CS, C, HS     | C               |
| 3    | CC, NC, LO    | not C           |
| 4    | MI            | N               |
| 5    | PL            | not N           |
| 6    | VS            | V               |
| 7    | VC            | not V           |
| 8    | HI            | C and not Z     |
| 9    | LS            | not c or Z      |
| 10   | GE            | N = V           |
| 11   | LT            | N ≠ V           |
| 12   | GT            | not Z and N = V |
| 13   | LE            | Z or N ≠ V      |
| 14   | PE            | P               |
| 15   | PO            | not P           |

`SKIP cc` is the only conditional instruction.

## Calls and Returns

There are multiple instructions in `CALL` and `RET` groups.

| Mnemonic    | Effect                                                                      |
|-------------|-----------------------------------------------------------------------------|
| `CALL l`    | Computes function address as `IP ± l`, where `l` is a signed 11-bit offset. |
| `CALLA abs` | Function address is in a next instruction word.                             |
| `CALL POP`  | Function address is popped off the parameter stack.                         |
|             |                                                                             |
| `RET #n`    | Cleans up #n words from stack, then returns.                                |
| `RET [SPF]` | Treats the last function parameter as a word count for cleanup.             |

**Stackette** has two different stacks: return stack, and parameter stack.
All instructions of the `CALL` group push IP and PFP registers on the return
stack, while `RET`s pop them from there.

The other thing `CALL`s and `RET`s have to take care of is a stack frame.
`CALL` instructions copies `PSP` to `PFP`, assuming the caller had pushed all
the function parameters. `RET` instructions do the frame cleanup. 

There are two categories of calling conventions, depending on caller or callee
doing stack cleanup. When stack is cleaned up by the caller, each call is
followed by the same stack correction code, which is a waste of program space.
When stack is cleaned up by the callee, it is unknown at the time how many
parameters were passed by the caller, which is complicates variadic function
support.

**Stackette** ISA is aimed at the sweet spot:
- `call` loads PFP from PSP, arguments are above it (PFP wit positive offsets),
  return value(s) and local variables are below it (negative offsets). Variadic
  functions must push an argument count word last;
- `ret #n` drops n argument words;
- `ret [pfp]` takes the n count from `pfp` (a count word, counting itself, pushed
  last), for variable argument lists.

## Contexts and exceptions

System and user contexts have their own IP, RSP, SFP, MBP, MIDX, SR, PSP and
MMU map; the stack cache is flushed on a switch. Power-up and soft reset start
the system context at address 0. `RETI` (system only) runs the user context.
`TRAP p` continues the system context execution after `RETI`.

The system context runs on reset, when the user context traps, faults, or is
interrupted. The cause is in the ID register: class in bits 7:6, number in 5:0.

| Class | Meaning   | Number                                                                                                      |
|-------|-----------|-------------------------------------------------------------------------------------------------------------|
| 0     | reset     | 0                                                                                                           |
| 1     | interrupt | line (0 timer, 1 UART RX, 2..7 external)                                                                    |
| 2     | `trap #p` | p (0..63)                                                                                                   |
| 3     | fault     | 0 illegal instruction, 1 memory refused, 2 stack fill refused, 3 stack write-back refused, 4 divide by zero |

Interrupts are taken in the user context between instructions. A fault in the
system context is a soft reset. Faulting instructions are restartable (nothing
changes), except the write-back fault, which is asynchronous.

## Peripherals (byte addresses in the data space, default map)

| Address        | Size, words | Device                                                                                                              |
|----------------|-------------|---------------------------------------------------------------------------------------------------------------------|
| 0xf000..0xf007 | 4           | UART                                                                                                                |
| 0xf008..0xf00f | 4           | interrupt controller (RAW, MASK, ACTIVE)                                                                            |
| 0xf010..0xf01f | 8           | timer (COUNT, COMPARE, CONTROL, STATUS, PRESCALE)                                                                   |
| 0xf020..0xf03f | 16          | context device: IP, RSP, SFP, MBP, MIDX, SW, PSP of the inactive context, then ID, FAULT_IP, FAULT_ADDR (read only) |
| 0xf040..0xf047 | 4           | SPI master (network controller)                                                                                     |
| 0xf080..0xf0bf | 32          | IDE host                                                                                                            |
| 0xf100..0xf1ff | 128         | MMU registers: 64 system, then 64 user (`space*16 + page`)                                                          |
