; Calls of variadic functions, as the ACK C compiler makes them: the caller
; pushes the arguments (the last one first), then a count word on top; the
; callee finds it at [pfp+0] and the named parameters start at [pfp+2]. The
; count is the number of words in the argument area less the words of the
; result. The callee puts its result at [pfp+2*count] through a pointer, frees
; its locals and executes the variadic RET, which takes the count from [pfp+0]:
; the result ends up on top of the stack, in place of the arguments. A result
; of two words has a pad word below the arguments, so that it does not
; overwrite the count. Expect ABCDEFG, then silence. A check that fails stops
; the program with the letter missing. Each check also tests that PFP and the
; stack come back as they were: a sentinel is pushed below the call.

start:

; A: sum(2, 10, 20) = 30; four words with the count, one result: count 3
        lea push, [pfp+0]       ; PFP, to be compared after the call
        ld push, #0x5a5a        ; the sentinel
        ld push, #20
        ld push, #10
        ld push, #2
        ld push, #3
        call sum
        sub tos, #30
        st null, pop
        skip eq
        bra fail
        sub tos, #0x5a5a        ; the stack is as it was: the sentinel is next
        st null, pop
        skip eq
        bra fail
        lea push, [pfp+0]
        sub push, tos           ; PFP after - PFP before
        skip eq
        bra fail
        ld push, #65            ; 'A'
        st [0xf000], pop

; B: sum(0), no variadic arguments: count 1
        ld push, #0x5a5a
        ld push, #0
        ld push, #1
        call sum
        sub tos, #0
        st null, pop
        skip eq
        bra fail
        sub tos, #0x5a5a
        st null, pop
        skip eq
        bra fail
        ld push, #66            ; 'B'
        st [0xf000], pop

; C: sum(3, 1, 2, 3) = 6: five words, count 4
        ld push, #0x5a5a
        ld push, #3
        ld push, #2
        ld push, #1
        ld push, #3
        ld push, #4
        call sum
        sub tos, #6
        st null, pop
        skip eq
        bra fail
        sub tos, #0x5a5a
        st null, pop
        skip eq
        bra fail
        ld push, #67            ; 'C'
        st [0xf000], pop

; D: a result of two words, one named parameter and a pad: count 3 - 2 = 1
        ld push, #0x5a5a
        ld push, #0             ; the pad
        ld push, #7
        ld push, #1
        call lresult
        sub tos, #0x5678
        st null, pop
        skip eq
        bra fail
        sub tos, #0x1234
        st null, pop
        skip eq
        bra fail
        sub tos, #0x5a5a
        st null, pop
        skip eq
        bra fail
        ld push, #68            ; 'D'
        st [0xf000], pop

; E: the same with two more arguments: count 5 - 2 = 3
        ld push, #0x5a5a
        ld push, #0
        ld push, #30
        ld push, #20
        ld push, #7
        ld push, #3
        call lresult
        sub tos, #0x5678
        st null, pop
        skip eq
        bra fail
        sub tos, #0x1234
        st null, pop
        skip eq
        bra fail
        sub tos, #0x5a5a
        st null, pop
        skip eq
        bra fail
        ld push, #69            ; 'E'
        st [0xf000], pop

; F: no result: store(1, 0xbeef) writes 0xbeef at 0x2000; the count is 3
        ld push, #0x5a5a
        ld push, #0xbeef
        ld push, #1
        ld push, #3
        call store
        sub tos, #0x5a5a
        st null, pop
        skip eq
        bra fail
        ld push, [0x2000]
        sub tos, #0xbeef
        st null, pop
        skip eq
        bra fail
        ld push, #70            ; 'F'
        st [0xf000], pop

; G: a variadic function that calls a variadic function: nest(5) is
; sum(2, 5, 6) + 1 = 12; the return stack holds two frames
        ld push, #0x5a5a
        ld push, #5
        ld push, #1             ; count: 2 words in the area, one result
        call nest
        sub tos, #12
        st null, pop
        skip eq
        bra fail
        sub tos, #0x5a5a
        st null, pop
        skip eq
        bra fail
        ld push, #71            ; 'G'
        st [0xf000], pop

done:   bra done
fail:   bra fail

; int sum(int n, ...): the sum of the n words after n. Two locals: the
; accumulator [pfp-2] and the counter [pfp-4]; the pointer is on the stack.
sum:
        addp sp, #-4
        ld push, #0
        st [pfp-2], pop
        ld push, [pfp+2]
        st [pfp-4], pop
        lea push, [pfp+4]
sum_loop:
        ld push, [pfp-4]
        cmp tos, #0
        st null, pop
        skip ne
        bra sum_end
        ld push, [tos]
        add tos, [pfp-2]
        st [pfp-2], pop
        add tos, #2
        ld push, [pfp-4]
        sub tos, #1
        st [pfp-4], pop
        bra sum_loop
sum_end:
        st null, pop
        ld push, [pfp-2]
        lea push, [pfp+0]       ; the epilogue of a variadic function
        ld push, [pfp+0]
        add tos, tos
        add push, tos
        st [tos], under
        st null, pop
        addp sp, #4
        ret

; long lresult(int n, ...): 0x12345678
lresult:
        ld push, #0x1234
        ld push, #0x5678
        lea push, [pfp+0]
        ld push, [pfp+0]
        add tos, tos
        add push, tos
        st [tos], under
        add tos, #2
        st [tos], under
        st null, pop
        ret

; void store(int n, ...): writes the word after n at 0x2000
store:
        ld push, [pfp+4]
        st [0x2000], pop
        ret

; int nest(int a, ...): sum(2, a, a + 1) + 1
nest:
        ld push, [pfp+2]
        add tos, #1
        ld push, [pfp+2]
        ld push, #2
        ld push, #3             ; the count: 4 words in the area, one result
        call sum
        add tos, #1
        lea push, [pfp+0]
        ld push, [pfp+0]
        add tos, tos
        add push, tos
        st [tos], under
        st null, pop
        ret
