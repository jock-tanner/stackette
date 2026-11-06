; A small kernel: two processes with separate pages, system calls that
; return a result on the user stack, a timer that preempts, a fault, an exit.
; Expect ABCDEFTGHZ, then silence.
;
; The two processes run the same code page (the flash, code page 0) and have
; their own data page 3, return stack page 13 and a parameter stack and data
; page 14 (the same physical page in both spaces), and share data page 4.
; Everything else is mapped to a page that has no memory, so the user cannot
; reach the kernel's RAM or the peripherals: a refused access is a fault.
;
; The system context is a coroutine: `reti` runs the user context, and the
; system context carries on after it when the user traps, faults or is
; interrupted. A system call is a stub (`putc`, ...) whose `trap` makes the
; kernel read the argument at [pfp+0] of the user, and push the result on the
; user's parameter stack; the stub moves it into the argument slot, as the
; epilogue of a compiled function does.
;
; The context device is at 0xf020 (user IP RSP PFP MBP MIDX SW PSP, then the
; interrupt ID 0xf02e, fault IP 0xf030, fault address 0xf032), the MMU of the
; system at 0xf100 (code, return stack, parameter stack, data: 16 words each),
; that of the user at 0xf180. The kernel's data page 8 is a window on the
; stack page of the process that runs (so a user stack address 0xeXXX is
; 0x8XXX here), page 9 on its private data page, page 10 on the shared page.
;
; Process table at 0x5000, 32 bytes each (process 1 at 0x5020): the words IP
; RSP PFP MBP MIDX SW PSP of the user, the state (0 alive, 1 dead), the
; physical page of data page 3, of the stack page, of the return stack page.
; 0x5100 is the pointer to the process that runs.

start:  ; the table
        ld push, #u0
        st [0x5000], pop
        ld push, #u1
        st [0x5020], pop
        ld push, #0xd800
        st [0x5002], pop
        ld push, #0xd800
        st [0x5022], pop
        ld push, #0xe800
        st [0x5004], pop
        ld push, #0xe800
        st [0x5024], pop
        ld push, #0
        st [0x5006], pop
        ld push, #0
        st [0x5026], pop
        ld push, #0
        st [0x5008], pop
        ld push, #0
        st [0x5028], pop
        ld push, #0
        st [0x500a], pop
        ld push, #0
        st [0x502a], pop
        ld push, #0
        st [0x500e], pop
        ld push, #0
        st [0x502e], pop
        ld push, #0xe800
        st [0x500c], pop
        ld push, #0xe800
        st [0x502c], pop
        ld push, #0x10
        st [0x5010], pop
        ld push, #0x11
        st [0x5030], pop
        ld push, #0x14
        st [0x5012], pop
        ld push, #0x15
        st [0x5032], pop
        ld push, #0x16
        st [0x5014], pop
        ld push, #0x17
        st [0x5034], pop

        ; the memory the processes own: clear what the tests read
        ld push, #0x10
        st [0xf172], pop        ; window 9: the private page of process 0
        ld push, #0
        st [0x9002], pop
        ld push, #0x11
        st [0xf172], pop
        ld push, #0
        st [0x9002], pop
        ld push, #0x12
        st [0xf174], pop        ; window 10: the shared page
        ld push, #0
        st [0xa000], pop

        ; the user maps: no memory, except what a process may use
        ld push, #0xf182        ; code pages 1..15
        ld push, #15
        call fill
        ld push, #0xf1a0        ; return stack pages 0..15
        ld push, #16
        call fill
        ld push, #0xf1c0        ; parameter stack pages 0..15
        ld push, #16
        call fill
        ld push, #0xf1e2        ; data pages 1..15
        ld push, #15
        call fill
        ld push, #0x12
        st [0xf1e8], pop        ; data page 4: shared

        ld push, #65            ; 'A'
        st [0xf000], pop
        ld push, #0x5000
        st [0x5100], pop
        call loadctx

