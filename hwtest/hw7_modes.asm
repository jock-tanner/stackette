; System and user contexts: TRAP, RETI, exceptions, soft reset.
; Expect ABCBCDEFGHSITJ, then silence.
; The context device is at 0xf020: user IP 0xf020, MBP 0xf026, PSP 0xf02c,
; interrupt ID 0xf02e, fault IP 0xf030. The UART is at 0xf000.

; ---- system context: starts here after power-up and after a soft reset ----
start:  ld push, [0xf02e]       ; interrupt ID, 0 after power-up
        sub tos, #0x89          ; a TRAP in system mode
        st null, pop
        skip ne
        bra soft_trap
        ld push, [0xf02e]
        sub tos, #0xc0          ; an illegal instruction in system mode
        st null, pop
        skip ne
        bra soft_ill

        ld push, #user
        st [0xf020], pop        ; the user context: IP
        ld push, #0xe800
        st [0xf02c], pop        ; and its own parameter stack

loop:   reti                    ; run the user context
        ; the system context carries on here after every exception of it
        ld push, [0xf02e]
        sub tos, #0x85          ; trap #5
        st null, pop
        skip ne
        bra h5
        ld push, [0xf02e]
        sub tos, #0xc0          ; illegal instruction
        st null, pop
        skip ne
        bra hill
        ld push, [0xf02e]
        sub tos, #0x87          ; trap #7
        st null, pop
        skip ne
        bra h7
        ld push, [0xf02e]
        sub tos, #0x88          ; trap #8
        st null, pop
        skip ne
        bra h8
        ld push, [0xf02e]
        sub tos, #0x8a          ; trap #10
        st null, pop
        skip ne
        bra h10
        ld push, [0xf02e]
        sub tos, #0x89          ; trap #9
        st null, pop
        skip ne
        bra h9
        ld push, [0xf02e]
        sub tos, #0x8b          ; trap #11
        st null, pop
        skip ne
        bra h11
        ld push, #63            ; '?'
        st [0xf000], pop
stop:   bra stop

h5:     ld push, #65            ; 'A'
        st [0xf000], pop
        bra loop

; an illegal instruction of the user: 'B', 'C' if the fault IP is its IP;
; then it is skipped
hill:   ld push, #66
        st [0xf000], pop
        ld push, [0xf030]
        sub tos, [0xf020]
        st null, pop
        skip eq
        bra hill1
        ld push, #67
        st [0xf000], pop
hill1:  ld push, [0xf020]
        add tos, #2
        st [0xf020], pop
        bra loop

; the user's MBP is in the context: 'D'. The system's own MBP is another one.
h7:     ld push, [0xf026]
        sub tos, #0x1234
        st null, pop
        skip eq
        bra h7a
        ld push, #68
        st [0xf000], pop
h7a:    ld push, #0x5555
        st mbp, pop
        bra loop

; the flags are separate: the user's carry must survive these
h8:     clrb #0x1f
        setb #0x0e
        bra loop

; the stacks are separate
h10:    ld push, #0x99
        ld push, #0x98
        st null, pop
        st null, pop
        bra loop

; a TRAP in system mode is a soft reset: it comes back to `start`
h9:     trap #9
h11:    .word 0xffff

soft_trap:
        ld push, #83            ; 'S'
        st [0xf000], pop
        bra loop
soft_ill:
        ld push, #84            ; 'T'
        st [0xf000], pop
        bra loop

; ---- user context ----
user:   trap #5                 ; A
        .word 0xffff            ; B C
        reti                    ; illegal in user mode: B C

        ld push, #0x1234
        st mbp, pop
        trap #7                 ; D
        ld push, mbp
        sub tos, #0x1234
        st null, pop
        skip eq
        bra u1
        ld push, #69            ; E
        st [0xf000], pop

u1:     clrb #0x1f
        setb #1                 ; carry
        trap #8
        skip cs
        bra u2
        ld push, #70            ; F
        st [0xf000], pop

u2:     ld push, #0x11
        ld push, #0x22
        trap #10
        sub tos, #0x22
        st null, pop
        skip eq
        bra u3
        ld push, #71            ; G
        st [0xf000], pop
u3:     sub tos, #0x11
        st null, pop
        skip eq
        bra u4
        ld push, #72            ; H
        st [0xf000], pop

u4:     trap #9                 ; S
        ld push, #73            ; I
        st [0xf000], pop
        trap #11                ; T
        ld push, #74            ; J
        st [0xf000], pop
done:   bra done
