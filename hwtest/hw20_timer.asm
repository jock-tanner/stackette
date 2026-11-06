; The timer interrupt as a notification, with an idle process. Expect
; ACBTTTSRZ, then silence.
;
; The kernel is the one of hw19 (message passing between processes 0 and 1),
; with a third process, the idle one (`bra .`), that runs only when nothing
; else is ready: an interrupt can be taken only in the user context, so, with
; all the processes blocked, something must run for it to reach the kernel.
; The interrupt of the timer is turned into a notification of process 1, the
; driver, from the pseudo process HARDWARE (the table entry at 0x50c0 that has
; only the endpoint 0xfffe): delivered at once if the driver waits for a
; message from it, else remembered (+34), and delivered by its next receive.
; Trap 16 starts the timer with the period in the first argument, or stops it
; if that is 0. The kernel masks the line when it takes the interrupt, as the
; kernel needs more than a period to come back to the user, and trap 17 opens
; it again, after the driver has dealt with the tick (it clears the flag too).
;
; Table: process 0 at 0x5000, 1 at 0x5040, idle at 0x5080, HARDWARE at 0x50c0.
; A process that runs is kept if it is ready (and not the idle one), else the
; first ready one of 0 and 1 runs, else the idle one, unless both are dead.
; The rest of the table and the calls are as in hw19; +34 is the pending
; interrupt.

start:
        ld push, #u0
        st [0x5000], pop
        ld push, #55296
        st [0x5002], pop
        ld push, #59392
        st [0x5004], pop
        ld push, #0
        st [0x5006], pop
        ld push, #0
        st [0x5008], pop
        ld push, #0
        st [0x500a], pop
        ld push, #59392
        st [0x500c], pop
        ld push, #0
        st [0x500e], pop
        ld push, #16
        st [0x5010], pop
        ld push, #20
        st [0x5012], pop
        ld push, #22
        st [0x5014], pop
        ld push, #0
        st [0x5016], pop
        ld push, #0
        st [0x5018], pop
        ld push, #0
        st [0x501a], pop
        ld push, #0
        st [0x501c], pop
        ld push, #0
        st [0x501e], pop
        ld push, #0
        st [0x5020], pop
        ld push, #0
        st [0x5022], pop
        ld push, #u1
        st [0x5040], pop
        ld push, #55296
        st [0x5042], pop
        ld push, #59392
        st [0x5044], pop
        ld push, #0
        st [0x5046], pop
        ld push, #0
        st [0x5048], pop
        ld push, #0
        st [0x504a], pop
        ld push, #59392
        st [0x504c], pop
        ld push, #0
        st [0x504e], pop
        ld push, #17
        st [0x5050], pop
        ld push, #21
        st [0x5052], pop
        ld push, #23
        st [0x5054], pop
        ld push, #0
        st [0x5056], pop
        ld push, #0
        st [0x5058], pop
        ld push, #0
        st [0x505a], pop
        ld push, #0
        st [0x505c], pop
        ld push, #0
        st [0x505e], pop
        ld push, #1
        st [0x5060], pop
        ld push, #0
        st [0x5062], pop
        ld push, #idle
        st [0x5080], pop
        ld push, #55296
        st [0x5082], pop
        ld push, #59392
        st [0x5084], pop
        ld push, #0
        st [0x5086], pop
        ld push, #0
        st [0x5088], pop
        ld push, #0
        st [0x508a], pop
        ld push, #59392
        st [0x508c], pop
        ld push, #0
        st [0x508e], pop
        ld push, #24
        st [0x5090], pop
        ld push, #25
        st [0x5092], pop
        ld push, #26
        st [0x5094], pop
        ld push, #0
        st [0x5096], pop
        ld push, #0
        st [0x5098], pop
        ld push, #0
        st [0x509a], pop
        ld push, #0
        st [0x509c], pop
        ld push, #0
        st [0x509e], pop
        ld push, #2
        st [0x50a0], pop
        ld push, #0
        st [0x50a2], pop
        ld push, #0xfffe        ; HARDWARE
        st [0x50e0], pop
        ; the user maps: no memory, except what a process may use
        ld push, #0xf182        ; code pages 1..15
        ld push, #15
        calla fill
        ld push, #0xf1a0        ; return stack pages 0..15
        ld push, #16
        calla fill
        ld push, #0xf1c0        ; parameter stack pages 0..15
        ld push, #16
        calla fill
        ld push, #0xf1e2        ; data pages 1..15
        ld push, #15
        calla fill
        ld push, #0x12
        st [0xf1e8], pop        ; data page 4: shared, but not for messages
        ld push, #65            ; 'A'
        st [0xf000], pop
        ld push, #0x5000
        st [0x5100], pop
        calla loadctx

