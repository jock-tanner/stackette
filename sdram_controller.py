"""
Simple controller for the ISSI IS42S16160G-7 SDRAM found on the DE0-Nano.
16M x 16-bit data bus (32 MiB), CAS latency 2, 50 MHz controller clock by
default (set CLOCK_FREQ to match the clock you actually drive it with).

Amaranth HDL port of stffrdhrn/sdram-controller (rtl/sdram_controller.v),
with the interfaces described using amaranth.lib.wiring (Amaranth >= 0.5).

Host interface (`bus`, a MemoryPort, see memory_port.py) is a valid/ready
handshake on `bus.req`:
  * No burst support on the host side. With burst=4 (the default) the chip
    is read 4 words at a time, an aligned block of 8 bytes, and the words
    are kept in `buffers` read-ahead buffers: a later read of a word that is
    in one is answered without the SDRAM (see below).

  * The initiator sets valid together with we / addr / wdata and holds them
    until ready is high. The request is accepted on the clock edge where
    valid & ready. ready is only high in IDLE when no refresh is due, so a
    request can never be silently dropped: just keep valid up.

  * we = 1 writes wdata (16 bits) to addr; the write reaches the SDRAM a few
    clocks later. No response is generated for writes.

  * byte_hi / byte_lo select which bytes of the word take part. They drive
    the SDRAM's UDQM / LDQM masks (inverted: enabled = unmasked), so a write
    stores only the enabled bytes, and on a read the disabled lane of
    resp.data is undefined. Like addr and wdata they are latched on accept,
    so the initiator may drop the whole request right after it. (The masks
    must stay active until the data cycle, a few clocks after the accept:
    with unlatched masks an initiator that drops its request would block the
    write, or leave the data bus floating on a read.)

  * we = 0 reads 16 bits from addr; the result appears on `bus.resp`
    a few clocks later, when resp.valid pulses. On a read the byte enables
    are ignored by the SDRAM (both lanes are read, the buffers need whole
    words), the lane that was not enabled is still undefined for the host.

Read-ahead (burst=4):
  * A read first looks (READ_CHK, one clock, from the latched address, so
    the MMU's late address never meets the comparison) for the word in the
    buffers: a hit is answered 2 clocks after the accept, and the SDRAM is
    not touched. A miss reads the aligned block of 4 words (sequential
    burst, wrapping inside the block, the requested word first: its response
    is as early as without burst) into the buffer that was used least
    recently; the controller is ready again when the 4th word is in. The
    block is inside a row and a bank (columns are the low address bits).
  * Every write invalidates the word it writes in the buffers (all writes
    come through here), a refresh does not matter. Reads and writes of all
    initiators go through the same buffers, so with two of them a data read
    does not push the code stream's block out.

Timing (50 MHz, CL2, all derived from the -7 datasheet minimums, see T_*):
  * write: ACT, WRITE the next clock (tRCD 15 ns), ready again 2 clocks after
    the WRITE: 3 clocks per write. Read: 6 clocks from accept to response on
    a miss (5 with burst=1, which has no READ_CHK), 2 on a hit.
  * Every access ends with auto precharge and the spacing of the next
    command includes tRP, so refresh needs no PRECHARGE: REF, one NOP, ready
    again (3 clocks), once per tREF/8192 minus a margin.
  * Init: 200 us hold (the board wrapper), PRECHARGE ALL, 8 REF, MRS.

Reset:
  * The controller uses the reset of the domain it runs in (e.g. "sync", or
    whatever you rename it to with DomainRenamer). Reset (or power-up)
    starts the SDRAM initialization sequence; there is no separate rst port.
    The host sees ready = 0 until initialization has finished.

SDRAM interface (`sdram`, an SDRAMPins signature):
  * The bidirectional DQ bus is split into dq_i / dq_o / dq_oe so it can be
    connected to whatever tristate primitive your platform uses.
"""
from enum import IntEnum
from amaranth import Cat, Const, Module, Mux, Signal
from amaranth.lib.wiring import Component, In, Out, Signature

from memory_port import MemoryPort

