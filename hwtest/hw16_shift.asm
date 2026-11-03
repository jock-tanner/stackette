; SHR and SAR: shifts right by one bit, the carry gets the bit that fell out.
; Long shifts take two instructions: SHR or SAR on the high word (NOS), then
; RRC on the low word (TOS); left: ADD TOS, TOS and ADC NOS, NOS. Expect
; ABCDEFGH, then silence. A check that fails stops the program with the letter
; missing. Each step leaves the stack as it found it.

start:

; A: shr: 0x8003 >> 1 = 0x4001, C = 1, not negative
        ld push, #0x8003
        shr tos
        skip cs
        bra fail
        skip pl
        bra fail
        sub tos, #0x4001
        st null, pop
        skip eq
        bra fail
        ld push, #65            ; 'A'
        st [0xf000], pop

; B: sar: 0x8002 >> 1 = 0xc001, C = 0, negative
        ld push, #0x8002
        sar tos
        skip cc
        bra fail
        skip mi
        bra fail
        sub tos, #0xc001
        st null, pop
        skip eq
        bra fail
        ld push, #66            ; 'B'
        st [0xf000], pop

; C: sar of a positive number: 0x4001 >> 1 = 0x2000, C = 1
        ld push, #0x4001
        sar tos
        skip cs
        bra fail
        sub tos, #0x2000
        st null, pop
        skip eq
        bra fail
        ld push, #67            ; 'C'
        st [0xf000], pop

; D: the other items: shr nos, sar push, shr under; V is not changed
        setb #8
        ld push, #6
        ld push, #0x8000
        shr nos                 ; 6 >> 1 = 3 below
        skip vs
        bra fail
        clrb #8
        sub tos, #0x8000
        st null, pop
        sub tos, #3
        st null, pop
        skip eq
        bra fail
        ld push, #68            ; 'D'
        st [0xf000], pop

; E: the byte forms: shr.b 0x01fe >> 1 = 0x7f; sar.b 0x0080 >> 1 = 0xc0
        ld push, #0x01fe
        shr.b tos
        sub tos, #0x7f
        st null, pop
        skip eq
        bra fail
        ld push, #0x0080
        sar.b tos
        sub tos, #0xc0
        st null, pop
        skip eq
        bra fail
        ld push, #69            ; 'E'
        st [0xf000], pop

; F: a long shifted right, logical: 0x80000001 >> 1 = 0x40000000
        ld push, #0x8000        ; the high word is NOS
        ld push, #1             ; the low word is TOS
        shr nos
        rrc tos
        skip cs                 ; the bit that fell out of the low word
        bra fail
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        sub tos, #0x4000
        st null, pop
        skip eq
        bra fail
        ld push, #70            ; 'F'
        st [0xf000], pop

; G: a long shifted right, arithmetic: 0x80000003 >> 1 = 0xc0000001
        ld push, #0x8000
        ld push, #3
        sar nos
        rrc tos
        skip cs
        bra fail
        sub tos, #1
        st null, pop
        skip eq
        bra fail
        sub tos, #0xc000
        st null, pop
        skip eq
        bra fail
        ld push, #71            ; 'G'
        st [0xf000], pop

; H: a long shifted left: 0x40008001 << 1 = 0x80010002
        ld push, #0x4000
        ld push, #0x8001
        add tos, tos
        adc nos, nos
        sub tos, #2
        st null, pop
        skip eq
        bra fail
        sub tos, #0x8001
        st null, pop
        skip eq
        bra fail
        ld push, #72            ; 'H'
        st [0xf000], pop

done:   bra done
fail:   bra fail