loop:   reti
        ld push, [0xf02e]
        sub tos, #0x81
        st null, pop
        skip ne
        bra h_send
        ld push, [0xf02e]
        sub tos, #0x82
        st null, pop
        skip ne
        bra h_recv
        ld push, [0xf02e]
        sub tos, #0x83
        st null, pop
        skip ne
        bra h_sendrec
        ld push, [0xf02e]
        sub tos, #0x84
        st null, pop
        skip ne
        bra h_notify
        ld push, [0xf02e]
        sub tos, #0x88
        st null, pop
        skip ne
        bra h_putc
        ld push, [0xf02e]
        sub tos, #0x89
        st null, pop
        skip ne
        bra h_exit
        ld push, [0xf02e]
        sub tos, #0x90
        st null, pop
        skip ne
        bra h_timer
        ld push, [0xf02e]
        sub tos, #0x91
        st null, pop
        skip ne
        bra h_irqen
        ld push, [0xf02e]
        sub tos, #0x40
        st null, pop
        skip ne
        bra h_tick
        ld push, #63            ; '?'
        st [0xf000], pop
stop:   bra stop

resched:
        calla sched
        calla loadctx
        bra loop

; ---- the system calls ----

; the common start: the context into the table, O, the arguments
entry:  call savectx
        ld push, [0x5100]
        xor tos, #0x40
        st [0x5102], pop
        calla getargs
        ret #0

; result 0xffff for the caller
fail:
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #65535
        st [0x5108], pop
        calla pushres
        bra resched

h_putc:
        calla entry
        ld push, [0x510a]
        st [0xf000], pop
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        calla pushres
        bra resched

h_exit:
        ld push, #1
        ld push, [0x5100]
        st mbp, pop
        ld push, #14
        st midx, pop
        st [mbp+midx], pop
        bra resched

h_timer:
        calla entry
        ld push, #1
        st [0xf016], pop
        ld push, [0x510a]
        sub tos, #0
        st null, pop
        skip ne
        bra tm_stop
        ld push, #0
        st [0xf010], pop
        ld push, [0x510a]
        st [0xf012], pop
        ld push, #3
        st [0xf014], pop
        ld push, #1
        st [0xf00a], pop
        bra tm_done
tm_stop:
        ld push, #0
        st [0xf00a], pop
        ld push, #0
        st [0xf014], pop
tm_done:
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        calla pushres
        bra resched

h_irqen:
        calla entry
        ld push, #1
        st [0xf016], pop
        ld push, #1
        st [0xf00a], pop
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        calla pushres
        bra resched

h_tick:
        calla savectx
        ld push, #1
        st [0xf016], pop
        ld push, #0
        st [0xf00a], pop
        ld push, #20544
        st [0x5104], pop
        ld push, #20672
        st [0x5106], pop
        ld push, [0x5104]
        st mbp, pop
        ld push, #14
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #2
        st null, pop
        skip eq
        bra tk_pend
        calla filtok
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip ne
        bra tk_pend
        calla putnote
        ld push, #0
        ld push, [0x5104]
        st mbp, pop
        ld push, #14
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x5104]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        calla pushres
        bra resched
tk_pend:
        ld push, #1
        ld push, [0x5104]
        st mbp, pop
        ld push, #34
        st midx, pop
        st [mbp+midx], pop
        bra resched

h_send:
        ld push, #0
        st [0x510e], pop
        bra dosend
h_sendrec:
        ld push, #1
        st [0x510e], pop
