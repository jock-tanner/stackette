; Word accesses at odd addresses of the data space: two byte accesses (the
; high lane of the word it starts in, then the low lane of the next word).
; Expect ABCDEFGH, then silence. (RAM at 0x5000..)

start:
        ld push, #0xaaaa
        st [0x5000], pop
        ld push, #0xbbbb
        st [0x5002], pop
        ld push, #0xcccc
        st [0x5004], pop
        ld push, #0x1234
        st [0x5001], pop
        ld push, [0x5000]
        sub tos, #0x34aa
        st null, pop
        skip eq
        bra done
        ld push, [0x5002]
        sub tos, #0xbb12
        st null, pop
        skip eq
        bra done
        ld push, [0x5004]
        sub tos, #0xcccc
        st null, pop
        skip eq
        bra done
        ld push, #65            ; 'A'
        st [0xf000], pop
        ld push, [0x5001]
        sub tos, #0x1234
        st null, pop
        skip eq
        bra done
        ld push, #66            ; 'B'
        st [0xf000], pop
        ld push, #0x5000
        st mbp, pop
        ld push, #1
        st midx, pop
        ld push, [mbp+midx++]
        sub tos, #0x1234
        st null, pop
        skip eq
        bra done
        ld push, midx
        sub tos, #0x3
        st null, pop
        skip eq
        bra done
        ld push, [mbp+midx]
        sub tos, #0xccbb
        st null, pop
        skip eq
        bra done
        ld push, #67            ; 'C'
        st [0xf000], pop
        ld push, #0x7788
        st [mbp+midx++], pop
        ld push, midx
        sub tos, #0x5
        st null, pop
        skip eq
        bra done
        ld push, [0x5002]
        sub tos, #0x8812
        st null, pop
        skip eq
        bra done
        ld push, [0x5004]
        sub tos, #0xcc77
        st null, pop
        skip eq
        bra done
        ld push, #68            ; 'D'
        st [0xf000], pop
        ld push, #0x5003
        ld push, [tos]
        sub tos, #0x7788
        st null, pop
        skip eq
        bra done
        st null, pop
        ld push, #69            ; 'E'
        st [0xf000], pop
        ld push, #1
        add tos, [0x5003]
        sub tos, #0x7789
        st null, pop
        skip eq
        bra done
        ld push, #70            ; 'F'
        st [0xf000], pop
        ld push, #0xabcd
        swap tos, [0x5001]
        sub tos, #0x1234
        st null, pop
        skip eq
        bra done
        ld push, [0x5001]
        sub tos, #0xabcd
        st null, pop
        skip eq
        bra done
        ld push, [0x5000]
        sub tos, #0xcdaa
        st null, pop
        skip eq
        bra done
        ld push, [0x5002]
        sub tos, #0x88ab
        st null, pop
        skip eq
        bra done
        ld push, #71            ; 'G'
        st [0xf000], pop
        ld push, #0x1357
        st [0x5006], pop
        ld push, [0x5006]
        sub tos, #0x1357
        st null, pop
        skip eq
        bra done
        ld.b push, [0x5007]
        sub tos, #0x13
        st null, pop
        skip eq
        bra done
        ld push, #72            ; 'H'
        st [0xf000], pop
done:
        bra done
