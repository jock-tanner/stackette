; The MMU. Expect ABCDEFG, then silence.
; The user context's registers are at 0xf180 (CODE, 16 words), 0xf1a0 (RSTACK),
; 0xf1c0 (PSTACK) and 0xf1e0 (DATA): word k of a set maps virtual page k.
; The system context's are 64 words (0x80 bytes) lower, at 0xf100.. The
; context device is at 0xf020, the UART at 0xf000.

start:  ld push, [0xf02e]       ; interrupt ID, 0 after power-up
        sub tos, #0x81          ; trap #1 in system mode: a soft reset
        st null, pop
        skip ne
        bra soft

        ld push, #65            ; 'A'
        st [0xf000], pop
        ; the user's data page 3 is the physical page 5, its page 4 is a
        ; page that has no memory
        ld push, #5
        st [0xf1e6], pop
        ld push, #0x3000
        st [0xf1e8], pop
        ld push, #0x1234
        st [0x5000], pop        ; the physical page 5, by the identity map
        ld push, #user
        st [0xf020], pop
        ld push, #0xe800
        st [0xf02c], pop

loop:   reti
        ld push, [0xf02e]
        sub tos, #0x82          ; trap #2
        st null, pop
        skip ne
        bra h2
        ld push, [0xf02e]
        sub tos, #0xc1          ; a request was refused
        st null, pop
        skip ne
        bra hfault
        ld push, [0xf02e]
        sub tos, #0x83          ; trap #3
        st null, pop
        skip ne
        bra h3
        ld push, #63            ; '?'
        st [0xf000], pop
stop:   bra stop

; the user wrote 0x4321 to its data page 3, which is the physical page 5
h2:     ld push, [0x5002]
        sub tos, #0x4321
        st null, pop
        skip eq
        bra loop
        ld push, #67            ; 'C'
        st [0xf000], pop
        bra loop

; the user read its data page 4: the fault IP is that instruction, the fault
; address is the virtual one. The instruction has two words.
hfault: ld push, [0xf032]
        sub tos, #0x4010
        st null, pop
        skip eq
        bra hf1
        ld push, #68            ; 'D'
        st [0xf000], pop
hf1:    ld push, [0xf030]
        add tos, #4
        st [0xf020], pop
        bra loop

; the system context maps its data page 6 to the physical page 7 ...
h3:     ld push, #7
        st [0xf16c], pop
        ld push, #0x5a
        st [0x7000], pop
        ld push, [0x6000]
        sub tos, #0x5a
        st null, pop
        skip eq
        bra h3a
        ld push, #69            ; 'E'
        st [0xf000], pop
h3a:    trap #1                 ; ... and is reset: the map is the default again

soft:   ld push, #0x66
        st [0x6000], pop        ; the physical page 6 again
        ld push, [0x7000]       ; must not have changed
        sub tos, #0x5a
        st null, pop
        skip eq
        bra loop
        ld push, [0x6000]
        sub tos, #0x66
        st null, pop
        skip eq
        bra loop
        ld push, #70            ; 'F'
        st [0xf000], pop
        bra loop

; ---- user context ----
user:   ld push, [0x3000]       ; the physical page 5
        sub tos, #0x1234
        st null, pop
        skip eq
        bra u1
        ld push, #66            ; 'B'
        st [0xf000], pop
u1:     ld push, #0x4321
        st [0x3002], pop
        trap #2                 ; C
        ld push, [0x4010]       ; refused: D
        trap #3                 ; E, then a soft reset of the system: F
        ld push, [0x3000]       ; the user's map is as it was
        sub tos, #0x1234
        st null, pop
        skip eq
        bra done
        ld push, #71            ; 'G'
        st [0xf000], pop
done:   bra done
