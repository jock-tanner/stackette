; hw25: bringup of the ENC28J60 board on the SPI master (spi_master.py, the
; registers at 0xf040: DATA, CONTROL, DIVIDER, STATUS). The SPI is done by the
; hardware: a store of DATA sends a byte (st.b) or two bytes (st, the low byte
; first), a load of DATA waits for the transfer and returns what came in.
; The program
;   - resets the chip and waits for ESTAT.CLKRDY,
;   - reads the PHY identification registers PHID1 and PHID2 through the MII
;     interface and compares them with the documented values,
;       PHID1 = 0x0083, PHID2 = 0x1400 | PREV (the revision, bits 3..0),
;   - reads EREVID,
;   - writes 32 words into the buffer memory of the chip (WBM) and reads them
;     back (RBM), a word at a time,
;   - times the read of 1024 bytes of the buffer with the timer (one tick per
;     microsecond, with the clock of 50 MHz).
; The result goes to the UART, at 38400 baud:
;     ENC28J60 PHID1=0083 PHID2=1400 OK
;     EREVID=0006
;     BUFFER 64 BYTES OK
;     READ 1024 BYTES IN 02A3 US (HEX)
; ERR instead of OK where the values are not the right ones; NO CLKRDY (the
; clock of the chip does not come up) and MII TIMEOUT (MISTAT.BUSY does not go
; down) stop the program. With no chip, a MISO that is low gives NO CLKRDY,
; a MISO that is high gives MII TIMEOUT. A ! alone is a fault of the system
; context.
;
; The ENC28J60 (DS39662): the commands are RCR 000a aaaa, WCR 010a aaaa,
; BFS 100a aaaa, BFC 101a aaaa, RBM 0x3a, WBM 0x7a, and the system reset 0xff.
; The registers are in four banks, selected by ECON1 bits 1..0 (the same address
; 0x1f in all banks); a read of a MAC or MII register has a dummy byte after the
; command. A PHY register is read through MIREGADR (bank 2, 0x14), MICMD.MIIRD
; (bank 2, 0x12 bit 0), a wait for MISTAT.BUSY (bank 3, 0x0a bit 0) to go down,
; and MIRDL, MIRDH (bank 2, 0x18, 0x19). The times of CS_N are kept by the
; hardware.
;
; The variables (RAM at 0x5000..):
;   0x5010 command byte         0x5012 dummy byte flag    0x5014 data, in or out
;   0x5018 bank                 0x5020 value to print     0x5022 digit
;   0x5024 digits left          0x5026 bits left
;   0x5030 PHY register         0x5032 PHY value          0x5034 timeout flag
;   0x5036 polls                0x5038 delay counter
;   0x5040 PHID1                0x5042 PHID2
;   0x5050 pattern              0x5052 words left        0x5054 errors

start:  ld push, [0xf02e]       ; interrupt ID, 0 after power-up
        sub tos, #0
        st null, pop
        skip eq
        bra fault
        ld push, #banner
        st mbp, pop
        call puts

; the system reset, then wait for the oscillator: ESTAT.CLKRDY (0x1d bit 0)
        ld push, #0xff
        st [0x5010], pop
        call cmd1
        call delay
        ld push, #0
        st [0x5036], pop
wclk:   ld push, #0x1d
        st [0x5010], pop
        ld push, #0
        st [0x5012], pop
        call rcr
        ld push, [0x5014]
        tst tos, #1
        st null, pop
        skip eq
        bra clkok
        ld push, [0x5036]
        add tos, #1
        st [0x5036], tos
        cmp pop, #100
        skip cc
        bra noclk
        bra wclk
clkok:  call delay              ; the PHY wants some time more

; PHID1 and PHID2
        ld push, #2
        st [0x5030], pop
        call phyrd
        ld push, [0x5034]
        tst tos, #255
        st null, pop
        skip eq
        bra miito
        ld push, [0x5032]
        st [0x5040], pop
        ld push, #3
        st [0x5030], pop
        call phyrd
        ld push, [0x5034]
        tst tos, #255
        st null, pop
        skip eq
        bra miito
        ld push, [0x5032]
        st [0x5042], pop
        ld push, #phid1
        st mbp, pop
        call puts
        ld push, [0x5040]
        st [0x5020], pop
        call hex16
        ld push, #phid2
        st mbp, pop
        call puts
        ld push, [0x5042]
        st [0x5020], pop
        call hex16
        ld push, [0x5040]
        sub tos, #0x83
        st null, pop
        skip eq
        bra bad1
        ld push, [0x5042]
        and tos, #0xfff0
        sub tos, #0x1400
        st null, pop
        skip eq
        bra bad1
        ld push, #okmsg
        st mbp, pop
        call puts
        bra rev