dosend:
        calla entry
        calla chkm
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        calla ep2ptr
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, [0x510c]
        ld push, [0x5100]
        st mbp, pop
        ld push, #22
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x5104]
        st mbp, pop
        ld push, #14
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #2
        st null, pop
        skip eq
        bra sd_block
        ld push, [0x5100]
        st [0x5106], pop
        calla filtok
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip ne
        bra sd_block
        ld push, [0x5100]
        st [0x5106], pop
        calla copymsg
        ld push, #0
        ld push, [0x5104]
        st mbp, pop
        ld push, #14
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x5104]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        calla pushres
        ld push, [0x510e]
        sub tos, #0
        st null, pop
        skip eq
        bra sd_rec
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        calla pushres
        bra resched
sd_rec:
        ld push, [0x5104]
        st mbp, pop
        ld push, #32
        st midx, pop
        ld push, [mbp+midx]
        ld push, [0x5100]
        st mbp, pop
        ld push, #26
        st midx, pop
        st [mbp+midx], pop
        ld push, #2
        ld push, [0x5100]
        st mbp, pop
        ld push, #14
        st midx, pop
        st [mbp+midx], pop
        bra resched
sd_block:
        ld push, [0x5104]
        ld push, [0x5100]
        st mbp, pop
        ld push, #28
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x510e]
        ld push, [0x5100]
        st mbp, pop
        ld push, #30
        st midx, pop
        st [mbp+midx], pop
        ld push, #3
        ld push, [0x5100]
        st mbp, pop
        ld push, #14
        st midx, pop
        st [mbp+midx], pop
        bra resched

h_recv:
        calla entry
        calla chkm
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, [0x510c]
        ld push, [0x5100]
        st mbp, pop
        ld push, #22
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x510a]
        ld push, [0x5100]
        st mbp, pop
        ld push, #26
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x5100]
        st mbp, pop
        ld push, #34
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #0
        st null, pop
        skip ne
        bra rc_pend1
        ld push, [0x5100]
        st [0x5104], pop
        ld push, #20672
        st [0x5106], pop
        calla filtok
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip ne
        bra rc_pend1
        calla putnote
        ld push, #0
        ld push, [0x5100]
        st mbp, pop
        ld push, #34
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        calla pushres
        bra resched
rc_pend1:
        ld push, [0x5100]
        st mbp, pop
        ld push, #24
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #0
        st null, pop
        skip ne
        bra rc_send
        ld push, [0x5100]
        st [0x5104], pop
        ld push, [0x5102]
        st [0x5106], pop
        calla filtok
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip ne
        bra rc_send
        calla putnote
        ld push, #0
        ld push, [0x5100]
        st mbp, pop
        ld push, #24
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        calla pushres
        bra resched
rc_send:
        ld push, [0x5102]
        st mbp, pop
        ld push, #14
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #3
        st null, pop
        skip eq
        bra rc_block
        ld push, [0x5102]
        st mbp, pop
        ld push, #28
        st midx, pop
        ld push, [mbp+midx]
        sub tos, [0x5100]
        st null, pop
        skip eq
        bra rc_block
        ld push, [0x5100]
        st [0x5104], pop
        ld push, [0x5102]
        st [0x5106], pop
        calla filtok
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip ne
        bra rc_block
        ld push, [0x5100]
        st [0x5104], pop
        ld push, [0x5102]
        st [0x5106], pop
        calla copymsg
        ld push, [0x5102]
        st mbp, pop
        ld push, #30
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #0
        st null, pop
        skip ne
        bra rc_plain
        ld push, #2
        ld push, [0x5102]
        st mbp, pop
        ld push, #14
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x5100]
        st mbp, pop
        ld push, #32
        st midx, pop
        ld push, [mbp+midx]
        ld push, [0x5102]
        st mbp, pop
        ld push, #26
        st midx, pop
        st [mbp+midx], pop
        bra rc_done
rc_plain:
        ld push, #0
        ld push, [0x5102]
        st mbp, pop
        ld push, #14
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x5102]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        calla pushres
rc_done:
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        calla pushres
        bra resched
rc_block:
        ld push, #2
        ld push, [0x5100]
        st mbp, pop
        ld push, #14
        st midx, pop
        st [mbp+midx], pop
        bra resched

