; hw28: bringup of the IDE-CF adapter on the IDE host (ide_pio.py, the
; registers at 0xf080: the task file as words 0..7, the timing 8..10, the
; status of the host 11, the alternate status 14). The program
;   - selects the card (master, LBA) and waits for BSY = 0 and DRDY = 1,
;   - sends IDENTIFY DEVICE (0xec), waits for DRQ and reads the 256 words,
;   - prints the model string (the characters of a word are in the order
;     high byte, low byte) and the number of sectors (words 61 and 60),
;   - writes the sector 4 with a pattern (WRITE SECTORS, 0x30), reads it back
;     (READ SECTORS, 0x20) and compares.
; The result goes to the UART, at 38400 baud:
;     IDE CF
;     MODEL: STACKETTE TEST CARD
;     SECTORS=00000800
;     WRITE/READ 1 SECTOR OK
; ERR and the number of the words that differ where the sector is not what was
; written; NO CARD if the card does not get ready (a floating bus reads 0xffff,
; BSY = 1; it is followed by the status, the alternate status, the status of the
; host (bit 0 INTRQ, bit 1 timeout, bit 2 IORDY) and what the sector count
; register keeps after a write of 0xa5, and the status with the drive select
; of a slave, ST1), TIMEOUT if it does not give DRQ. A ! alone is a fault of the
; system context.
;
; The variables (RAM at 0x5000..):
;   0x5020 value to print     0x5022 digit       0x5024 digits left
;   0x5026 bits left          0x5030 flag: timeout
;   0x5036 outer polls        0x5038 inner polls
;   0x5050 pattern            0x5052 words left  0x5054 errors
;   0x5100..0x52ff buffer of a sector (the IDENTIFY data first)

start:  ld push, [0xf02e]       ; interrupt ID, 0 after power-up
        sub tos, #0
        st null, pop
        skip eq
        bra fault
        ld push, #banner
        st mbp, pop
        call puts

