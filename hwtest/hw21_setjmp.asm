; setjmp and longjmp as system calls. Expect ABCDEFGHZ, then silence.
;
; The kernel is the one of hw19 (two processes, a process table, the context of
; the process saved on a trap), without the message passing. A frame of the
; program is the parameter stack (plain memory) plus the return stack, where
; `call` leaves the return address at [rsp] and the PFP of the caller at
; [rsp+2]. Trap 32, setjmp(buf), is made by its stub, so, from the context,
; the kernel writes into the buffer (four words, in the private data page):
;   0 the return address, read at [rsp] of the return stack (through a window
;     on its physical page), 1 the PFP of the caller, [rsp+2],
;   2 the RSP after the return of setjmp, rsp+4, 3 the PSP after it, which is
;     the PFP of the stub (the result takes the place of the argument).
; Trap 33, longjmp(buf, v), makes the context of the process (IP, PFP, RSP, PSP)
; from the four words and stores v (1 if it is 0) at [PSP], where the result of
; the old setjmp is. The process continues after its `call setjmp`. It is
; refused, with the result 0xffff, if the buffer is not in the private page or
; the frame of setjmp is dead: its RSP must not be below that of the caller of
; longjmp. Windows: 8 the stack page, 9 the private page, 11 the return stack
; page of the process.
;
; Kernel variables as in hw19, and 0x5120..0x5126 the four words of the buffer.

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
        sub tos, #0xa0
        st null, pop
        skip ne
        bra h_setjmp
        ld push, [0xf02e]
        sub tos, #0xa1
        st null, pop
        skip ne
        bra h_longjmp
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

h_setjmp:
        calla entry
        ld push, [0x510a]
        st [0x510c], pop
        calla chkm
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, [0x5100]
        st mbp, pop
        ld push, #20
        st midx, pop
        ld push, [mbp+midx]
        st [0xf176], pop        ; window 11
        ld push, [0x5100]
        st mbp, pop
        ld push, #2
        st midx, pop
        ld push, [mbp+midx]
        and tos, #0x0fff
        or tos, #0xb000
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, [mbp+midx++]
        st [0x5120], pop
        ld push, [mbp+midx++]
        st [0x5122], pop
        ld push, [0x5100]
        st mbp, pop
        ld push, #2
        st midx, pop
        ld push, [mbp+midx]
        add tos, #4
        st [0x5124], pop
        ld push, [0x5100]
        st mbp, pop
        ld push, #4
        st midx, pop
        ld push, [mbp+midx]
        st [0x5126], pop
        ld push, [0x5100]
        st mbp, pop
        ld push, #16
        st midx, pop
        ld push, [mbp+midx]
        st [0xf172], pop        ; window 9
        ld push, [0x510a]
        and tos, #0x0fff
        or tos, #0x9000
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, [0x5120]
        st [mbp+midx++], pop
        ld push, [0x5122]
        st [mbp+midx++], pop
        ld push, [0x5124]
        st [mbp+midx++], pop
        ld push, [0x5126]
        st [mbp+midx++], pop
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        calla pushres
        bra resched

h_longjmp:
        calla entry
        ld push, [0x510c]
        st [0x510e], pop
        ld push, [0x510a]
        st [0x510c], pop
        calla chkm
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        ld push, [0x5100]
        st mbp, pop
        ld push, #16
        st midx, pop
        ld push, [mbp+midx]
        st [0xf172], pop
        ld push, [0x510a]
        and tos, #0x0fff
        or tos, #0x9000
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, [mbp+midx++]
        st [0x5120], pop
        ld push, [mbp+midx++]
        st [0x5122], pop
        ld push, [mbp+midx++]
        st [0x5124], pop
        ld push, [mbp+midx++]
        st [0x5126], pop
        ld push, [0x5100]
        st mbp, pop
        ld push, #2
        st midx, pop
        ld push, [mbp+midx]
        add tos, #4
        st [0x5110], pop
        ld push, [0x5124]
        sub tos, [0x5110]
        st null, pop
        skip cs
        bra fail
        ld push, [0x5120]
        ld push, [0x5100]
        st mbp, pop
        ld push, #0
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x5122]
        ld push, [0x5100]
        st mbp, pop
        ld push, #4
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x5124]
        ld push, [0x5100]
        st mbp, pop
        ld push, #2
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x5126]
        ld push, [0x5100]
        st mbp, pop
        ld push, #12
        st midx, pop
        st [mbp+midx], pop
        ld push, [0x510e]
        sub tos, #0
        st null, pop
        skip eq
        bra lj_v
        ld push, #1
        st [0x510e], pop
