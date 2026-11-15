; hw32: the shift and rotate instructions SHL, SHR, SAR, ROL, ROR, RCL, RCR (count
; in the instruction or in TOS, word or byte, the stack destinations), with the
; barrel shifter of shifter.py. Expect ABCDEFGHIJKLMNOPQR, then silence. A check that fails stops the program with the letter missing.
; Each step leaves the stack as it found it.

start:

; A: shl by 4 in place: bit 12 goes to C
        ld push, #0x1234
        shl tos, #4
        skip cs
        bra fail
        skip vc
        bra fail
        skip pl
        bra fail
        skip ne
        bra fail
        sub tos, #0x2340
        st null, pop
        skip eq
        bra fail
        ld push, #65            ; 'A'
        st [0xf000], pop

; B: shr by 1: the low bit goes to C, a 0 comes in
        ld push, #0x8001
        shr tos, #1
        skip cs
        bra fail
        skip vc
        bra fail
        skip pl
        bra fail
        skip ne
        bra fail
        sub tos, #0x4000
        st null, pop
        skip eq
        bra fail
        ld push, #66            ; 'B'
        st [0xf000], pop

; C: sar by 3: the sign comes in, N
        ld push, #0x8001
        sar tos, #3
        skip cc
        bra fail
        skip vc
        bra fail
        skip mi
        bra fail
        skip ne
        bra fail
        sub tos, #0xf000
        st null, pop
        skip eq
        bra fail
        ld push, #67            ; 'C'
        st [0xf000], pop

; D: shl of NOS by 8, TOS stays
        ld push, #0x00ff
        ld push, #7
        shl nos, #8
        skip cc
        bra fail
        skip vc
        bra fail
        skip mi
        bra fail
        skip ne
        bra fail
        sub tos, #7             ; TOS is untouched
        st null, pop
        skip eq
        bra fail
        sub tos, #0xff00
        st null, pop
        skip eq
        bra fail
        ld push, #68            ; 'D'
        st [0xf000], pop

; E: shl by a count in TOS
        ld push, #3
        ld push, #5
        shl push, tos           ; the count in TOS, dropped
        skip cc
        bra fail
        skip vc
        bra fail
        skip pl
        bra fail
        skip ne
        bra fail
        sub tos, #0x60
        st null, pop
        skip eq
        bra fail
        ld push, #69            ; 'E'
        st [0xf000], pop

; F: shl by 16 saturates to 0
        ld push, #0xffff
        ld push, #16
        shl push, tos           ; by the width: 0, C is bit 0
        skip cs
        bra fail
        skip vc
        bra fail
        skip pl
        bra fail
        skip eq
        bra fail
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, #70            ; 'F'
        st [0xf000], pop

; G: sar by 17 gives the sign, C is the sign
        ld push, #0x8000
        ld push, #17
        sar push, tos           ; by more than the width: the sign
        skip cs
        bra fail
        skip vc
        bra fail
        skip mi
        bra fail
        skip ne
        bra fail
        sub tos, #0xffff
        st null, pop
        skip eq
        bra fail
        ld push, #71            ; 'G'
        st [0xf000], pop

; H: shl.b by 1: N is bit 7
        ld push, #0x01ff
        shl.b tos, #1           ; the upper byte is ignored, the result zero-extended
        skip cs
        bra fail
        skip vc
        bra fail
        skip mi
        bra fail
        skip ne
        bra fail
        sub tos, #0xfe
        st null, pop
        skip eq
        bra fail
        ld push, #72            ; 'H'
        st [0xf000], pop

; I: a count of 0 leaves C and the item (the count in TOS stays for dd nos)
        ld push, #0x1234
        ld push, #1
        shr tos, #1             ; C = 1, TOS = 0
        shl nos, tos            ; by a count of 0
        skip cs
        bra fail
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        sub tos, #0x1234
        st null, pop
        skip eq
        bra fail
        ld push, #73            ; 'I'
        st [0xf000], pop

; J: a shift clears V
        ld push, #0x7fff
        add tos, #1             ; V is set
        skip vs
        bra fail
        ld push, #0x0100
        shr tos, #8             ; a real shift clears V
        skip vc
        bra fail
        sub tos, #1
        st null, pop
        skip eq
        bra fail
        st null, pop
        ld push, #74            ; 'J'
        st [0xf000], pop

