; SWAP, the atomic exchange of TOS or NOS with a word or byte of memory.
; Expect ABCDEFG, then silence. (RAM at 0x5000..)

start:  ld push, [0xf02e]       ; interrupt ID, 0 after power-up
        sub tos, #0xc0          ; the illegal instruction at the end
        st null, pop
        skip ne
        bra illegal

; A: a word, TOS <-> [0x5000]
        ld push, #0x1234
        st [0x5000], pop
        ld push, #0xabcd
        swap tos, [0x5000]
        sub tos, #0x1234        ; TOS has the old memory word
        st null, pop
        skip ne
        bra a1
        bra b
a1:     ld push, [0x5000]
        sub tos, #0xabcd        ; and memory the old TOS
        st null, pop
        skip eq
        bra b
        ld push, #65            ; 'A'
        st [0xf000], pop

; B: NOS <-> [0x5002], through MBP and MIDX with the post-increment
b:      ld push, #0x5000
        st mbp, pop
        ld push, #2
        st midx, pop
        ld push, #0x1111
        st [0x5002], pop
        ld push, #0x2222
        st [0x5004], pop
        ld push, #0x3333        ; NOS
        ld push, #0x4444        ; TOS
        swap nos, [mbp+midx++]  ; [0x5002] <-> NOS, MIDX = 4
        sub tos, #0x4444        ; TOS is not touched
        st null, pop
        skip ne
        bra b1
        bra c
b1:     ld push, midx
        sub tos, #4
        st null, pop
        skip ne
        bra b2
        bra c
b2:     ld push, [0x5002]
        sub tos, #0x3333
        st null, pop
        skip ne
        bra b3
        bra c
b3:     ld push, tos            ; the old [0x5002] is NOS now ...
        sub tos, #0x1111
        st null, pop
        skip eq
        bra c
        ld push, #66            ; 'B'
        st [0xf000], pop
        st null, pop            ; drop NOS

; C: a byte: the memory byte goes to the stack zero-extended, the low byte
; of TOS to memory, the other byte stays
c:      ld push, #0x1234
        st [0x5006], pop
        ld push, #0xbeef
        swap.b tos, [0x5006]    ; the low byte of the word
        sub tos, #0x34
        st null, pop
        skip ne
        bra c1
        bra d
c1:     ld push, [0x5006]
        sub tos, #0x12ef
        st null, pop
        skip eq
        bra d
        ld push, #0x00aa
        swap.b tos, [0x5007]    ; the high byte
        sub tos, #0x12
        st null, pop
        skip ne
        bra c2
        bra d
c2:     ld push, [0x5006]
        sub tos, #0xaaef
        st null, pop
        skip eq
        bra d
        ld push, #67            ; 'C'
        st [0xf000], pop

; D: an item of the stack itself: with two items the second is at 0xeffe,
; so this is an exchange of TOS and NOS
d:      ld push, #7
        ld push, #9
        swap tos, [0xeffe]
        ld push, tos
        sub tos, #7
        st null, pop
        skip ne
        bra d1
        bra e
d1:     ld push, nos
        sub tos, #9
        st null, pop
        skip eq
        bra e
        ld push, #68            ; 'D'
        st [0xf000], pop
e0:     st null, pop            ; drop the two items
        st null, pop

; E: a lock: the one who gets 0 has taken it
e:      ld push, #0
        st [0x5008], pop
        ld push, #1
        swap tos, [0x5008]
        sub tos, #0             ; the first gets 0 ...
        st null, pop
        skip ne
        bra e1
        bra f
e1:     ld push, #1
        swap tos, [0x5008]
        sub tos, #1             ; ... the second gets 1
        st null, pop
        skip eq
        bra f
        ld push, #69            ; 'E'
        st [0xf000], pop

; F: the stack is as it was: the depth of the stack did not change
f:      ld push, sp
        sub tos, #0xf000
        st null, pop
        skip ne
        bra f1
        bra g
f1:     ld push, #70            ; 'F'
        st [0xf000], pop

; G: swap with push (dd = 2) does not exist: the illegal instruction
g:      .word 0xaa20, 0x5000

stop:   bra stop

illegal: ld push, #71           ; 'G'
        st [0xf000], pop
        bra stop