bad1:   ld push, #errmsg
        st mbp, pop
        call puts

; EREVID (bank 3, 0x12): an ETH register, no dummy byte
rev:    ld push, #3
        st [0x5018], pop
        call bank
        ld push, #0x12
        st [0x5010], pop
        ld push, #0
        st [0x5012], pop
        call rcr
        ld push, #revmsg
        st mbp, pop
        call puts
        ld push, [0x5014]
        st [0x5020], pop
        call hex16
        ld push, #crlf
        st mbp, pop
        call puts

; the buffer: EWRPT = ERDPT = 0x0200 (bank 0), 32 words written, read back
        ld push, #0
        st [0x5018], pop
        call bank
        ld push, #0x42          ; WCR EWRPTL
        st [0x5010], pop
        ld push, #0
        st [0x5014], pop
        call wcr
        ld push, #0x43          ; WCR EWRPTH
        st [0x5010], pop
        ld push, #2
        st [0x5014], pop
        call wcr
        ld push, #0x40          ; WCR ERDPTL
        st [0x5010], pop
        ld push, #0
        st [0x5014], pop
        call wcr
        ld push, #0x41          ; WCR ERDPTH
        st [0x5010], pop
        ld push, #2
        st [0x5014], pop
        call wcr
        ld push, #0x1234
        st [0x5050], pop
        ld push, #32
        st [0x5052], pop
        ld push, #1
        st [0xf042], pop
        ld push, #0x7a          ; WBM
        st.b [0xf040], pop
bw:     ld push, [0x5050]
        st [0xf040], pop        ; two bytes, the low one first
        ld push, [0x5050]
        add tos, #0x2531
        st [0x5050], pop
        ld push, [0x5052]
        sub tos, #1
        st [0x5052], pop
        skip eq
        bra bw
        ld push, #0
        st [0xf042], pop
        ld push, #0x1234
        st [0x5050], pop
        ld push, #32
        st [0x5052], pop
        ld push, #0
        st [0x5054], pop
        ld push, #1
        st [0xf042], pop
        ld push, #0x3a          ; RBM
        st.b [0xf040], pop
br:     ld push, #0
        st [0xf040], pop        ; two clocked bytes
        ld push, [0xf040]       ; they came in
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
        bra br
        ld push, #0
        st [0xf042], pop
        ld push, #bufmsg
        st mbp, pop
        call puts
        ld push, [0x5054]
        tst tos, #255
        st null, pop
        skip eq
        bra bad2
        ld push, #okmsg
        st mbp, pop
        call puts
        bra timing
bad2:   ld push, #errmsg
        st mbp, pop
        call puts

; the speed: 512 words from the buffer, timed by the timer (PRESCALE 49: a tick
; per microsecond, COUNT, CONTROL.RUN)
timing: ld push, #49
        st [0xf018], pop
        ld push, #0
        st [0xf010], pop
        ld push, #1
        st [0xf014], pop
        ld push, #512
        st [0x5052], pop
        ld push, #1
        st [0xf042], pop
        ld push, #0x3a
        st.b [0xf040], pop
rd:     ld push, #0
        st [0xf040], pop
        ld push, [0xf040]
        st null, pop
        ld push, [0x5052]
        sub tos, #1
        st [0x5052], pop
        skip eq
        bra rd
        ld push, [0xf010]
        st [0x5020], pop
        ld push, #0
        st [0xf014], pop        ; the timer stops
        ld push, #0
        st [0xf042], pop        ; CS
        ld push, #timemsg
        st mbp, pop
        call puts
        call hex16
        ld push, #usmsg
        st mbp, pop
        call puts
        bra stop

noclk:  ld push, #noclkmsg
        st mbp, pop
        call puts
        bra stop
miito:  ld push, #miimsg
        st mbp, pop
        call puts
stop:   bra stop
fault:  ld push, #33            ; '!'
        st [0xf000], pop
        bra stop

counterr:
        ld push, [0x5054]
        add tos, #1
        st [0x5054], pop
        ret #0

; phyrd: the PHY register [0x5030] -> [0x5032]; [0x5034] is 1 on a timeout
phyrd:  ld push, #0
        st [0x5034], pop
        ld push, #2
        st [0x5018], pop
        call bank
        ld push, #0x54          ; WCR MIREGADR
        st [0x5010], pop
        ld push, [0x5030]
        st [0x5014], pop
        call wcr
        ld push, #0x52          ; WCR MICMD: MIIRD
        st [0x5010], pop
        ld push, #1
        st [0x5014], pop
        call wcr
        ld push, #3
        st [0x5018], pop
        call bank
        ld push, #0
        st [0x5036], pop
