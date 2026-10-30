; Interrupts: the timer and the interrupt controller. Expect ABCDE, then
; silence. The timer is at 0xf010 (COUNT, COMPARE, CONTROL, STATUS,
; PRESCALE), the interrupt controller at 0xf008 (RAW, MASK, ACTIVE), the
; context device at 0xf020, the UART at 0xf000.
; The user context counts in [0x5002] in a loop with a SKIP in it; the
; interrupt can come between the SKIP and the instruction it skips, and the
; loop must go on as if nothing happened (a '!' is printed if not).

start:  ld push, [0xf02e]       ; interrupt ID, 0 after power-up
        sub tos, #0x40          ; the class 1, the line 0: the timer
        st null, pop
        skip ne
        bra irq

        ld push, #65            ; 'A'
        st [0xf000], pop
        ld push, #0
        st [0x5000], pop        ; the number of interrupts so far
        ld push, #0
        st [0x5002], pop        ; the counter of the user
        ld push, #0x2000
        st [0xf012], pop        ; COMPARE
        ld push, #3
        st [0xf014], pop        ; CONTROL: run and reload
        ld push, #1
        st [0xf00a], pop        ; MASK: the timer
        ld push, #user
        st [0xf020], pop
        ld push, #0xe800
        st [0xf02c], pop

loop:   reti
        ld push, [0xf02e]
        sub tos, #0x40
        st null, pop
        skip ne
        bra irq
        ld push, #63            ; '?'
        st [0xf000], pop
stop:   bra stop

; the timer has matched: clear it, count, and after three of them go on
irq:    ld push, #1
        st [0xf016], pop        ; STATUS: clear MATCH
        ld push, [0x5000]
        add tos, #1
        st [0x5000], pop
        ld push, [0x5000]
        sub tos, #3
        st null, pop
        skip ne
        bra done
        bra loop

done:   ld push, #66            ; 'B'
        st [0xf000], pop
        ld push, #0
        st [0xf00a], pop        ; MASK: nothing
        ld push, #0
        st [0xf014], pop        ; CONTROL: stop
        ld push, #1
        st [0xf016], pop
        ld push, [0x5002]       ; the user has been counting
        sub tos, #0
        st null, pop
        skip eq
        bra countd
        bra stop
countd: ld push, #67            ; 'C'
        st [0xf000], pop
        ; in the system context the interrupts wait: the line is active and
        ; unmasked, and nothing happens until RETI
        ld push, #0
        st [0xf010], pop        ; COUNT
        ld push, #3
        st [0xf014], pop
        ld push, #1
        st [0xf00a], pop
wait:   ld push, [0xf016]       ; STATUS: MATCH comes while we are here
        and tos, #1
        st null, pop
        skip ne
        bra wait
        ld push, [0xf00c]       ; ACTIVE
        sub tos, #1
        st null, pop
        skip eq
        bra stop
        ld push, #68            ; 'D'
        st [0xf000], pop
        ld push, #0
        st [0xf00a], pop
        ld push, #0
        st [0xf014], pop
        ld push, #1
        st [0xf016], pop
        ld push, [0xf008]       ; RAW: the line is low
        sub tos, #0
        st null, pop
        skip eq
        bra stop
        ld push, #69            ; 'E'
        st [0xf000], pop
        bra stop

; ---- user context ----
user:   ld push, [0x5002]
        add tos, #1
        st [0x5002], pop
        skip ne
        bra dead                ; skipped, the counter is not 0
        bra user

dead:   ld push, #33            ; '!'
        st [0xf000], pop
        bra dead