loop:   reti
        ld push, [0xf02e]
        sub tos, #0x81          ; trap #1: putc
        st null, pop
        skip ne
        bra h_putc
        ld push, [0xf02e]
        sub tos, #0x82          ; trap #2: yield
        st null, pop
        skip ne
        bra h_yield
        ld push, [0xf02e]
        sub tos, #0x83          ; trap #3: exit
        st null, pop
        skip ne
        bra h_exit
        ld push, [0xf02e]
        sub tos, #0x84          ; trap #4: start the timer
        st null, pop
        skip ne
        bra h_timer
        ld push, [0xf02e]
        sub tos, #0x40          ; interrupt, line 0: the timer
        st null, pop
        skip ne
        bra h_tick
        ld push, [0xf02e]
        sub tos, #0xc1          ; fault: a request was refused
        st null, pop
        skip ne
        bra h_fault
        ld push, #63            ; '?'
        st [0xf000], pop
stop:   bra stop

; ---- the system calls ----

; putc(c): write the character at [pfp] of the user to the UART
h_putc: ld push, [0xf024]       ; the user's PFP
        sub tos, #0x6000        ; its address in the window
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, [mbp+midx]
        st [0xf000], pop
        call pushz
        bra loop

; yield(): the next process runs
h_yield:
        call pushz
        call savectx
        call sched
        call loadctx
        bra loop

; exit(): the process is dead, the next runs
h_exit: ld push, [0x5100]
        st mbp, pop
        ld push, #14
        st midx, pop
        ld push, #1
        st [mbp+midx], pop
        call sched
        call loadctx
        bra loop

; timer(): start the timer, to preempt the process
h_timer:
        ld push, #1
        st [0xf016], pop        ; STATUS: clear MATCH
        ld push, #0
        st [0xf010], pop        ; COUNT
        ld push, #0x400
        st [0xf012], pop        ; COMPARE
        ld push, #3
        st [0xf014], pop        ; CONTROL: run and reload
        ld push, #1
        st [0xf00a], pop        ; MASK: the timer
        call pushz
        bra loop

; ---- the interrupt and the fault ----

; the timer has matched: stop it, 'T', and the next process runs
h_tick: ld push, #0
        st [0xf00a], pop
        ld push, #0
        st [0xf014], pop
        ld push, #1
        st [0xf016], pop
        ld push, #84            ; 'T'
        st [0xf000], pop
        call savectx
        call sched
        call loadctx
        bra loop

; a refused request: 'F' if the address is that of the UART, and the
; instruction (two words) is skipped
h_fault:
        ld push, [0xf032]
        sub tos, #0xf000
        st null, pop
        skip eq
        bra stop
        ld push, #70            ; 'F'
        st [0xf000], pop
        ld push, [0xf030]
        add tos, #4
        st [0xf020], pop
        bra loop

; ---- the subroutines of the kernel ----

; fill(first register, count): the registers get the page that has no memory
fill:   ld push, [pfp+2]
        st mbp, pop
        ld push, [pfp+0]
        add tos, tos
        st [0x5104], pop        ; the count of bytes
        ld push, #0
        st midx, pop
fill1:  ld push, #0x3000
        st [mbp+midx++], pop
        ld push, midx
        sub tos, [0x5104]
        st null, pop
        skip eq
        bra fill1
        ret #2

; the user's PSP goes down by a word, and the word is 0: the result of the
; system call
pushz:  ld push, [0xf02c]
        sub tos, #2
        ld push, tos
        st [0xf02c], pop
        sub tos, #0x6000
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, #0
        st [mbp+midx], pop
        ret #0

