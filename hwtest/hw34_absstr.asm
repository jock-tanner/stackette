; hw34: the string form of [abs16], `[abs]*` (01 0010 e): the address stays, MIDX
; is the byte count and goes down by the size to 0; skipped (with its address word)
; if MIDX is 0 at the start. Expect ABCD. (RAM at 0x5000.., stack pointer kept at 0x5100)

start:
        ld push, sp
        st [0x5100], pop
; A: pops into one word, the last one stays
        ld push, #1
        ld push, #2
        ld push, #3
        ld push, #4
        ld push, #0x8
        st midx, pop
        st [0x5000]*, pop
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
        sub tos, #0x1
        st null, pop
        skip eq
        bra fail
        ld push, #65
        st [0xf000], pop
; B: three pushes of one word
        ld push, #0x1234
        st [0x5002], pop
        ld push, #0x6
        st midx, pop
        ld push, [0x5002]*
        ld push, midx
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, [sp+0]
        sub tos, #0x1234
        st null, pop
        skip eq
        bra fail
        ld push, [sp+1]
        sub tos, #0x1234
        st null, pop
        skip eq
        bra fail
        ld push, [sp+2]
        sub tos, #0x1234
        st null, pop
        skip eq
        bra fail
        addp sp, #6
        ld push, sp
        sub tos, [0x5100]
        st null, pop
        skip eq
        bra fail
        ld push, #66
        st [0xf000], pop
; C: nothing to do: the stack stays, the address word is skipped
        ld push, #5
        ld push, #0x0
        st midx, pop
        st [0x5000]*, pop
        sub tos, #5
        st null, pop
        skip eq
        bra fail
        ld push, sp
        sub tos, [0x5100]
        st null, pop
        skip eq
        bra fail
        ld push, [0x5000]
        sub tos, #0x1
        st null, pop
        skip eq
        bra fail
        ld push, #67
        st [0xf000], pop
; D: bytes
        ld push, #0x5a
        ld push, #0x3
        st midx, pop
        st.b [0x5200]*, tos
        ld push, midx
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        st null, pop
        ld.b push, [0x5200]
        sub tos, #0x5a
        st null, pop
        skip eq
        bra fail
        ld push, #68
        st [0xf000], pop

stop:   bra stop
fail:   bra fail