h_notify:
        calla entry
        calla ep2ptr
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, [0x5104]
        st mbp, pop
        ld push, #14
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #2
        st null, pop
        skip eq
        bra nt_pend
        ld push, [0x5100]
        st [0x5106], pop
        calla filtok
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip ne
        bra nt_pend
        ld push, [0x5100]
        st [0x5106], pop
        calla putnote
        ld push, #0
        ld push, [0x5104]
        st mbp, pop
        ld push, #14
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x5104]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        calla pushres
        bra nt_done
nt_pend:
        ld push, #1
        ld push, [0x5104]
        st mbp, pop
        ld push, #24
        st midx, pop
        st [mbp+midx], pop
nt_done:
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        calla pushres
        bra resched

; ---- the subroutines of the kernel ----

; fill(first register, count): the registers get the page that has no memory
fill:   ld push, [pfp+2]
        st mbp, pop
        ld push, [pfp+0]
        add tos, tos
        st [0x5112], pop        ; the count of bytes
        ld push, #0
        st midx, pop
fill1:  ld push, #0x3000
        st [mbp+midx++], pop
        ld push, midx
        sub tos, [0x5112]
        st null, pop
        skip eq
        bra fill1
        ret #2

; the arguments of the process that runs: A0 = [pfp+0], A1 = [pfp+2]
getargs:
        ld push, [0x5100]
        st mbp, pop
        ld push, #18
        st midx, pop
        ld push, [mbp+midx]
        st [0xf170], pop        ; window 8: its stack page
        ld push, [0x5100]
        st mbp, pop
        ld push, #4
        st midx, pop
        ld push, [mbp+midx]
        and tos, #0x0fff
        or tos, #0x8000
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, [mbp+midx++]
        st [0x510a], pop
        ld push, [mbp+midx++]
        st [0x510c], pop
        ret #0

; the result V is pushed on the parameter stack of the process T
pushres:
        ld push, [0x5106]
        st mbp, pop
        ld push, #18
        st midx, pop
        ld push, [mbp+midx]
        st [0xf170], pop
        ld push, [0x5106]
        st mbp, pop
        ld push, #12
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #2
        ld push, tos
        ld push, [0x5106]
        st mbp, pop
        ld push, #12
        st midx, pop
        st [mbp+midx], pop
        and tos, #0x0fff
        or tos, #0x8000
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, [0x5108]
        st [mbp+midx], pop
        ret #0

; V = 0 if the message pointer A1 is in the private page, aligned and the
; whole message fits it, 0xffff if not
chkm:
        ld push, #65535
        st [0x5108], pop
        ld push, [0x510c]
        and tos, #0xf000
        sub tos, #0x3000
        st null, pop
        skip eq
        bra chkm_end
        ld push, [0x510c]
        and tos, #1
        sub tos, #0
        st null, pop
        skip eq
        bra chkm_end
        ld push, [0x510c]
        and tos, #0x0fff
        sub tos, #0x0fe9
        st null, pop
        skip cc
        bra chkm_end
        ld push, #0
        st [0x5108], pop
chkm_end:
        ret #0

; D = the process of the endpoint A0, V = 0 if it exists and is not the one
; that runs, 0xffff if not
ep2ptr:
        ld push, #65535
        st [0x5108], pop
        ld push, [0x510a]
        and tos, #0xfffe
        sub tos, #0
        st null, pop
        skip eq
        bra ep_end
        ld push, [0x510a]
        add tos, tos
        add tos, tos
        add tos, tos
        add tos, tos
        add tos, tos
        add tos, tos
        add tos, #0x5000
        st [0x5104], pop
        ld push, [0x5104]
        sub tos, [0x5100]
        st null, pop
        skip ne
        bra ep_end
        ld push, #0
        st [0x5108], pop
ep_end:
        ret #0

; V = 1 if the process D waits for a message from the process T: it waits
; for any source or for the endpoint of T, 0 if not
filtok:
        ld push, [0x5106]
        st mbp, pop
        ld push, #32
        st midx, pop
        ld push, [mbp+midx]
        st [0x5110], pop
        ld push, [0x5104]
        st mbp, pop
        ld push, #26
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #0xffff
        st null, pop
        skip ne
        bra fok
        ld push, [0x5104]
        st mbp, pop
        ld push, #26
        st midx, pop
        ld push, [mbp+midx]
        sub tos, [0x5110]
        st null, pop
        skip ne
        bra fok
        ld push, #0
        st [0x5108], pop
        ret #0
