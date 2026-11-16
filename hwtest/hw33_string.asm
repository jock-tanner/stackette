; hw33: the string forms of the pointer modes, `[mbp+midx++]*` and `[mbp+--midx]*`
; (001 1 mm e): the instruction repeats until MIDX is 0, and is skipped if MIDX is 0
; at the start. Expect ABCDEFGH, then silence. A check that fails stops the
; program with the letter missing. (RAM at 0x5000..; the stack pointer is kept at 0x5100)

start:
; A: a copy from the stack space, post-increment: the memory gets the image of the stack
        ld push, sp
        st [0x5100], pop
        ld push, #1
        ld push, #2
        ld push, #3
        ld push, #4
        ld push, #5
        ld push, #6
        ld push, #7
        ld push, #8
        ld push, #0x5010
        st mbp, pop
        ld push, #0xfff0
        st midx, pop
        st [mbp+midx++]*, pop
        ld push, midx
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, sp
        sub tos, [0x5100]
        st null, pop
        skip eq
        bra fail
        ld push, [0x5000]
        sub tos, #0x8
        st null, pop
        skip eq
        bra fail
        ld push, [0x500e]
        sub tos, #0x1
        st null, pop
        skip eq
        bra fail
        ld push, [0x5006]
        sub tos, #0x5
        st null, pop
        skip eq
        bra fail
        ld push, #65            ; 'A'
        st [0xf000], pop

; B: pre-decrement: the same words, in the reverse order
        ld push, #1
        ld push, #2
        ld push, #3
        ld push, #4
        ld push, #0x5200
        st mbp, pop
        ld push, #0x8
        st midx, pop
        st [mbp+--midx]*, pop
        ld push, midx
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, sp
        sub tos, [0x5100]
        st null, pop
        skip eq
        bra fail
        ld push, [0x5200]
        sub tos, #0x1
        st null, pop
        skip eq
        bra fail
        ld push, [0x5206]
        sub tos, #0x4
        st null, pop
        skip eq
        bra fail
        ld push, #66            ; 'B'
        st [0xf000], pop

; C: MIDX = 0: nothing is done, the value stays on the stack
        ld push, #0x1234
        st [0x5300], pop
        ld push, #5
        ld push, #0x5300
        st mbp, pop
        ld push, #0x0
        st midx, pop
        st [mbp+midx++]*, pop
        sub tos, #5
        st null, pop
        skip eq
        bra fail
        ld push, sp
        sub tos, [0x5100]
        st null, pop
        skip eq
        bra fail
        ld push, [0x5300]
        sub tos, #0x1234
        st null, pop
        skip eq
        bra fail
        ld push, #67            ; 'C'
        st [0xf000], pop

; D: a fill with TOS: the stack stays
        ld push, #0x4321
        st [0x5408], pop
        ld push, #0xabcd
        ld push, #0x5408
        st mbp, pop
        ld push, #0xfff8
        st midx, pop
        st [mbp+midx++]*, tos
        ld push, midx
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, [0x5400]
        sub tos, #0xabcd
        st null, pop
        skip eq
        bra fail
        ld push, [0x5406]
        sub tos, #0xabcd
        st null, pop
        skip eq
        bra fail
        ld push, [0x5408]
        sub tos, #0x4321
        st null, pop
        skip eq
        bra fail
        sub tos, #0xabcd
        st null, pop
        skip eq
        bra fail
        ld push, sp
        sub tos, [0x5100]
        st null, pop
        skip eq
        bra fail
        ld push, #68            ; 'D'
        st [0xf000], pop

; E: bytes: five of them, the sixth stays
        ld push, #0x55
        st.b [0x5505], pop
        ld push, #0x77
        ld push, #0x5505
        st mbp, pop
        ld push, #0xfffb
        st midx, pop
        st.b [mbp+midx++]*, tos
        ld push, midx
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld.b push, [0x5500]
        sub tos, #0x77
        st null, pop
        skip eq
        bra fail
        ld.b push, [0x5504]
        sub tos, #0x77
        st null, pop
        skip eq
        bra fail
        ld.b push, [0x5505]
        sub tos, #0x55
        st null, pop
        skip eq
        bra fail
        st null, pop
        ld push, sp
        sub tos, [0x5100]
        st null, pop
        skip eq
        bra fail
        ld push, #69            ; 'E'
        st [0xf000], pop

; F: a sum of words, the ALU repeats
        ld push, #1
        st [0x5600], pop
        ld push, #2
        st [0x5602], pop
        ld push, #3
        st [0x5604], pop
        ld push, #4
        st [0x5606], pop
        ld push, #0x5608
        st mbp, pop
        ld push, #0xfff8
        st midx, pop
        ld push, #0
        add tos, [mbp+midx++]*
        sub tos, #10
        st null, pop
        skip eq
        bra fail
        ld push, midx
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, sp
        sub tos, [0x5100]
        st null, pop
        skip eq
        bra fail
        ld push, #70            ; 'F'
        st [0xf000], pop

; G: LD pushes the words one after another
        ld push, #0x5606
        st mbp, pop
        ld push, #0xfffa
        st midx, pop
        ld push, [mbp+midx++]*
        ld push, midx
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, [sp+0]
        sub tos, #3
        st null, pop
        skip eq
        bra fail
        ld push, [sp+1]
        sub tos, #2
        st null, pop
        skip eq
        bra fail
        ld push, [sp+2]
        sub tos, #1
        st null, pop
        skip eq
        bra fail
        addp sp, #6
        ld push, sp
        sub tos, [0x5100]
        st null, pop
        skip eq
        bra fail
        ld push, #71            ; 'G'
        st [0xf000], pop

; H: a byte copy with the pre-decrement mode from the stack space
        ld push, #0x44
        ld push, #0x33
        ld push, #0x22
        ld push, #0x11
        ld push, #0x5700
        st mbp, pop
        ld push, #0x3
        st midx, pop
        st.b [mbp+--midx]*, pop
        ld push, midx
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld.b push, [0x5700]
        sub tos, #0x33
        st null, pop
        skip eq
        bra fail
        ld.b push, [0x5701]
        sub tos, #0x22
        st null, pop
        skip eq
        bra fail
        ld.b push, [0x5702]
        sub tos, #0x11
        st null, pop
        skip eq
        bra fail
        st null, pop            ; the 0x44 that was not popped
        ld push, sp
        sub tos, [0x5100]
        st null, pop
        skip eq
        bra fail
        ld push, #72            ; 'H'
        st [0xf000], pop

stop:   bra stop
fail:   bra fail