; the registers of the user into the table, at the process that runs
savectx:
        ld push, [0x5100]
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, [0xf020]
        st [mbp+midx++], pop
        ld push, [0xf022]
        st [mbp+midx++], pop
        ld push, [0xf024]
        st [mbp+midx++], pop
        ld push, [0xf026]
        st [mbp+midx++], pop
        ld push, [0xf028]
        st [mbp+midx++], pop
        ld push, [0xf02a]
        st [mbp+midx++], pop
        ld push, [0xf02c]
        st [mbp+midx++], pop
        ret #0

; the process to run is the other one, if it is alive; if not the same, if
; that is alive; if not, everything is over
sched:  ld push, [0x5100]
        xor tos, #0x20
        st mbp, pop
        ld push, #14
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #0
        st null, pop
        skip ne
        bra other
        ld push, [0x5100]
        st mbp, pop
        ld push, [mbp+midx]
        sub tos, #0
        st null, pop
        skip ne
        bra same
        ld push, #90            ; 'Z'
        st [0xf000], pop
        bra stop
other:  ld push, mbp
        st [0x5100], pop
same:   ret #0

; the registers of the table go to the user context, and its pages to the
; maps; the windows 8 and 9 follow the process
loadctx:
        ld push, [0x5100]
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, [mbp+midx++]
        st [0xf020], pop
        ld push, [mbp+midx++]
        st [0xf022], pop
        ld push, [mbp+midx++]
        st [0xf024], pop
        ld push, [mbp+midx++]
        st [0xf026], pop
        ld push, [mbp+midx++]
        st [0xf028], pop
        ld push, [mbp+midx++]
        st [0xf02a], pop
        ld push, [mbp+midx++]
        st [0xf02c], pop
        ld push, [mbp+midx++]   ; the state: not needed
        st null, pop
        ld push, [mbp+midx++]   ; data page 3
        ld push, tos
        st [0xf1e6], pop
        st [0xf172], pop
        ld push, [mbp+midx++]   ; the stack page
        ld push, tos
        ld push, tos
        st [0xf1fc], pop        ; data page 14
        st [0xf1dc], pop        ; parameter stack page 14
        st [0xf170], pop        ; window 8
        ld push, [mbp+midx++]   ; the return stack page
        st [0xf1ba], pop        ; return stack page 13
        ret #0

; ---- the user code: the stubs of the system calls ----

putc:   trap #1
        st [pfp+0], pop         ; the result replaces the argument
        ret #0
yield:  trap #2
        st null, pop
        ret #0
exit:   trap #3
        bra exit
timer:  trap #4
        st null, pop
        ret #0

; a function with a result in the argument: double(x)
dbl:    ld push, [pfp+0]
        add tos, tos
        st [pfp+0], pop
        ret #0

; ---- process 0 ----
u0:     ld push, #66            ; 'B'
        call putc
        st null, pop
        call yield
        ld push, #0x1234        ; private: process 1 must not see it
        st [0x3002], pop
        ld push, #7
        call dbl                ; a call, the return stack of its own
        sub tos, #14
        st null, pop
        skip eq
        bra bad
        ld push, #68            ; 'D'
        call putc
        st null, pop
        call yield
        call timer
spin:   ld push, [0x4000]       ; the shared page: process 1 sets it, and it
        sub tos, #0             ; can only run if the timer preempts this loop
        st null, pop
        skip ne
        bra spin
        ld push, #72            ; 'H'
        call putc
        st null, pop
        call exit
bad:    ld push, #120           ; 'x'
        call putc
        st null, pop
        call exit

; ---- process 1 ----
u1:     ld push, #67            ; 'C'
        call putc
        st null, pop
        call yield
        ld push, [0x3002]       ; its own page: not what process 0 wrote
        sub tos, #0
        st null, pop
        skip eq
        bra bad
        ld push, #69            ; 'E'
        call putc
        st null, pop
        ld push, [0xf000]       ; the UART is not mapped: a fault, 'F'
        call yield
        ld push, #1
        st [0x4000], pop
        ld push, #71            ; 'G'
        call putc
        st null, pop
        call exit