; hardware reset (RESET# if it is wired), then a wait for the card
        ld push, #1
        st [0xf098], pop
        call delay
        ld push, #0
        st [0xf098], pop
        call delay
        call delay
        call delay

; software reset of the card: SRST (and nIEN) for a while, then released
        ld push, #6
        st [0xf09c], pop
        call delay
        ld push, #2
        st [0xf09c], pop
        call delay

; the card: master, LBA
        ld push, #0xe0
        st [0xf08c], pop
        call waitrdy
        ld push, [0x5030]
        tst tos, #1
        st null, pop
        skip eq
        bra nocard

; IDENTIFY DEVICE
        ld push, #0xec
        st [0xf08e], pop
        call waitdrq
        ld push, [0x5030]
        tst tos, #1
        st null, pop
        skip eq
        bra timeout
        call rdsect

; the model: words 27..46, the characters of a word are swapped
        ld push, #modelm
        st mbp, pop
        call puts
        ld push, #0
        st [0x5052], pop
mod1:   ld push, [0x5052]
        xor tos, #1
        add tos, #0x5136        ; 0x5100 + 2 * 27
        st mbp, pop
        ld push, #0
        st midx, pop
        ld.b push, [mbp+midx]
        st [0xf000], pop
        ld push, [0x5052]
        add tos, #1
        st [0x5052], tos
        cmp pop, #40
        skip ne
        bra mod2
        bra mod1
mod2:   ld push, #secm
        st mbp, pop
        call puts
        ld push, [0x517c]       ; word 62, 0x5100 + 2 * 62: the high part
        st [0x5020], pop
        call hex16
        ld push, [0x5178]       ; word 60
        st [0x5020], pop
        call hex16
        ld push, #crlf
        st mbp, pop
        call puts

; WRITE SECTORS: LBA 4, one sector
        ld push, #1
        st [0xf084], pop
        ld push, #4
        st [0xf086], pop
        ld push, #0
        st [0xf088], pop
        ld push, #0
        st [0xf08a], pop
        ld push, #0xe0
        st [0xf08c], pop
        ld push, #0x30
        st [0xf08e], pop
        call waitdrq
        ld push, [0x5030]
        tst tos, #1
        st null, pop
        skip eq
        bra timeout
        ld push, #0x1234
        st [0x5050], pop
        ld push, #256
        st [0x5052], pop
wsec:   ld push, [0x5050]
        st [0xf080], pop
        ld push, [0x5050]
        add tos, #0x2531
        st [0x5050], pop
        ld push, [0x5052]
        sub tos, #1
        st [0x5052], pop
        skip eq
        bra wsec
        call waitrdy
        ld push, [0x5030]
        tst tos, #1
        st null, pop
        skip eq
        bra timeout

; READ SECTORS and compare
        ld push, #0x20
        st [0xf08e], pop
        call waitdrq
        ld push, [0x5030]
        tst tos, #1
        st null, pop
        skip eq
        bra timeout
        call rdsect
        ld push, #0x1234
        st [0x5050], pop
        ld push, #0
        st [0x5054], pop
        ld push, #0x5100
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, #256
        st [0x5052], pop
cmpw:   ld push, [mbp+midx++]
        sub tos, [0x5050]
        st null, pop
        skip eq
        call counterr
        ld push, [0x5050]
        add tos, #0x2531
        st [0x5050], pop
        ld push, [0x5052]
        sub tos, #1
        st [0x5052], pop
        skip eq
        bra cmpw
        ld push, [0x5054]
        tst tos, #0xffff
        st null, pop
        skip ne
        bra rwgood
        ld push, #rwerr
        st mbp, pop
        call puts
        ld push, [0x5054]
        st [0x5020], pop
        call hex16
        ld push, #crlf
        st mbp, pop
        call puts
        bra stop
rwgood: ld push, #rwok
        st mbp, pop
        call puts
stop:   bra stop
nocard: ld push, #nocardm
        st mbp, pop
        call puts
        ld push, [0xf08e]       ; the status register
        st [0x5020], pop
        call hex16
        ld push, #almsg
        st mbp, pop
        call puts
        ld push, [0xf09c]       ; the alternate status
        st [0x5020], pop
        call hex16
        ld push, #hsmsg
        st mbp, pop
        call puts
        ld push, [0xf096]       ; the host: INTRQ, timeout, IORDY
        st [0x5020], pop
        call hex16
        ld push, #0xa5          ; the sector count register keeps what is
        st [0xf084], pop        ; written, if the bus works
        ld push, #scmsg
        st mbp, pop
        call puts
        ld push, [0xf084]
        st [0x5020], pop
        call hex16
        ld push, #0xf0         ; the card as a slave (DEV = 1)?
        st [0xf08c], pop
        ld push, #slmsg
        st mbp, pop
        call puts
        ld push, [0xf08e]
        st [0x5020], pop
        call hex16
        ld push, #crlf
        st mbp, pop
        call puts
        bra stop
timeout:
        ld push, #tomsg
        st mbp, pop
        call puts
        bra stop
fault:  ld push, #33            ; '!'
        st [0xf000], pop
        bra stop

counterr:
        ld push, [0x5054]
        add tos, #1
        st [0x5054], pop
        ret #0

; delay: about 10 ms
delay:  ld push, #0
        st [0x5038], pop
dly1:   ld push, [0x5038]
        sub tos, #1
        st [0x5038], pop
        skip eq
        bra dly1
        ret #0

; rdsect: 256 words from the data port to 0x5100
rdsect: ld push, #0x5100
        st mbp, pop
        ld push, #0
        st midx, pop
        ld push, #256
        st [0x5052], pop
rds1:   ld push, [0xf080]
        st [mbp+midx++], pop
        ld push, [0x5052]
        sub tos, #1
        st [0x5052], pop
        skip eq
        bra rds1
        ret #0

; waitrdy: poll the status until BSY = 0 and DRDY = 1; [0x5030] = 1 on a
; timeout (100 * 65536 polls)
waitrdy:
        ld push, #0
        st [0x5030], pop
        ld push, #100
        st [0x5036], pop
wr1:    ld push, #0
        st [0x5038], pop
wr2:    ld push, [0xf08e]
        and tos, #0xc0
        sub tos, #0x40
        st null, pop
        skip eq
        bra wr3
        ret #0
wr3:    ld push, [0x5038]
        sub tos, #1
        st [0x5038], pop
        skip eq
        bra wr2
        ld push, [0x5036]
        sub tos, #1
        st [0x5036], pop
        skip eq
        bra wr1
        ld push, #1
        st [0x5030], pop
        ret #0

; waitdrq: poll the status until BSY = 0 and DRQ = 1; [0x5030] = 1 on a timeout
waitdrq:
        ld push, #0
        st [0x5030], pop
        ld push, #100
        st [0x5036], pop
wq1:    ld push, #0
        st [0x5038], pop
wq2:    ld push, [0xf08e]
        and tos, #0x88
        sub tos, #0x08
        st null, pop
        skip eq
        bra wq3
        ret #0
wq3:    ld push, [0x5038]
        sub tos, #1
        st [0x5038], pop
        skip eq
        bra wq2
        ld push, [0x5036]
        sub tos, #1
        st [0x5036], pop
        skip eq
        bra wq1
        ld push, #1
        st [0x5030], pop
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
        .word 0x4449, 0x2045, 0x4643, 0x0a0d, 0x0000
modelm:
        .word 0x4f4d, 0x4544, 0x3a4c, 0x0020
secm:
        .word 0x0a0d, 0x4553, 0x5443, 0x524f, 0x3d53, 0x0000
rwok:
        .word 0x5257, 0x5449, 0x2f45, 0x4552, 0x4441, 0x3120, 0x5320, 0x4345, 0x4f54, 0x2052, 0x4b4f, 0x0a0d, 0x0000
rwerr:
        .word 0x5257, 0x5449, 0x2f45, 0x4552, 0x4441, 0x3120, 0x5320, 0x4345, 0x4f54, 0x2052, 0x5245, 0x2052, 0x0000
nocardm:
        .word 0x4f4e, 0x4320, 0x5241, 0x2044, 0x5453, 0x003d
slmsg:
        .word 0x5320, 0x3154, 0x003d
almsg:
        .word 0x4120, 0x3d4c, 0x0000
hsmsg:
        .word 0x4820, 0x3d53, 0x0000
scmsg:
        .word 0x5320, 0x3d43, 0x0000
tomsg:
        .word 0x4954, 0x454d, 0x554f, 0x0d54, 0x000a
crlf:
        .word 0x0a0d, 0x0000