CLOCK_FREQ = 50         # MHz, frequency of the controller's clock
REFRESH_TIME = 64       # ms (how often we need to refresh)
REFRESH_COUNT = 8192    # refresh cycles required per refresh_time
REFRESH_MARGIN = 16     # clocks: a refresh may wait for an access that is
                        # already running (longest one is under 12 clocks)
POWERUP_TIME = 200      # us of stable clock + NOPs before the first command
                        # (datasheet: 100 us)
INIT_REFRESHES = 8      # auto refreshes during init (datasheet: at least 2)

# IS42S16160G-7 minimums in ns (datasheet Rev. F, -7 column). Everything the
# controller waits for is derived from these and CLOCK_FREQ, so a different
# clock needs no other change.
T_RCD = 15      # ACT to READ/WRITE
T_RP = 15       # PRECHARGE to ACT (also what auto precharge needs)
T_RC = 60       # ACT to ACT / REF to ACT, same bank (this part: any bank)
T_DAL = 30      # last write data to the next ACT/REF, with auto precharge
                # (tDPL 14 + tRP 15, rounded up as the datasheet does)


def clocks(ns):
    """Smallest whole number of controller clocks that is >= ns."""
    return (ns * CLOCK_FREQ + 999) // 1000

ROW_WIDTH = 13
COL_WIDTH = 9
BANK_WIDTH = 2
SDRAM_ADDR_WIDTH = max(ROW_WIDTH, COL_WIDTH)
HOST_ADDR_WIDTH = ROW_WIDTH + COL_WIDTH + BANK_WIDTH


def fill_sel(victim, i):
    """Is buffer i the victim (a Signal, or the constant 0 for one buffer)."""
    if isinstance(victim, int):
        return Const(int(victim == i), 1)
    return victim == i


class State(IntEnum):
    """
    State encoding (same values as the Verilog; state[4] means "read/write").
    """
    IDLE = 0b00000

    INIT_NOP1 = 0b01000
    INIT_PRE1 = 0b01001
    INIT_NOP1_1 = 0b00101
    INIT_REF1 = 0b01010
    INIT_NOP2 = 0b01011
    INIT_LOAD = 0b01110
    INIT_NOP4 = 0b01111

    REF_REF = 0b00011
    REF_NOP2 = 0b00100

    READ_ACT = 0b10000
    READ_NOP1 = 0b10001
    READ_CAS = 0b10010
    READ_NOP2 = 0b10011
    READ_READ = 0b10100
    READ_CHK = 0b10101

    WRIT_ACT = 0b11000
    WRIT_NOP1 = 0b11001
    WRIT_CAS = 0b11010
    WRIT_NOP2 = 0b11011


class Command(IntEnum):
    """
    SDRAM commands: CKE, CS#, RAS#, CAS#, WE#, BA1, BA0, A10. The Verilog
    uses 'x' (don't care) in some bits; they are 0 here.
    """
    PALL = 0b10010001
    REF = 0b10001000
    NOP = 0b10111000
    MRS = 0b10000000    # 1000000x
    BACT = 0b10011000   # 10011xxx
    READ = 0b10101001   # 10101xx1
    WRIT = 0b10100001   # 10100xx1


class SDRAMPins(Signature):
    """
    SDRAM pins, described from the controller's point of view. The clock is
    not included: it is driven separately (phase shifted) by the board.
    """
    def __init__(self):
        super().__init__({
            'a': Out(SDRAM_ADDR_WIDTH),
            'ba': Out(BANK_WIDTH),
            'cke': Out(1),
            'cs_n': Out(1),
            'ras_n': Out(1),
            'cas_n': Out(1),
            'we_n': Out(1),
            'dqm': Out(2),        # {UDQM, LDQM}
            'dq_i': In(16),        # from the SDRAM DQ pins
            'dq_o': Out(16),       # to the SDRAM DQ pins
            'dq_oe': Out(1),        # drive DQ pins when high
        })


