; Message passing between two processes, Minix style: send, receive,
; sendrec and notify, with blocking, a message copied between the private
; pages of the processes, a message pointer check and a pending notification.
; Expect ABCDEFGHIJKZ, then silence.
;
; The kernel is the one of hw18: the system context is a coroutine, `reti`
; runs the user context. Now the kernel saves the user context in the process
; table on every trap, works only on the table, and loads the context of the
; process to run at the end. A process that blocks is not resumed; the result
; of its system call is pushed on its parameter stack (through a window) when
; it is unblocked.
;
; Traps: 1 send(dst,m), 2 receive(src,m), 3 sendrec(dst,m), 4 notify(dst),
; 8 putc(c), 9 exit(). Endpoints are the process numbers 0 and 1, ANY is
; 0xffff. The first argument is at [pfp+0], the second at [pfp+2]; the
; result replaces the deepest one, so a call with two arguments returns by
; `ret #1`. A message is 12 words: the source (set by the kernel), the type,
; ten words of data. It must be in the data page 3, the private one, and
; word aligned, or the call returns 0xffff.
;
; Kernel windows (system data pages): 8 (0x8000) the stack page of the
; process whose result or arguments are touched, 9 and 10 the private pages
; of the sender and the receiver of a message.
;
; Process table at 0x5000, 64 bytes each (process 1 at 0x5040): the words IP
; RSP PFP MBP MIDX SW PSP of the user, +14 state (0 ready, 1 dead, 2 receives,
; 3 sends), +16 physical page of data page 3, +18 of the stack page, +20 of
; the return stack page, +22 message pointer, +24 pending notification,
; +26 the source it waits for, +28 the process it sends to (a table pointer),
; +30 1 if it is a sendrec, +32 its endpoint.
; Kernel variables: 0x5100 the process that runs, 0x5102 the other, 0x5104
; D, 0x5106 T, 0x5108 V, 0x510a first argument, 0x510c second argument,
; 0x510e flag, 0x5110 temporary; 0x5200 a 24 byte buffer.

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
        st [0xf1e8], pop        ; data page 4: shared, but not for messages
        ld push, #65            ; 'A'
        st [0xf000], pop
        ld push, #0x5000
        st [0x5100], pop
        call loadctx

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
        ld push, #63            ; '?'
        st [0xf000], pop
stop:   bra stop

resched:
        call sched
        call loadctx
        bra loop

; ---- the system calls ----

; the common start: the context into the table, O, the arguments
entry:  call savectx
        ld push, [0x5100]
        xor tos, #0x40
        st [0x5102], pop
        call getargs
        ret #0

; result 0xffff for the caller
fail:
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #65535
        st [0x5108], pop
        call pushres
        bra resched

h_putc:
        call entry
        ld push, [0x510a]
        st [0xf000], pop
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        call pushres
        bra resched

h_exit:
        ld push, #1
        ld push, [0x5100]
        st mbp, pop
        ld push, #14
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
        call entry
        call chkm
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        call ep2ptr
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
        call filtok
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip ne
        bra sd_block
        ld push, [0x5100]
        st [0x5106], pop
        call copymsg
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
        call pushres
        ld push, [0x510e]
        sub tos, #0
        st null, pop
        skip eq
        bra sd_rec
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        call pushres
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
        call entry
        call chkm
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
        call filtok
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip ne
        bra rc_send
        call putnote
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
        call pushres
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
        call filtok
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip ne
        bra rc_block
        ld push, [0x5100]
        st [0x5104], pop
        ld push, [0x5102]
        st [0x5106], pop
        call copymsg
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
        call pushres
rc_done:
        ld push, [0x5100]
        st [0x5106], pop
        ld push, #0
        st [0x5108], pop
        call pushres
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
        call entry
        call ep2ptr
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
        call filtok
        ld push, [0x5108]
        sub tos, #0
        st null, pop
        skip ne
        bra nt_pend
        ld push, [0x5100]
        st [0x5106], pop
        call putnote
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
        call pushres
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
        call pushres
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

; ---- process 0: the client ----
u0:
        ld push, #66
        call putc
        st null, pop
        ld push, #7
        st [0x3102], pop
        ld push, #21
        st [0x3104], pop
        ld push, #0x3100
        ld push, #1
        call sendrec
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
        sub tos, #8
        st null, pop
        skip eq
        bra bad0
        ld push, [0x3104]
        sub tos, #42
        st null, pop
        skip eq
        bra bad0
        ld push, #70
        call putc
        st null, pop
        ld push, #0x3100
        ld push, #65535
        call receive
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
        sub tos, #16384
        st null, pop
        skip eq
        bra bad0
        ld push, #71
        call putc
        st null, pop
        ld push, #0x4000
        ld push, #1
        call send
        sub tos, #65535
        st null, pop
        skip eq
        bra bad0
        ld push, #72
        call putc
        st null, pop
        ld push, #9
        st [0x3102], pop
        ld push, #0x3100
        ld push, #1
        call send
        sub tos, #0
        st null, pop
        skip eq
        bra bad0
        ld push, #73
        call putc
        st null, pop
        ld push, #0x3100
        ld push, #1
        call receive
        sub tos, #0
        st null, pop
        skip eq
        bra bad0
        ld push, [0x3102]
        sub tos, #10
        st null, pop
        skip eq
        bra bad0
        ld push, #75
        call putc
        st null, pop
        call exit
bad0:  ld push, #120           ; 'x'
        call putc
        st null, pop
        call exit

; ---- process 1: the server ----
u1:
        ld push, #67
        call putc
        st null, pop
        ld push, #0x3100
        ld push, #65535
        call receive
        sub tos, #0
        st null, pop
        skip eq
        bra bad1
        ld push, [0x3100]
        sub tos, #0
        st null, pop
        skip eq
        bra bad1
        ld push, [0x3102]
        sub tos, #7
        st null, pop
        skip eq
        bra bad1
        ld push, [0x3104]
        add tos, tos
        st [0x3104], pop
        ld push, #8
        st [0x3102], pop
        ld push, #68
        call putc
        st null, pop
        ld push, #0x3100
        ld push, #0
        call send
        sub tos, #0
        st null, pop
        skip eq
        bra bad1
        ld push, #69
        call putc
        st null, pop
        ld push, #0
        call notify
        st null, pop
        ld push, #0x3100
        ld push, #65535
        call receive
        sub tos, #0
        st null, pop
        skip eq
        bra bad1
        ld push, [0x3100]
        sub tos, #0
        st null, pop
        skip eq
        bra bad1
        ld push, [0x3102]
        sub tos, #9
        st null, pop
        skip eq
        bra bad1
        ld push, #74
        call putc
        st null, pop
        ld push, #10
        st [0x3102], pop
        ld push, #0x3100
        ld push, #0
        call send
        sub tos, #0
        st null, pop
        skip eq
        bra bad1
        call exit
bad1:  ld push, #121           ; 'y'
        call putc
        st null, pop
        call exit
