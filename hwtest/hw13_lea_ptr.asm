; LEA (the address of a frame item) and [tos] (a pointer on the stack).
; Expect ABCDEFGHI, then silence. A check that fails stops the program with
; its letter missing. (RAM at 0x5000..)

start:  ld push, [0xf02e]       ; interrupt ID, 0 after power-up
        sub tos, #0xc0          ; the illegal instruction at the end
        st null, pop
        skip ne
        bra illegal

; A: lea: SFP is 0xf000 here; all four dd forms, no flags
        setb #0x1f
        lea push, [pfp-4]
        skip cs
        bra fail
        skip vs
        bra fail
        sub tos, #0xeffc
        st null, pop
        skip eq
        bra fail
        ld push, #1
        ld push, #2
        lea under, [pfp-8]      ; 1, 0xeff8, 2
        lea nos, [pfp+126]      ; 1, 0xf07e, 2 is replaced: NOS = 0xf07e
        sub tos, #2
        st null, pop
        skip eq
        bra fail
        sub tos, #0xf07e
        st null, pop
        skip eq
        bra fail
        sub tos, #1
        st null, pop
        skip eq
        bra fail
        ld push, #65            ; 'A'
        st [0xf000], pop

; B: ld tos, [tos] is `@`
b:      ld push, #0x1234
        st [0x5000], pop
        ld push, #0x5000
        ld tos, [tos]
        sub tos, #0x1234
        st null, pop
        skip eq
        bra fail
        ld push, #66            ; 'B'
        st [0xf000], pop

; C: ld push, [tos] keeps the pointer, ld nos, [tos] replaces NOS,
; ld under inserts below the pointer
        ld push, #0x4321
        st [0x5002], pop
        ld push, #0x5002
        ld push, [tos]          ; 0x5002, 0x4321
        sub tos, #0x4321
        st null, pop
        skip eq
        bra fail
        sub tos, #0x5002
        st null, pop
        skip eq
        bra fail
        ld push, #0
        ld push, #0x5002
        ld nos, [tos]           ; 0x4321, 0x5002
        st null, pop            ; the pointer
        sub tos, #0x4321
        st null, pop
        skip eq
        bra fail
        ld push, #0x5002
        ld under, [tos]         ; 0x5002, 0x4321 (the value is below)
        sub tos, #0x5002
        st null, pop
        skip eq
        bra fail
        sub tos, #0x4321
        st null, pop
        skip eq
        bra fail
        ld push, #67            ; 'C'
        st [0xf000], pop

; D: st [tos], under stores NOS and removes it, the pointer stays;
; st [tos], nos stores NOS and keeps both
        ld push, #0xabcd
        ld push, #0x5004
        st [tos], under
        sub tos, #0x5004
        st null, pop
        skip eq
        bra fail
        ld push, [0x5004]
        sub tos, #0xabcd
        st null, pop
        skip eq
        bra fail
        ld push, #0x7777
        ld push, #0x5006
        st [tos], nos
        st null, pop            ; the pointer
        sub tos, #0x7777        ; the value is still there
        st null, pop
        skip eq
        bra fail
        ld push, [0x5006]
        sub tos, #0x7777
        st null, pop
        skip eq
        bra fail
        ld push, #68            ; 'D'
        st [0xf000], pop

; E: the ALU through [tos]: dd push uses the pointer up
        ld push, #0x0100
        ld push, #0x5004
        add push, [tos]         ; 0x0100 + 0xabcd, the pointer is gone
        sub tos, #0xaccd
        st null, pop
        skip eq
        bra fail
        ld push, #0x0100
        ld push, #0x5004
        add nos, [tos]          ; the pointer stays
        st null, pop
        sub tos, #0xaccd
        st null, pop
        skip eq
        bra fail
        ld push, #69            ; 'E'
        st [0xf000], pop

; F: bytes: zero-extended loads, a store of one byte
        ld push, #0x12ef
        st [0x5008], pop
        ld push, #0x5009
        ld.b tos, [tos]
        sub tos, #0x12
        st null, pop
        skip eq
        bra fail
        ld push, #0x5008
        ld.b push, [tos]
        sub tos, #0xef
        st null, pop
        st null, pop
        ld push, #0xff55
        ld push, #0x5009
        st.b [tos], under       ; the byte 0x55 goes to the high byte
        st null, pop
        ld push, [0x5008]
        sub tos, #0x55ef
        st null, pop
        skip eq
        bra fail
        ld push, #70            ; 'F'
        st [0xf000], pop

; G: swap through the pointer: a lock, the one who gets 0 has taken it
        ld push, #0
        st [0x500a], pop
        ld push, #1
        ld push, #0x500a
        swap nos, [tos]
        st null, pop            ; the pointer
        sub tos, #0             ; the old contents
        st null, pop
        skip eq
        bra fail
        ld push, [0x500a]
        sub tos, #1
        st null, pop
        skip eq
        bra fail
        ld push, #71            ; 'G'
        st [0xf000], pop

; H: the stack is as it was
        ld push, sp
        sub tos, #0xf000
        st null, pop
        skip eq
        bra fail
        ld push, #72            ; 'H'
        st [0xf000], pop

; I: st [tos], pop does not exist: the illegal instruction
        .word 0xa670

fail:   bra fail

illegal: ld push, #73           ; 'I'
        st [0xf000], pop
stop:   bra stop