fok:
        ld push, #1
        st [0x5108], pop
        ret #0

; the message of the process T is copied to the buffer of the process D, with
; the endpoint of T as its source
copymsg:
        ld push, [0x5106]
        st mbp, pop
        ld push, #16
        st midx, pop
        ld push, [mbp+midx]
        st [0xf172], pop        ; window 9
        ld push, [0x5104]
        st mbp, pop
        ld push, #16
        st midx, pop
        ld push, [mbp+midx]
        st [0xf174], pop        ; window 10
        ld push, [0x5106]
        st mbp, pop
        ld push, #22
        st midx, pop
        ld push, [mbp+midx]
        and tos, #0x0fff
        or tos, #0x9000
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, [mbp+midx++]
        st [0x5200], pop
        ld push, [mbp+midx++]
        st [0x5202], pop
        ld push, [mbp+midx++]
        st [0x5204], pop
        ld push, [mbp+midx++]
        st [0x5206], pop
        ld push, [mbp+midx++]
        st [0x5208], pop
        ld push, [mbp+midx++]
        st [0x520a], pop
        ld push, [mbp+midx++]
        st [0x520c], pop
        ld push, [mbp+midx++]
        st [0x520e], pop
        ld push, [mbp+midx++]
        st [0x5210], pop
        ld push, [mbp+midx++]
        st [0x5212], pop
        ld push, [mbp+midx++]
        st [0x5214], pop
        ld push, [mbp+midx++]
        st [0x5216], pop
        ld push, [0x5106]
        st mbp, pop
        ld push, #32
        st midx, pop
        ld push, [mbp+midx]
        st [0x5200], pop
        ld push, [0x5104]
        st mbp, pop
        ld push, #22
        st midx, pop
        ld push, [mbp+midx]
        and tos, #0x0fff
        or tos, #0xa000
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, [0x5200]
        st [mbp+midx++], pop
        ld push, [0x5202]
        st [mbp+midx++], pop
        ld push, [0x5204]
        st [mbp+midx++], pop
        ld push, [0x5206]
        st [mbp+midx++], pop
        ld push, [0x5208]
        st [mbp+midx++], pop
        ld push, [0x520a]
        st [mbp+midx++], pop
        ld push, [0x520c]
        st [mbp+midx++], pop
        ld push, [0x520e]
        st [mbp+midx++], pop
        ld push, [0x5210]
        st [mbp+midx++], pop
        ld push, [0x5212]
        st [mbp+midx++], pop
        ld push, [0x5214]
        st [mbp+midx++], pop
        ld push, [0x5216]
        st [mbp+midx++], pop
        ret #0

; the notification of T arrives in the buffer of the process D
putnote:
        ld push, [0x5106]
        st mbp, pop
        ld push, #32
        st midx, pop
        ld push, [mbp+midx]
        st [0x5110], pop
        ld push, [0x5104]
        st mbp, pop
        ld push, #16
        st midx, pop
        ld push, [mbp+midx]
        st [0xf174], pop
        ld push, [0x5104]
        st mbp, pop
        ld push, #22
        st midx, pop
        ld push, [mbp+midx]
        and tos, #0x0fff
        or tos, #0xa000
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, [0x5110]
        st [mbp+midx++], pop
        ld push, #0x4000
        st [mbp+midx++], pop
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

; the process to run: the same if it is a ready one but the idle one, else
; the first ready of 0 and 1, else the idle one, unless both are dead
sched:
        ld push, [0x5100]
        sub tos, #0x5080
        st null, pop
        skip ne
        bra sch_scan
        ld push, [0x5100]
        st mbp, pop
        ld push, #14
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #0
        st null, pop
        skip ne
        bra sched_ret
