; CMP and TST: SUB and AND that only set the flags. dd names the item that is
; compared: tos and nos stay, pop removes TOS, under removes NOS. Expect
; ABCDEFGH, then silence. A check that fails stops the program with the letter
; missing. Each step leaves the stack as it found it.

start:

; A: cmp tos: 5 - 7 borrows (cc), is negative (mi) and not zero; 5 stays
        ld push, #5
        cmp tos, #7
        skip cc
        bra fail
        skip mi
        bra fail
        skip ne
        bra fail
        sub tos, #5
        st null, pop
        skip eq
        bra fail
        ld push, #65            ; 'A'
        st [0xf000], pop

; B: cmp pop: 5 - 5 is zero with no borrow (cs); the 5 is gone, 9 is TOS
        ld push, #9
        ld push, #5
        cmp pop, #5
        skip eq
        bra fail
        skip cs
        bra fail
        sub tos, #9
        st null, pop
        skip eq
        bra fail
        ld push, #66            ; 'B'
        st [0xf000], pop

; C: cmp nos: NOS 1 - 3 borrows and is negative; both items stay
        ld push, #1
        ld push, #8
        cmp nos, #3
        skip cc
        bra fail
        skip mi
        bra fail
        sub tos, #8
        st null, pop
        skip eq
        bra fail
        sub tos, #1
        st null, pop
        skip eq
        bra fail
        ld push, #67            ; 'C'
        st [0xf000], pop

; D: cmp under: NOS 6 - 6 is zero, NOS is removed, 100 stays as TOS
        ld push, #6
        ld push, #100
        cmp under, #6
        skip eq
        bra fail
        sub tos, #100
        st null, pop
        skip eq
        bra fail
        ld push, #68            ; 'D'
        st [0xf000], pop

; E: tst tos: 0x0c & 3 is zero; C and V are not changed (both set before)
        setb #9
        ld push, #0x0c
        tst tos, #3
        skip eq
        bra fail
        skip cs
        bra fail
        skip vs
        bra fail
        clrb #9
        sub tos, #0x0c
        st null, pop
        skip eq
        bra fail
        ld push, #69            ; 'E'
        st [0xf000], pop

; F: tst pop: 0x8000 & 0x8000 is negative and not zero; the item is removed
        ld push, #55
        ld push, #0x8000
        tst pop, #0x8000
        skip mi
        bra fail
        skip ne
        bra fail
        sub tos, #55
        st null, pop
        skip eq
        bra fail
        ld push, #70            ; 'F'
        st [0xf000], pop

; G: the byte form compares the low bytes: 0x1234 and 0x0034
        ld push, #0x1234
        cmp.b pop, #0x34
        skip eq
        bra fail
        ld push, #71            ; 'G'
        st [0xf000], pop

; H: memory operands: [abs], and [tos] where TOS is the pointer itself
        ld push, #0x2000
        st [0x2000], pop
        ld push, #0x2000
        cmp tos, [0x2000]
        skip eq
        bra fail
        cmp pop, [tos]
        skip eq
        bra fail
        ld push, #72            ; 'H'
        st [0xf000], pop

done:   bra done
fail:   bra fail
