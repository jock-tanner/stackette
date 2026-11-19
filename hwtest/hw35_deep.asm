; hw35: long runs of the string forms through the stack cache: 256 words are pushed
; by one instruction (the cache spills them to memory as they come) and popped by
; another (it fills them again). Expect AB, then silence. A check that fails stops
; the program with the letter missing. (RAM at 0x5000..; the stack pointer is kept
; at 0x5100)

start:
        ld push, sp
        st [0x5100], pop

; A: a table of 256 words at 0x6000, each is its own offset from 0x6200: the words are
; pushed in the reverse order, and popped to another table at 0x6200: the copy must be
; the same
        ld push, #0x6200
        st mbp, pop
        ld push, #0xfe00
        st midx, pop
fill:   ld push, midx
        st [mbp+midx++], pop
        ld push, midx
        sub tos, #0
        st null, pop
        skip eq
        bra fill
        ld push, #0x6000
        st mbp, pop
        ld push, #0x200
        st midx, pop
        ld push, [mbp+--midx]*
        ld push, midx
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, #0x6400
        st mbp, pop
        ld push, #0xfe00
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
        ld push, #0xfe00
        st midx, pop
chk:    ld push, [mbp+midx]
        sub tos, midx
        st null, pop
        skip eq
        bra fail
        ld push, midx
        add tos, #2
        st midx, pop
        ld push, midx
        sub tos, #0
        st null, pop
        skip eq
        bra chk
        ld push, #65            ; 'A'
        st [0xf000], pop

; B: the same word pushed 200 times from one address, and popped into another
        ld push, #0x7e57
        st [0x6600], pop
        ld push, #400
        st midx, pop
        ld push, [0x6600]*
        ld push, midx
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, [sp+0]
        sub tos, #0x7e57
        st null, pop
        skip eq
        bra fail
        ld push, [sp+31]
        sub tos, #0x7e57
        st null, pop
        skip eq
        bra fail
        ld push, #400
        st midx, pop
        st [0x6602]*, pop
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
        ld push, [0x6602]
        sub tos, #0x7e57
        st null, pop
        skip eq
        bra fail
        ld push, #66            ; 'B'
        st [0xf000], pop

stop:   bra stop
fail:   bra fail