; K: shr.b by a count in TOS
        ld push, #0x00f0
        ld push, #4
        shr.b push, tos         ; byte, count in TOS
        skip cc
        bra fail
        skip vc
        bra fail
        skip pl
        bra fail
        skip ne
        bra fail
        sub tos, #0x0f
        st null, pop
        skip eq
        bra fail
        ld push, #75            ; 'K'
        st [0xf000], pop

; L: rol by 4: 0x1234 -> 0x2341, C = the bit that wrapped
        ld push, #0x1234
        rol tos, #4
        skip cs
        bra fail
        skip vc
        bra fail
        skip pl
        bra fail
        skip ne
        bra fail
        sub tos, #0x2341
        st null, pop
        skip eq
        bra fail
        ld push, #76            ; 'L'
        st [0xf000], pop

; M: ror by 4: 0x1234 -> 0x4123, C = bit 15 of the result
        ld push, #0x1234
        ror tos, #4
        skip cc
        bra fail
        skip vc
        bra fail
        skip pl
        bra fail
        skip ne
        bra fail
        sub tos, #0x4123
        st null, pop
        skip eq
        bra fail
        ld push, #77            ; 'M'
        st [0xf000], pop

; N: rcl by 1 through carry: the old C comes in, the sign goes to C
        setb #1                 ; C = 1
        ld push, #0x8000
        rcl tos, #1
        skip cs
        bra fail
        skip vc
        bra fail
        skip pl
        bra fail
        skip ne
        bra fail
        sub tos, #0x1
        st null, pop
        skip eq
        bra fail
        ld push, #78            ; 'N'
        st [0xf000], pop

; O: rcr by 1 through carry
        setb #1
        ld push, #0x0001
        rcr tos, #1
        skip cs
        bra fail
        skip vc
        bra fail
        skip mi
        bra fail
        skip ne
        bra fail
        sub tos, #0x8000
        st null, pop
        skip eq
        bra fail
        ld push, #79            ; 'O'
        st [0xf000], pop

; P: rcl by 3 with the count in TOS
        setb #1
        ld push, #0x8001
        ld push, #3
        rcl push, tos           ; the ring of 17 bits by 3
        skip cc
        bra fail
        skip vc
        bra fail
        skip pl
        bra fail
        skip ne
        bra fail
        sub tos, #0xe
        st null, pop
        skip eq
        bra fail
        ld push, #80            ; 'P'
        st [0xf000], pop

; Q: a count of 0 in TOS is 16 for rcl and rcr
        clrb #1
        ld push, #0x1234
        ld push, #0
        rcl push, tos           ; the ring of 17 bits by 16
        skip cc
        bra fail
        skip vc
        bra fail
        sub tos, #0x91a
        st null, pop
        skip eq
        bra fail
        setb #1
        ld push, #0x0f01
        ld push, #17            ; the low 4 bits: 1
        rcr push, tos
        skip cs
        bra fail
        sub tos, #0x8780
        st null, pop
        skip eq
        bra fail
        ld push, #81            ; 'Q'
        st [0xf000], pop

; R: dd push and under with a count, and the count 16
        ld push, #0x0021
        ld push, #0x0100
        shl push, #4            ; the result of NOS on top, both stay
        sub tos, #0x0210
        st null, pop
        skip eq
        bra fail
        sub tos, #0x0100
        st null, pop
        skip eq
        bra fail
        sub tos, #0x0021
        st null, pop
        skip eq
        bra fail
        ld push, #0x0021
        ld push, #0x0100
        shr under, #8           ; the result of TOS goes below it
        sub tos, #0x0100
        st null, pop
        skip eq
        bra fail
        sub tos, #1
        st null, pop
        skip eq
        bra fail
        sub tos, #0x0021
        st null, pop
        skip eq
        bra fail
        ld push, #0x8000
        sar tos, #16            ; 16 is a count of its own
        skip cs
        bra fail
        sub tos, #0xffff
        st null, pop
        skip eq
        bra fail
        ld push, #82            ; 'R'
        st [0xf000], pop

stop:   bra stop
fail:   bra fail
