; The UART receiver interrupts. Prints '>' and echoes every byte it receives,
; from the interrupt handler, while the user context counts in [0x5002]. A '?'
; is not echoed but answered: 'Y' if the user context has been running, 'N'
; if not. On a terminal: type abc? and expect >abcY.
; The UART is at 0xf000 (DATA, STATUS), the interrupt controller at 0xf008
; (RAW, MASK, ACTIVE): the UART receiver is its line 1, the timer is line 0.

start:  ld push, [0xf02e]       ; interrupt ID, 0 after power-up
        sub tos, #0x41          ; the class 1, the line 1: the UART
        st null, pop
        skip ne
        bra rx

        ld push, #62            ; '>'
        st [0xf000], pop
        ld push, #0
        st [0x5002], pop        ; the counter of the user
        ld push, #2
        st [0xf00a], pop        ; MASK: the UART
        ld push, #user
        st [0xf020], pop
        ld push, #0xe800
        st [0xf02c], pop

loop:   reti
        ld push, [0xf02e]
        sub tos, #0x41
        st null, pop
        skip ne
        bra rx
        ld push, #33            ; '!': not expected
        st [0xf000], pop
stop:   bra stop

; a byte has been received: reading it takes the line down
rx:     ld push, [0xf000]
        ld push, tos
        sub tos, #63            ; '?'
        st null, pop
        skip ne
        bra ask
        st [0xf000], pop        ; echo it
        bra loop

ask:    st null, pop            ; drop the byte
        ld push, [0x5002]
        sub tos, #0
        st null, pop
        skip eq
        bra yes
        ld push, #78            ; 'N'
        st [0xf000], pop
        bra loop
yes:    ld push, #89            ; 'Y'
        st [0xf000], pop
        bra loop

; ---- user context ----
user:   ld push, [0x5002]
        add tos, #1
        st [0x5002], pop
        bra user
