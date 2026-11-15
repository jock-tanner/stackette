; hw29: the pins of the IDE connector by hand (BB_CTRL, BB_DD_OUT, BB_DD_IN and
; BB_PINS of ide_pio.py, words 16..19 at 0xf0a0), to find a fault of the wiring
; with a meter or a probe on the adapter:
;   1. DD is read with nothing driving it (what the pull-ups and the card say),
;      then driven with 0x5555, 0xaaaa, 0xffff and 0x0000 and read back from the
;      pins: a line that does not follow is open, shorted or driven by someone
;      else. INTRQ and IORDY (bit 1) are shown as read.
;   2. Then, for ever, each output in turn is made active (low for RESET#, CS0#, CS1#,
;      DIOR#, DIOW#, high for DA0, DA1, DA2) for about two seconds and released
;      for the same time, and its name goes to the UART; then each DD line is
;      driven high alone, the same way, with its number.
; ACTIVE is low for the lines with # in the name, high for DA and DD. The idle
; state is RESET#, CS0#, CS1#, DIOR#, DIOW# high and DA = 0.
;
; Variables: 0x5020 value to print, 0x5022.. hex16, 0x5038 delay,
;   0x5060 index, 0x5062 pointer into the table, 0x5064 the DD bit, 0x5066 count

start:  ld push, #banner
        st mbp, pop
        call puts
        ld push, #0x21f         ; on, all strobes high, DD not driven
        st [0xf0a0], pop
        ld push, #undrv
        st mbp, pop
        call puts
        ld push, [0xf0a4]
        st [0x5020], pop
        call hex16
        ld push, #pinsm
        st mbp, pop
        call puts
        ld push, [0xf0a6]
        st [0x5020], pop
        call hex16
        ld push, #crlf
        st mbp, pop
        call puts
        ld push, #0x5555
        call drive
        ld push, #0xaaaa
        call drive
        ld push, #0xffff
        call drive
        ld push, #0
        call drive

forever:
        ld push, #sigtab
        st [0x5062], pop
        ld push, #8
        st [0x5060], pop
sig1:   ld push, [0x5062]
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, [mbp+midx++]   ; the name
        st [0x5068], pop
        ld push, [mbp+midx++]   ; the state with the line active
        st [0x506a], pop
        ld push, [0x5068]
        st mbp, pop
        call puts
        ld push, #act
        st mbp, pop
        call puts
        ld push, [0x506a]
        st [0xf0a0], pop
        call wait2s
        ld push, [0x5068]
        st mbp, pop
        call puts
        ld push, #idl
        st mbp, pop
        call puts
        ld push, #0x21f
        st [0xf0a0], pop
        call wait2s
        ld push, [0x5062]
        add tos, #4
        st [0x5062], pop
        ld push, [0x5060]
        sub tos, #1
        st [0x5060], pop
        skip eq
        bra sig1

; DD lines, one at a time
        ld push, #1
        st [0x5064], pop
        ld push, #0
        st [0x5066], pop
dd1:    ld push, #ddm
        st mbp, pop
        call puts
        ld push, [0x5066]
        st [0x5020], pop
        call hex16
        ld push, #act
        st mbp, pop
        call puts
        ld push, [0x5064]
        st [0xf0a2], pop
        ld push, #0x31f         ; DD driven
        st [0xf0a0], pop
        call wait2s
        ld push, #0x21f
        st [0xf0a0], pop
        call wait2s
        ld push, [0x5064]
        add tos, [0x5064]
        st [0x5064], pop
        ld push, [0x5066]
        add tos, #1
        st [0x5066], tos
        cmp pop, #16
        skip eq
        bra dd1
        bra forever

; drive: the word on the stack is driven on DD, and what the pins say is
; printed
drive:  st [0xf0a2], pop
        ld push, #0x31f
        st [0xf0a0], pop
        ld push, #drv
        st mbp, pop
        call puts
        ld push, [0xf0a2]
        st [0x5020], pop
        call hex16
        ld push, #eq
        st mbp, pop
        call puts
        ld push, [0xf0a4]
        st [0x5020], pop
        call hex16
        ld push, #crlf
        st mbp, pop
        call puts
        ld push, #0x21f
        st [0xf0a0], pop
        ret #0

; wait2s: about two seconds
wait2s: ld push, #40
        st [0x506c], pop
w2a:    ld push, #0
        st [0x5038], pop
w2b:    ld push, [0x5038]
        sub tos, #1
        st [0x5038], pop
        skip eq
        bra w2b
        ld push, [0x506c]
        sub tos, #1
        st [0x506c], pop
        skip eq
        bra w2a
        ret #0

; puts: the string at MBP, to the UART
puts:   ld push, #0
        st midx, pop
puts1:  ld.b push, [mbp+midx++]
        tst tos, #255
        skip ne
        bra puts2
        st [0xf000], pop
        bra puts1
puts2:  st null, pop
        ret #0

sigtab:
        .word n_reset, 0x01f
        .word n_cs0, 0x21d
        .word n_cs1, 0x21b
        .word n_dior, 0x217
        .word n_diow, 0x20f
        .word n_da0, 0x23f
        .word n_da1, 0x25f
        .word n_da2, 0x29f

; hex16: [0x5020] as four hex digits, to the UART
hex16:  ld push, #4
        st [0x5024], pop
hdig:   ld push, #0
        st [0x5022], pop
        ld push, #4
        st [0x5026], pop
hbit:   ld push, [0x5020]
        add tos, tos            ; the top bit goes to C
        st [0x5020], pop
        ld push, [0x5022]
        adc tos, tos            ; digit = digit * 2 + C
        st [0x5022], pop
        ld push, [0x5026]
        sub tos, #1
        st [0x5026], pop
        skip eq
        bra hbit
        ld push, [0x5022]
        add tos, #48
        cmp tos, #58
        skip lo
        add tos, #7
        st [0xf000], pop
        ld push, [0x5024]
        sub tos, #1
        st [0x5024], pop
        skip eq
        bra hdig
        ret #0

banner:
        .word 0x5748, 0x3932, 0x4920, 0x4544, 0x5020, 0x4e49, 0x0d53, 0x000a
undrv:
        .word 0x4444, 0x5520, 0x444e, 0x4952, 0x4556, 0x3d4e, 0x0000
drv:
        .word 0x2020, 0x5244, 0x5649, 0x4e45, 0x0020
eq:
        .word 0x3e2d, 0x0000
pinsm:
        .word 0x2020, 0x4e49, 0x5254, 0x2c51, 0x4f49, 0x4452, 0x3d59, 0x0000
crlf:
        .word 0x0a0d, 0x0000
act:
        .word 0x4120, 0x5443, 0x5649, 0x0d45, 0x000a
idl:
        .word 0x4920, 0x4c44, 0x0d45, 0x000a
ddm:
        .word 0x4444, 0x0000
n_reset:
        .word 0x4552, 0x4553, 0x2354, 0x0000
n_cs0:
        .word 0x5343, 0x2330, 0x0000
n_cs1:
        .word 0x5343, 0x2331, 0x0000
n_dior:
        .word 0x4944, 0x524f, 0x0023
n_diow:
        .word 0x4944, 0x574f, 0x0023
n_da0:
        .word 0x4144, 0x0030
n_da1:
        .word 0x4144, 0x0031
n_da2:
        .word 0x4144, 0x0032