sch_scan:
        ld push, #0x5000
        st mbp, pop
        ld push, #14
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #0
        st null, pop
        skip ne
        bra pick0
        ld push, #0x5040
        st mbp, pop
        ld push, #14
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #0
        st null, pop
        skip ne
        bra pick1
        ld push, #0x5000
        st mbp, pop
        ld push, #14
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #1
        st null, pop
        skip eq
        bra pickidle
        ld push, #0x5040
        st mbp, pop
        ld push, #14
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #1
        st null, pop
        skip eq
        bra pickidle
        ld push, #90            ; 'Z'
        st [0xf000], pop
        jmp stop
pick0:  ld push, #0x5000
        st [0x5100], pop
        ret #0
pick1:  ld push, #0x5040
        st [0x5100], pop
        ret #0
pickidle:
        ld push, #0x5080
        st [0x5100], pop
sched_ret:
        ret #0

; the registers of the table go to the user context, its pages to the maps
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
        st [0xf1e6], pop
        ld push, [mbp+midx++]   ; the stack page
        ld push, tos
        st [0xf1fc], pop        ; data page 14
        st [0xf1dc], pop        ; parameter stack page 14
        ld push, [mbp+midx++]   ; the return stack page
        st [0xf1ba], pop        ; return stack page 13
        ret #0

; ---- the user code: the stubs of the system calls ----

send:   trap #1
        st [pfp+2], pop
        ret #1
receive:
        trap #2
        st [pfp+2], pop
        ret #1
sendrec:
        trap #3
        st [pfp+2], pop
        ret #1
notify: trap #4
        st [pfp+0], pop
        ret #0
putc:   trap #8
        st [pfp+0], pop
        ret #0
exit:   trap #9
        bra exit
timer:  trap #16
        st [pfp+0], pop
        ret #0
irqen:  trap #17
        st null, pop
        ret #0
idle:   bra idle

; ---- process 0: waits for a message ----
u0:
        ld push, #67
        calla putc
        st null, pop
        ld push, #0x3100
        ld push, #65535
        calla receive
        sub tos, #0
        st null, pop
        skip eq
        bra bad0
        ld push, [0x3100]
        sub tos, #1
        st null, pop
        skip eq
        bra bad0
        ld push, [0x3102]
        sub tos, #5
        st null, pop
        skip eq
        bra bad0
        ld push, #82
        calla putc
        st null, pop
        calla exit
bad0:  ld push, #120           ; 'x'
        calla putc
        st null, pop
        calla exit

; ---- process 1: the driver of the timer ----
u1:
        ld push, #66
        calla putc
        st null, pop
        ld push, #0x400
        calla timer
        st null, pop
        ld push, #0x3100
        ld push, #65535
        calla receive
        sub tos, #0
        st null, pop
        skip eq
        bra bad1
        ld push, [0x3100]
        sub tos, #65534
        st null, pop
        skip eq
        bra bad1
        ld push, [0x3102]
        sub tos, #16384
        st null, pop
        skip eq
        bra bad1
        ld push, #84
        calla putc
        st null, pop
        calla irqen
        st null, pop
        ld push, #0x3100
        ld push, #65535
        calla receive
        sub tos, #0
        st null, pop
        skip eq
        bra bad1
        ld push, [0x3100]
        sub tos, #65534
        st null, pop
        skip eq
        bra bad1
        ld push, [0x3102]
        sub tos, #16384
        st null, pop
        skip eq
        bra bad1
        ld push, #84
        calla putc
        st null, pop
        calla irqen
        st null, pop
        ld push, #0x3100
        ld push, #65535
        calla receive
        sub tos, #0
        st null, pop
        skip eq
        bra bad1
        ld push, [0x3100]
        sub tos, #65534
        st null, pop
        skip eq
        bra bad1
        ld push, [0x3102]
        sub tos, #16384
        st null, pop
        skip eq
        bra bad1
        ld push, #84
        calla putc
        st null, pop
        ld push, #0
        calla timer
        st null, pop
        ld push, #5
        st [0x3102], pop
        ld push, #0x3100
        ld push, #0
        calla send
        sub tos, #0
        st null, pop
        skip eq
        bra bad1
        ld push, #83
        calla putc
        st null, pop
        calla exit
bad1:  ld push, #121           ; 'y'
        calla putc
        st null, pop
        calla exit