mbusy:  ld push, #0x0a          ; RCR MISTAT, a MII register
        st [0x5010], pop
        ld push, #1
        st [0x5012], pop
        call rcr
        ld push, [0x5014]
        tst tos, #1             ; BUSY
        st null, pop
        skip ne
        bra mdone
        ld push, [0x5036]
        add tos, #1
        st [0x5036], tos
        cmp pop, #100
        skip cc
        bra mtime
        bra mbusy
mtime:  ld push, #1
        st [0x5034], pop
        ret #0
mdone:  ld push, #2
        st [0x5018], pop
        call bank
        ld push, #0x52          ; WCR MICMD: 0
        st [0x5010], pop
        ld push, #0
        st [0x5014], pop
        call wcr
        ld push, #0x18          ; RCR MIRDL
        st [0x5010], pop
        ld push, #1
        st [0x5012], pop
        call rcr
        ld push, [0x5014]
        st [0x5032], pop
        ld push, #0x19          ; RCR MIRDH
        st [0x5010], pop
        ld push, #1
        st [0x5012], pop
        call rcr
        ld push, [0x5014]
        bswap tos
        or tos, [0x5032]
        st [0x5032], pop
        ret #0

; bank: selects the bank [0x5018]: BFC ECON1 with 3, then BFS ECON1 with the bank
bank:   ld push, #0xbf
        st [0x5010], pop
        ld push, #3
        st [0x5014], pop
        call wcr
        ld push, [0x5018]
        tst tos, #255
        st null, pop
        skip ne
        ret #0
        ld push, #0x9f
        st [0x5010], pop
        ld push, [0x5018]
        st [0x5014], pop
        call wcr
        ret #0

; cmd1: a command of one byte, [0x5010]
cmd1:   ld push, #1             ; CS
        st [0xf042], pop
        ld push, [0x5010]
        st.b [0xf040], pop
        ld push, #0
        st [0xf042], pop
        ret #0

; wcr: the command [0x5010] (WCR, BFS, BFC) with the data [0x5014]
wcr:    ld push, #1
        st [0xf042], pop
        ld push, [0x5010]
        st.b [0xf040], pop
        ld push, [0x5014]
        st.b [0xf040], pop
        ld push, #0
        st [0xf042], pop
        ret #0

; rcr: the command [0x5010] (RCR), a dummy byte if [0x5012] is not 0; the
; register goes to [0x5014]
rcr:    ld push, #1
        st [0xf042], pop
        ld push, [0x5010]
        st.b [0xf040], pop
        ld push, [0x5012]
        tst tos, #255
        st null, pop
        skip eq
        call xferz
        call xferz
        ld push, [0xf040]       ; the byte of the last transfer
        st [0x5014], pop
        ld push, #0
        st [0xf042], pop
        ret #0

; xferz: a transfer of the byte 0
xferz:  ld push, #0
        st.b [0xf040], pop
        ret #0

; delay: 65536 turns of a loop
delay:  ld push, #0
        st [0x5038], pop
dl1:    ld push, [0x5038]
        sub tos, #1
        st [0x5038], pop
        skip eq
        bra dl1
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
        .word 0x4e45, 0x3243, 0x4a38, 0x3036, 0x0020
phid1:
        .word 0x4850, 0x4449, 0x3d31, 0x0000
phid2:
        .word 0x5020, 0x4948, 0x3244, 0x003d
okmsg:
        .word 0x4f20, 0x0d4b, 0x000a
errmsg:
        .word 0x4520, 0x5252, 0x0a0d, 0x0000
revmsg:
        .word 0x5245, 0x5645, 0x4449, 0x003d
crlf:
        .word 0x0a0d, 0x0000
bufmsg:
        .word 0x5542, 0x4646, 0x5245, 0x3620, 0x2034, 0x5942, 0x4554, 0x0053
timemsg:
        .word 0x4552, 0x4441, 0x3120, 0x3230, 0x2034, 0x5942, 0x4554, 0x2053
        .word 0x4e49, 0x0020
usmsg:
        .word 0x5520, 0x2053, 0x4828, 0x5845, 0x0d29, 0x000a
noclkmsg:
        .word 0x4f4e, 0x4320, 0x4b4c, 0x4452, 0x0d59, 0x000a
miimsg:
        .word 0x494d, 0x2049, 0x4954, 0x454d, 0x554f, 0x0d54, 0x000a