lj_v:
        ld push, [0x5100]
        st mbp, pop
        ld push, #18
        st midx, pop
        ld push, [mbp+midx]
        st [0xf170], pop
        ld push, [0x5126]
        and tos, #0x0fff
        or tos, #0x8000
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, [0x510e]
        st [mbp+midx], pop
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

; the process to run: the same if it is ready, the other if it is ready, else
; all is over
sched:
        ld push, [0x5100]
        st mbp, pop
        ld push, #14
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #0
        st null, pop
        skip ne
        bra sched_ret
        ld push, [0x5100]
        xor tos, #0x40
        st [0x5102], pop
        ld push, [0x5102]
        st mbp, pop
        ld push, #14
        st midx, pop
        ld push, [mbp+midx]
        sub tos, #0
        st null, pop
        skip eq
        bra sched_dead
        ld push, [0x5102]
        st [0x5100], pop
sched_ret:
        ret #0
sched_dead:
        ld push, #90            ; 'Z'
        st [0xf000], pop
        bra stop

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

setjmp: trap #32
        st [pfp+0], pop
        ret #0
longjmp:
        trap #33
        st [pfp+2], pop
        ret #1
putc:   trap #8
        st [pfp+0], pop
        ret #0
exit:   trap #9
        bra exit

; ---- process 0 ----
u0:
        ld push, #66
        calla putc
        st null, pop
        ld push, #77
        ld push, #0x3200
        calla setjmp
        ld push, tos
        sub tos, #0
        st null, pop
        skip ne
        bra first1
        sub tos, #5
        st null, pop
        skip eq
        bra bad0
        sub tos, #77
        st null, pop
        skip eq
        bra bad0
        ld push, #68
        calla putc
        st null, pop
        bra done1
first1:
        st null, pop
        ld push, #67
        calla putc
        st null, pop
        calla g1
        bra bad0
done1:
        ld push, #88
        ld push, #0x3210
        calla setjmp
        ld push, tos
        sub tos, #0
        st null, pop
        skip ne
        bra first2
        sub tos, #1
        st null, pop
        skip eq
        bra bad0
        sub tos, #88
        st null, pop
        skip eq
        bra bad0
        ld push, #70
        calla putc
        st null, pop
        bra done2
first2:
        st null, pop
        ld push, #69
        calla putc
        st null, pop
        calla g2
        bra bad0
done2:
        calla mk
        ld push, #1
        ld push, #0x3220
        calla longjmp
        sub tos, #65535
        st null, pop
        skip eq
        bra bad0
        ld push, #71
        calla putc
        st null, pop
        ld push, #0x4000
        calla setjmp
        sub tos, #65535
        st null, pop
        skip eq
        bra bad0
        ld push, #72
        calla putc
        st null, pop
        calla exit
g1:    ld push, #5
        ld push, #0x3200
        calla longjmp
        bra bad0
g2:    ld push, #0
        ld push, #0x3210
        calla longjmp
        bra bad0
mk:    ld push, #0x3220
        calla setjmp
        st null, pop
        ret #0
bad0:  ld push, #120           ; 'x'
        calla putc
        st null, pop
        calla exit

; ---- process 1 ----
u1:
        calla exit