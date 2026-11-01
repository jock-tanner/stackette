; The operand field of the GENERAL format: ld.bs (the sign-extending byte
; load) from [abs], [tos] and [sp+k], [sp+k] up to k = 31, the e bit that a
; swap ignores, and the encodings that must be illegal. Expect ABCDEFGHIJ,
; then silence. A check that fails stops the program with its letter missing.
; (RAM at 0x5000..; the counter of the illegal instructions is at 0x5100)

start:  ld push, [0xf02e]       ; interrupt ID, 0 after power-up
        sub tos, #0xc0          ; an illegal instruction
        st null, pop
        skip ne
        bra illegal
        ld push, #0
        st [0x5100], pop

; A: ld.bs from [abs]: the byte f0 gives 0xfff0, the byte 12 gives 0x0012,
; ld.b gives 0x00f0
        ld push, #0x12f0
        st [0x5000], pop
        ld.bs push, [0x5000]
        sub tos, #0xfff0
        st null, pop
        skip eq
        bra fail
        ld.bs push, [0x5001]
        sub tos, #0x12
        st null, pop
        skip eq
        bra fail
        ld.b push, [0x5000]
        sub tos, #0xf0
        st null, pop
        skip eq
        bra fail
        ld push, #65            ; 'A'
        st [0xf000], pop

; B: ld.bs from [tos], an odd address too
        ld push, #0x5000
        ld.bs tos, [tos]
        sub tos, #0xfff0
        st null, pop
        skip eq
        bra fail
        ld push, #0x5001
        ld.bs tos, [tos]
        sub tos, #0x12
        st null, pop
        skip eq
        bra fail
        ld push, #66            ; 'B'
        st [0xf000], pop

; C: ld.bs from [sp+4], the fifth item of the stack (in memory)
        ld push, #0x12f0
        ld push, #0
        ld push, #0
        ld push, #0
        ld push, #0
        ld.bs push, [sp+4]
        sub tos, #0xfff0
        st null, pop
        skip eq
        bra fail
        ld.b push, [sp+4]
        sub tos, #0xf0
        st null, pop
        skip eq
        bra fail
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        ld push, #67            ; 'C'
        st [0xf000], pop

; D: [sp+30] and [sp+31] on a stack of 32 items, the deepest holds 0x100
        ld push, #256
        ld push, #257
        ld push, #258
        ld push, #259
        ld push, #260
        ld push, #261
        ld push, #262
        ld push, #263
        ld push, #264
        ld push, #265
        ld push, #266
        ld push, #267
        ld push, #268
        ld push, #269
        ld push, #270
        ld push, #271
        ld push, #272
        ld push, #273
        ld push, #274
        ld push, #275
        ld push, #276
        ld push, #277
        ld push, #278
        ld push, #279
        ld push, #280
        ld push, #281
        ld push, #282
        ld push, #283
        ld push, #284
        ld push, #285
        ld push, #286
        ld push, #287
        ld push, [sp+31]
        sub tos, #0x100
        st null, pop
        skip eq
        bra fail
        ld push, [sp+30]
        sub tos, #0x101
        st null, pop
        skip eq
        bra fail
        ld push, [sp+4]
        sub tos, #0x11b
        st null, pop
        skip eq
        bra fail
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        st null, pop
        ld push, sp
        sub tos, #0xf000
        st null, pop
        skip eq
        bra fail
        ld push, #68            ; 'D'
        st [0xf000], pop

; E: the e bit of a swap is ignored: swap.b tos, [0x5000] with e = 1 still
; zero-extends. The memory holds 0x12f0
        ld push, #0x77
        .word 0xa8a1, 0x5000
        sub tos, #0xf0
        st null, pop
        skip eq
        bra fail
        ld push, [0x5000]
        sub tos, #0x1277
        st null, pop
        skip eq
        bra fail
        ld push, #69            ; 'E'
        st [0xf000], pop

; F..J: encodings that are not instructions; each is a fault, the system
; context resets, and the counter tells which one is next
i1:     .word 0xa043            ; ld [sp+1] with e: not a stack item with e
        bra fail
i2:     .word 0xa018            ; 001 1xxx
        bra fail
i3:     .word 0xa023            ; 01 0001 1
        bra fail
i4:     .word 0xa030            ; 011 0000
        bra fail
i5:     .word 0xa422, 0x1234    ; st #imm16
        bra fail

fail:   bra fail

; the fault: count it, say its letter (F for the first), go to the next
illegal: ld push, [0x5100]
        add tos, #1
        st [0x5100], tos
        add tos, #69
        st [0xf000], pop
        ld push, [0x5100]
        sub tos, #1
        st null, pop
        skip ne
        bra i2
        ld push, [0x5100]
        sub tos, #2
        st null, pop
        skip ne
        bra i3
        ld push, [0x5100]
        sub tos, #3
        st null, pop
        skip ne
        bra i4
        ld push, [0x5100]
        sub tos, #4
        st null, pop
        skip ne
        bra i5
stop:   bra stop