class SDRAMController(Component):
    bus: In(MemoryPort(HOST_ADDR_WIDTH))
    sdram: Out(SDRAMPins())

    def __init__(self, *, read_delay=1, cas_latency=2, burst=4, buffers=2):
        """
        burst: 4 reads 4 words at a time into the read-ahead buffers, 1 is
        the plain controller (no buffers, every read goes to the SDRAM).
        buffers: how many 4-word blocks are kept (1 or 2).
        cas_latency: 2 or 3, programmed into the mode register. The -7 part
        allows CL2 at this clock (tCK2 min 7.5 ns, tAC2 5.4 ns, the same
        access time as CL3), and it makes every read one clock shorter.
        read_delay: capture offset trim. The data is captured in READ_READ,
        cas_latency + read_delay - 1 clocks after the READ command's clock,
        so with read_delay=1 (the proven setting at CL3) that is
        cas_latency clocks. The phase-shifted SDRAM clock moves the data
        window against the capture edge, 0 is the original timing.
        """
        super().__init__()
        assert cas_latency in (2, 3)
        assert burst in (1, 4)
        assert buffers in (1, 2)
        self.burst = burst
        self.buffers = buffers
        self.cas_latency = cas_latency
        self.read_delay = read_delay
        # distance from the READ command to the capture, in clocks
        self.capture = cas_latency + read_delay - 1
        assert self.capture >= 1
        # extra NOP clocks between ACT and the column command (tRCD)
        self.rcd_wait = clocks(T_RCD) - 1
        # clocks from the WRITE command to the next ACT: tDAL, and tRC from
        # the ACT (which is rcd_wait + 1 clocks before the WRITE); at least
        # 2: one clock for auto precharge to start, one for tRP
        self.write_to_act = max(
            2, clocks(T_DAL), clocks(T_RC) - self.rcd_wait - 1
        )
        # clocks from a REF command to the next command (tRC)
        self.ref_to_cmd = max(2, clocks(T_RC))
        # time between refresh commands, minus what a running access can
        # delay one
        self.cycles_between_refresh = (
            (CLOCK_FREQ * 1_000 * REFRESH_TIME) // REFRESH_COUNT
            - REFRESH_MARGIN
        )
        # the longest access (accept to ready again: the check, ACT, the
        # NOPs, READ, the NOPs, the burst) must not stretch the interval
        # over the limit
        self.read_clocks = (
            (burst > 1) + 1 + self.rcd_wait + 1 + (self.capture - 1) + burst
        )
        assert self.read_clocks + 4 <= REFRESH_MARGIN

    @property
    def ports(self):
        """All interface signals, e.g. for amaranth.back.verilog.convert().
        Add ClockSignal()/ResetSignal() of the domain yourself."""
        return [value for _, _, value in self.signature.flatten(self)]

    def elaborate(self, platform):
        m = Module()

        row_width = ROW_WIDTH
        col_width = COL_WIDTH
        bank_width = BANK_WIDTH

        host = self.bus.req
        resp = self.bus.resp
        sdram = self.sdram

        # Registers. Their reset values (applied by the domain reset) put the
        # controller at the start of the initialization sequence.
        haddr_r = Signal(HOST_ADDR_WIDTH)
        wr_data_r = Signal(16)
        dqm_r = Signal(2)
        state_cnt = Signal(4, init=0xf)
        refresh_cnt = Signal(10)
        init_refs = Signal(4, init=INIT_REFRESHES)
        command = Signal(8, init=Command.NOP)
        state = Signal(5, init=State.INIT_NOP1)

        # TODO (from the original): output addr[6:4] when programming mode
        #   register
        command_next = Signal(8)
        state_cnt_next = Signal(4)
        state_next = Signal(5)

        bank_addr_r = Signal(bank_width)
        addr_r = Signal(SDRAM_ADDR_WIDTH)

        # the read-ahead buffers: block address, a valid bit per word, the
        # 4 words; `lru` the one used least recently, `fill` the one the
        # burst in progress goes to
        blk = haddr_r[2:]
        off = haddr_r[:2]
        btag = [
            Signal(HOST_ADDR_WIDTH - 2, name=f'btag{i}')
            for i in range(self.buffers)
        ]
        bvalid = [Signal(4, name=f'bvalid{i}') for i in range(self.buffers)]
        bdata = [Signal(64, name=f'bdata{i}') for i in range(self.buffers)]
        lru = Signal()
        fill = Signal()

        # Request handshake: only take requests in IDLE, and not when a
        # refresh is about to start (the refresh has priority in IDLE).
        refresh_due = Signal()
        accept = Signal()
        m.d.comb += [
            refresh_due.eq(refresh_cnt >= self.cycles_between_refresh),
            host.ready.eq((state == State.IDLE) & ~refresh_due),
            accept.eq(host.valid & host.ready),
        ]

        # Outputs to the SDRAM
        # assign {clock_enable, cs_n, ras_n, cas_n, we_n} = command[7:3];
        m.d.comb += Cat(
            sdram.we_n, sdram.cas_n, sdram.ras_n, sdram.cs_n, sdram.cke
        ).eq(command[3:8])

        # state[4] is set if the mode is read/write
        with m.If(state[4]):
            m.d.comb += sdram.ba.eq(bank_addr_r)
        with m.Else():
            m.d.comb += sdram.ba.eq(command[1:3])

        with m.If(state[4] | (state == State.INIT_LOAD)):
            m.d.comb += sdram.a.eq(addr_r)
        with m.Else():
            # A10 = command[0], all other address bits zero
            m.d.comb += sdram.a.eq(Cat(Const(0, 10), command[0]))

        # Tristate data bus: drive only during the write CAS cycle
        m.d.comb += [
            sdram.dq_o.eq(wr_data_r),
            sdram.dq_oe.eq(state == State.WRIT_CAS),
        ]

        # DQM masks (active low on the chip): follow the latched byte
        # enables while reading/writing, mask both lanes otherwise.
        # Cat order: bit 0 = LDQM (byte_lo), bit 1 = UDQM (byte_hi).
        m.d.comb += sdram.dqm.eq(Mux(state[4], dqm_r, 0b11))

        # Registered state, command and counters
        m.d.sync += [
            state.eq(state_next),
            command.eq(command_next),
        ]

        with m.If(state_cnt == 0):
            m.d.sync += state_cnt.eq(state_cnt_next)
        with m.Else():
            m.d.sync += state_cnt.eq(state_cnt - 1)

        # Latch the request when it is accepted
        with m.If(accept):
            m.d.sync += [
                haddr_r.eq(host.addr),
                wr_data_r.eq(host.wdata),
                dqm_r.eq(Cat([~host.byte_lo, ~host.byte_hi])),
            ]
            if self.burst > 1:
                # a read takes both lanes, the buffers keep whole words
                with m.If(~host.we):
                    m.d.sync += dqm_r.eq(0)

        # Read response: the first word of the burst as it comes (the one
        # that was asked for), or a hit in the buffers
        m.d.sync += resp.valid.eq(0)
        with m.If((state == State.READ_READ) & (state_cnt == self.burst - 1)):
            m.d.sync += [
                resp.data.eq(sdram.dq_i),
                resp.valid.eq(1),
            ]

        if self.burst > 1:
            hits = [
                (btag[i] == blk) & bvalid[i].bit_select(off, 1)
                for i in range(self.buffers)
            ]
            hit_any = Signal()
            hit_data = Signal(16)
            if self.buffers == 2:
                m.d.comb += [
                    hit_any.eq(hits[0] | hits[1]),
                    hit_data.eq(Mux(
                        hits[0],
                        bdata[0].word_select(off, 16),
                        bdata[1].word_select(off, 16),
                    )),
                ]
            else:
                m.d.comb += [
                    hit_any.eq(hits[0]),
                    hit_data.eq(bdata[0].word_select(off, 16)),
                ]
            with m.If((state == State.READ_CHK) & hit_any):
                m.d.sync += [
                    resp.data.eq(hit_data),
                    resp.valid.eq(1),
                ]
                if self.buffers == 2:
                    m.d.sync += lru.eq(hits[0])

            # The burst's words: the first is the requested one, they wrap
            # inside the block. The word is valid from the clock after.
            idx = Signal(2)
            m.d.comb += idx.eq(off + ~state_cnt[:2])
            with m.If(state == State.READ_READ):
                for i in range(self.buffers):
                    with m.If(fill == i):
                        m.d.sync += [
                            bdata[i].word_select(idx, 16).eq(sdram.dq_i),
                            bvalid[i].bit_select(idx, 1).eq(1),
                        ]

            # A write takes its word out of the buffers (the check is in
            # the ACT clock, the next access is later than that)
            with m.If(state == State.WRIT_ACT):
                for i in range(self.buffers):
                    with m.If(btag[i] == blk):
                        m.d.sync += bvalid[i].bit_select(off, 1).eq(0)

        # Refresh counter: counts from one REF command to the next
        with m.If(state == State.REF_REF):
            m.d.sync += refresh_cnt.eq(0)
        with m.Else():
            m.d.sync += refresh_cnt.eq(refresh_cnt + 1)

        # Address generation for the SDRAM, based on the current state
        # Host address layout: | bank | row | column |
        bank_bits = haddr_r[col_width + row_width:]
        row_bits  = haddr_r[col_width:col_width + row_width]
        col_bits  = haddr_r[:col_width]

        with m.If((state == State.READ_ACT) | (state == State.WRIT_ACT)):
            m.d.comb += [
                bank_addr_r.eq(bank_bits),
                addr_r.eq(row_bits),
            ]
        with m.Elif((state == State.READ_CAS) | (state == State.WRIT_CAS)):
            # Send column address; bank is the bank to precharge.
            # addr = 0s, A10 = 1 (auto precharge), 0s, column address
            m.d.comb += [
                bank_addr_r.eq(bank_bits),
                addr_r.eq(Cat(col_bits, Const(0, 10 - col_width), Const(1, 1))),
            ]
        with m.Elif(state == State.INIT_LOAD):
            # Program the mode register during the load cycle.
            # A9 (burst read / single write) = 1, CAS latency in A6:A4,
            # sequential bursts, burst length A2:A0 = 1 or 4 (0b010)
            m.d.comb += addr_r.eq(
                0b1000000000 | (self.cas_latency << 4)
                | (0b010 if self.burst == 4 else 0)
            )

        # Next-state logic
        m.d.comb += [
            state_cnt_next.eq(0),
            command_next.eq(Command.NOP),
        ]

        with m.If(state == State.IDLE):
            # Monitor for refresh or hold
            with m.If(refresh_due):
                # every access ends with auto precharge, so all banks are idle
                # already (the access spacing includes tRP): REF right away
                m.d.comb += [
                    state_next.eq(State.REF_REF),
                    command_next.eq(Command.REF),
                ]
            with m.Elif(accept & ~host.we):
                if self.burst > 1:
                    # look in the buffers first, from the latched address
                    m.d.comb += state_next.eq(State.READ_CHK)
                else:
                    m.d.comb += [
                        state_next.eq(State.READ_ACT),
                        command_next.eq(Command.BACT),
                    ]
            with m.Elif(accept & host.we):
                m.d.comb += [
                    state_next.eq(State.WRIT_ACT),
                    command_next.eq(Command.BACT),
                ]
            with m.Else():
                m.d.comb += state_next.eq(State.IDLE)  # HOLD

        with m.Elif(state_cnt == 0):
            with m.Switch(state):
                # - init engine
                with m.Case(State.INIT_NOP1):
                    m.d.comb += [
                        state_next.eq(State.INIT_PRE1),
                        command_next.eq(Command.PALL),
                    ]
                with m.Case(State.INIT_PRE1):
                    m.d.comb += state_next.eq(State.INIT_NOP1_1)
                with m.Case(State.INIT_NOP1_1):
                    m.d.comb += [
                        state_next.eq(State.INIT_REF1),
                        command_next.eq(Command.REF),
                    ]
                with m.Case(State.INIT_REF1):
                    m.d.sync += init_refs.eq(init_refs - 1)
                    m.d.comb += [
                        state_next.eq(State.INIT_NOP2),
                        state_cnt_next.eq(3),
                    ]
                with m.Case(State.INIT_NOP2):
                    with m.If(init_refs == 0):
                        m.d.comb += [
                            state_next.eq(State.INIT_LOAD),
                            command_next.eq(Command.MRS),
                        ]
                    with m.Else():
                        m.d.comb += [
                            state_next.eq(State.INIT_REF1),
                            command_next.eq(Command.REF),
                        ]
                with m.Case(State.INIT_LOAD):
                    m.d.comb += state_next.eq(State.INIT_NOP4)
                # INIT_NOP4: default -> IDLE

                # - refresh (IDLE went straight to REF_REF)
                with m.Case(State.REF_REF):
                    if self.ref_to_cmd > 2:
                        m.d.comb += [
                            state_next.eq(State.REF_NOP2),
                            state_cnt_next.eq(self.ref_to_cmd - 3),
                        ]
                # REF_NOP2: default -> IDLE

                # - write: ACT, rcd_wait NOPs, WRITE, then IDLE again
                #   (write_to_act - 2) NOPs later
                with m.Case(State.WRIT_ACT):
                    if self.rcd_wait:
                        m.d.comb += [
                            state_next.eq(State.WRIT_NOP1),
                            state_cnt_next.eq(self.rcd_wait - 1),
                        ]
                    else:
                        m.d.comb += [
                            state_next.eq(State.WRIT_CAS),
                            command_next.eq(Command.WRIT),
                        ]
                with m.Case(State.WRIT_NOP1):
                    m.d.comb += [
                        state_next.eq(State.WRIT_CAS),
                        command_next.eq(Command.WRIT),
                    ]
                with m.Case(State.WRIT_CAS):
                    if self.write_to_act > 2:
                        m.d.comb += [
                            state_next.eq(State.WRIT_NOP2),
                            state_cnt_next.eq(self.write_to_act - 3),
                        ]
                # WRIT_NOP2: default -> IDLE

                # - read: ACT, rcd_wait NOPs, READ, capture - 1 NOPs,
                #   READ_READ captures the data
                with m.Case(State.READ_ACT):
                    if self.rcd_wait:
                        m.d.comb += [
                            state_next.eq(State.READ_NOP1),
                            state_cnt_next.eq(self.rcd_wait - 1),
                        ]
                    else:
                        m.d.comb += [
                            state_next.eq(State.READ_CAS),
                            command_next.eq(Command.READ),
                        ]
                with m.Case(State.READ_NOP1):
                    m.d.comb += [
                        state_next.eq(State.READ_CAS),
                        command_next.eq(Command.READ),
                    ]
                with m.Case(State.READ_CAS):
                    if self.capture > 1:
                        m.d.comb += [
                            state_next.eq(State.READ_NOP2),
                            state_cnt_next.eq(self.capture - 2),
                        ]
                    else:
                        m.d.comb += [
                            state_next.eq(State.READ_READ),
                            state_cnt_next.eq(self.burst - 1),
                        ]
                with m.Case(State.READ_NOP2):
                    m.d.comb += [
                        state_next.eq(State.READ_READ),
                        state_cnt_next.eq(self.burst - 1),
                    ]
                # READ_READ: the burst's `burst` clocks (state_cnt counts
                # them down), then the default -> IDLE

                # the check of a read: a hit is answered above and we are
                # done, a miss takes the least recently used buffer and goes
                # on to the SDRAM
                if self.burst > 1:
                    with m.Case(State.READ_CHK):
                        with m.If(~hit_any):
                            m.d.comb += [
                                state_next.eq(State.READ_ACT),
                                command_next.eq(Command.BACT),
                            ]
                            victim = lru if self.buffers == 2 else 0
                            m.d.sync += fill.eq(victim)
                            for i in range(self.buffers):
                                with m.If(fill_sel(victim, i)):
                                    m.d.sync += [
                                        btag[i].eq(blk),
                                        bvalid[i].eq(0),
                                    ]
                            if self.buffers == 2:
                                m.d.sync += lru.eq(~lru)

                with m.Default():
                    m.d.comb += state_next.eq(State.IDLE)

        with m.Else():
            # Counter not reached - HOLD
            m.d.comb += [
                state_next.eq(state),
                command_next.eq(command),
            ]

        return m
