; MUL, DIV and NOP. Expect ABCDEFG, then silence. A check that fails stops the
; program with the letter missing. Each step leaves the stack as it found it.
; The last step is a division by zero, fault 4: it resets the system context
; and the interrupt ID tells it.

start:  ld push, [0xf02e]       ; interrupt ID, 0 after power-up
        sub tos, #0xc4          ; division by zero
        st null, pop
        skip ne
        bra div0

; A: mulu: 300 * 200 = 0xea60, nothing in the high part, no C and V
        ld push, #300
        ld push, #200
        mulu
        nop                     ; changes nothing
        skip cc
        bra fail
        skip vc
        bra fail
        sub tos, #0xea60        ; the low part is TOS
        st null, pop
        skip eq
        bra fail
        sub tos, #0             ; the high part is NOS, now TOS
        st null, pop
        skip eq
        bra fail
        ld push, #65            ; 'A'
        st [0xf000], pop

; B: muls: -3 * 7 = -21, the high part is the sign: no C and V; N and not Z
        ld push, #0xfffd
        ld push, #7
        muls
        skip cc
        bra fail
        skip vc
        bra fail
        skip mi
        bra fail
        skip ne
        bra fail
        sub tos, #0xffeb
        st null, pop
        skip eq
        bra fail
        sub tos, #0xffff
        st null, pop
        skip eq
        bra fail
        ld push, #66            ; 'B'
        st [0xf000], pop

; C: mulu with a high part: 0x8000 * 4 = 0x20000, C and V and Z
        ld push, #0x8000
        ld push, #4
        mulu
        skip cs
        bra fail
        skip vs
        bra fail
        skip eq
        bra fail
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        sub tos, #2
        st null, pop
        skip eq
        bra fail
        ld push, #67            ; 'C'
        st [0xf000], pop

; D: divu 1000 / 7 = 142, remainder 6; divs -1000 / 7 = -142, remainder -6.
; DIV leaves C alone and sets V only for an overflow
        setb #1                 ; C
        ld push, #1000
        ld push, #7
        divu
        skip cs
        bra fail
        skip vc
        bra fail
        sub tos, #142
        st null, pop
        skip eq
        bra fail
        sub tos, #6
        st null, pop
        skip eq
        bra fail
        clrb #1
        ld push, #0xfc18        ; -1000
        ld push, #7
        divs
        skip cc
        bra fail
        skip mi
        bra fail
        sub tos, #0xff72        ; -142
        st null, pop
        skip eq
        bra fail
        sub tos, #0xfffa        ; -6
        st null, pop
        skip eq
        bra fail
        ld push, #68            ; 'D'
        st [0xf000], pop

; E: the byte forms look at the low bytes and give zero-extended bytes
        ld push, #0x1264        ; NOS 0x64 = 100
        ld push, #0xff90        ; TOS 0x90 = 144
        mulu.b                  ; 14400 = 0x3840
        sub tos, #0x40
        st null, pop
        skip eq
        bra fail
        sub tos, #0x38
        st null, pop
        skip eq
        bra fail
        ld push, #0x7780        ; -128
        ld push, #0x5580        ; -128
        muls.b                  ; 0x4000: the high part is significant
        skip vs
        bra fail
        skip cs
        bra fail
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        sub tos, #0x40
        st null, pop
        skip eq
        bra fail
        ld push, #0x33f6        ; -10
        ld push, #0x7703        ; 3
        divs.b                  ; -3 remainder -1
        sub tos, #0xfd
        st null, pop
        skip eq
        bra fail
        sub tos, #0xff
        st null, pop
        skip eq
        bra fail
        ld push, #69            ; 'E'
        st [0xf000], pop

; F: the most negative number divided by -1: the quotient wraps, V is set
        ld push, #0x8000
        ld push, #0xffff
        divs
        skip vs
        bra fail
        sub tos, #0x8000
        st null, pop
        skip eq
        bra fail
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, #70            ; 'F'
        st [0xf000], pop

; G: a division by zero is a fault: the system context resets, and starts
; at the top again, with the ID 0xc4
        ld push, #5
        ld push, #0
        divu
fail:   bra fail

div0:   ld push, #71            ; 'G'
        st [0xf000], pop
stop:   bra stop
