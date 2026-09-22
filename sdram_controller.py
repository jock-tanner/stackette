"""
Simple controller for the ISSI IS42S16160G-7 SDRAM found on the DE0-Nano.
16M x 16-bit data bus (32 MiB), CAS latency 3, 50 MHz controller clock by
default (set clk_frequency to match the clock you actually drive it with).

Amaranth HDL port of stffrdhrn/sdram-controller (rtl/sdram_controller.v).

Very simple host interface:
  * No burst support.

  * wr_addr / wr_data / wr_enable : write 16 bits. wr_data is latched when
                                    wr_enable is high; the write reaches
                                    the SDRAM a few clocks later.

  * rd_addr / rd_enable           : read 16 bits. rd_addr is latched on the
                                    clock edge where rd_enable is high;
                                    rd_data is valid a few clocks later,
                                    when rd_ready pulses.

  * rst                           : (synchronous) starts the
                                    SDRAM initialisation sequence.

  * busy                          : high while a read/write is in flight;
                                    don't issue commands while it is high.

The bidirectional SDRAM `data` bus is split into data_i / data_o / data_oe so
it can be connected to whatever tristate primitive your platform uses.
"""
from enum import IntEnum
from amaranth import Cat, Const, Elaboratable, Module, Signal


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
    INIT_REF2 = 0b01100
    INIT_NOP3 = 0b01101
    INIT_LOAD = 0b01110
    INIT_NOP4 = 0b01111

    REF_PRE = 0b00001
    REF_NOP1 = 0b00010
    REF_REF = 0b00011
    REF_NOP2 = 0b00100

    READ_ACT = 0b10000
    READ_NOP1 = 0b10001
    READ_CAS = 0b10010
    READ_NOP2 = 0b10011
    READ_READ = 0b10100

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


class SDRAMController(Elaboratable):
    def __init__(
        self, *, row_width=13, col_width=9, bank_width=2,
        clk_frequency=50,    # MHz, frequency of the controller's clock
        refresh_time=32,     # ms (how often we need to refresh)
        refresh_count=8192,  # refresh cycles required per refresh_time
    ):
        self.row_width = row_width
        self.col_width = col_width
        self.bank_width = bank_width
        self.sdraddr_width = max(row_width, col_width)
        self.haddr_width = bank_width + row_width + col_width

        if self.sdraddr_width < 11:
            raise ValueError(
                'SDRAM address bus must be at least 11 bits wide (A10 is used)'
            )

        # clk / refresh = clk / sec  *  sec / refbatch  ->  cycles per refresh
        self.cycles_between_refresh = (
            (clk_frequency * 1_000 * refresh_time) // refresh_count
        )

        # Host interface
        self.wr_addr = Signal(self.haddr_width)
        self.wr_data = Signal(16)
        self.wr_enable = Signal()
        self.rd_addr = Signal(self.haddr_width)
        self.rd_data = Signal(16)
        self.rd_ready = Signal()
        self.rd_enable = Signal()
        self.busy = Signal()
        self.rst = Signal()

        # SDRAM side
        self.addr = Signal(self.sdraddr_width)
        self.bank_addr = Signal(bank_width)
        self.data_i = Signal(16)                # from the SDRAM DQ pins
        self.data_o = Signal(16)                # to the SDRAM DQ pins
        self.data_oe = Signal()                 # drive DQ pins when high
        self.clock_enable = Signal()
        self.cs_n = Signal()
        self.ras_n = Signal()
        self.cas_n = Signal()
        self.we_n = Signal()
        self.data_mask_low = Signal()
        self.data_mask_high = Signal()

    @property
    def ports(self):
        return [
            self.wr_addr, self.wr_data, self.wr_enable,
            self.rd_addr, self.rd_data, self.rd_ready, self.rd_enable,
            self.busy, self.rst,
            self.addr, self.bank_addr,
            self.data_i, self.data_o, self.data_oe,
            self.clock_enable, self.cs_n, self.ras_n, self.cas_n, self.we_n,
            self.data_mask_low, self.data_mask_high,
        ]

    def elaborate(self, platform):
        m = Module()

        row_width = self.row_width
        col_width = self.col_width
        bank_width = self.bank_width

        # I/O registers
        haddr_r = Signal(self.haddr_width)
        wr_data_r = Signal(16)

        # Internal wiring
        state_cnt = Signal(4)
        refresh_cnt = Signal(10)
        command = Signal(8)
        state = Signal(5)

        # TODO (from the original): output addr[6:4] when programming mode
        #   register
        command_next = Signal(8)
        state_cnt_next = Signal(4)
        state_next = Signal(5)

        bank_addr_r = Signal(bank_width)
        addr_r = Signal(self.sdraddr_width)

        # Outputs to the SDRAM
        # assign {clock_enable, cs_n, ras_n, cas_n, we_n} = command[7:3];
        m.d.comb += Cat(
            self.we_n, self.cas_n, self.ras_n, self.cs_n, self.clock_enable
        ).eq(command[3:8])

        # state[4] is set if the mode is read/write
        with m.If(state[4]):
            m.d.comb += self.bank_addr.eq(bank_addr_r)
        with m.Else():
            m.d.comb += self.bank_addr.eq(command[1:3])

        with m.If(state[4] | (state == State.INIT_LOAD)):
            m.d.comb += self.addr.eq(addr_r)
        with m.Else():
            # A10 = command[0], all other address bits zero
            m.d.comb += self.addr.eq(Cat(Const(0, 10), command[0]))

        # Tristate data bus: drive only during the write CAS cycle
        m.d.comb += [
            self.data_o.eq(wr_data_r),
            self.data_oe.eq(state == State.WRIT_CAS),
        ]

        # Data masks: 0 while reading/writing, 1 otherwise
        m.d.comb += [
            self.data_mask_low.eq(~state[4]),
            self.data_mask_high.eq(~state[4]),
        ]

        # Host interface (all registered on the clock edge)
        with m.If(self.rst):
            m.d.sync += [
                state.eq(State.INIT_NOP1),
                command.eq(Command.NOP),
                state_cnt.eq(0xf),
                haddr_r.eq(0),
                wr_data_r.eq(0),
                self.rd_data.eq(0),
                self.busy.eq(0),
            ]
        with m.Else():
            m.d.sync += [
                state.eq(state_next),
                command.eq(command_next),
            ]

            with m.If(state_cnt == 0):
                m.d.sync += state_cnt.eq(state_cnt_next)
            with m.Else():
                m.d.sync += state_cnt.eq(state_cnt - 1)

            with m.If(self.wr_enable):
                m.d.sync += wr_data_r.eq(self.wr_data)

            with m.If(state == State.READ_READ):
                m.d.sync += [
                    self.rd_data.eq(self.data_i),
                    self.rd_ready.eq(1),
                ]
            with m.Else():
                m.d.sync += self.rd_ready.eq(0)

            m.d.sync += self.busy.eq(state[4])

            with m.If(self.rd_enable):
                m.d.sync += haddr_r.eq(self.rd_addr)
            with m.Elif(self.wr_enable):
                m.d.sync += haddr_r.eq(self.wr_addr)

        # Refresh counter
        with m.If(self.rst):
            m.d.sync += refresh_cnt.eq(0)
        with m.Elif(state == State.REF_NOP2):
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
            # A9 (burst read / single write) = 1, CAS latency = 3,
            # burst length = 1
            m.d.comb += addr_r.eq(0b1000110000)

        # Next-state logic
        m.d.comb += [
            state_cnt_next.eq(0),
            command_next.eq(Command.NOP),
        ]

        with m.If(state == State.IDLE):
            # Monitor for refresh or hold
            with m.If(refresh_cnt >= self.cycles_between_refresh):
                m.d.comb += [
                    state_next.eq(State.REF_PRE),
                    command_next.eq(Command.PALL),
                ]
            with m.Elif(self.rd_enable):
                m.d.comb += [
                    state_next.eq(State.READ_ACT),
                    command_next.eq(Command.BACT),
                ]
            with m.Elif(self.wr_enable):
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
                    m.d.comb += [
                        state_next.eq(State.INIT_NOP2),
                        state_cnt_next.eq(3),
                    ]
                with m.Case(State.INIT_NOP2):
                    m.d.comb += [
                        state_next.eq(State.INIT_REF2),
                        command_next.eq(Command.REF),
                    ]
                with m.Case(State.INIT_REF2):
                    m.d.comb += [
                        state_next.eq(State.INIT_NOP3),
                        state_cnt_next.eq(3),
                    ]
                with m.Case(State.INIT_NOP3):
                    m.d.comb += [
                        state_next.eq(State.INIT_LOAD),
                        command_next.eq(Command.MRS),
                    ]
                with m.Case(State.INIT_LOAD):
                    m.d.comb += state_next.eq(State.INIT_NOP4)
                # INIT_NOP4: default -> IDLE

                # - refresh
                with m.Case(State.REF_PRE):
                    m.d.comb += state_next.eq(State.REF_NOP1)
                with m.Case(State.REF_NOP1):
                    m.d.comb += [
                        state_next.eq(State.REF_REF),
                        command_next.eq(Command.REF),
                    ]
                with m.Case(State.REF_REF):
                    m.d.comb += [
                        state_next.eq(State.REF_NOP2),
                        state_cnt_next.eq(3),
                    ]
                # REF_NOP2: default -> IDLE

                # - write
                with m.Case(State.WRIT_ACT):
                    m.d.comb += state_next.eq(State.WRIT_NOP1)
                with m.Case(State.WRIT_NOP1):
                    m.d.comb += [
                        state_next.eq(State.WRIT_CAS),
                        command_next.eq(Command.WRIT),
                    ]
                with m.Case(State.WRIT_CAS):
                    m.d.comb += state_next.eq(State.WRIT_NOP2)
                # WRIT_NOP2: default -> IDLE

                # - read
                with m.Case(State.READ_ACT):
                    m.d.comb += state_next.eq(State.READ_NOP1)
                with m.Case(State.READ_NOP1):
                    m.d.comb += [
                        state_next.eq(State.READ_CAS),
                        command_next.eq(Command.READ),
                    ]
                with m.Case(State.READ_CAS):
                    m.d.comb += state_next.eq(State.READ_NOP2)
                with m.Case(State.READ_NOP2):
                    m.d.comb += state_next.eq(State.READ_READ)
                # READ_READ: default -> IDLE

                with m.Default():
                    m.d.comb += state_next.eq(State.IDLE)

        with m.Else():
            # Counter not reached - HOLD
            m.d.comb += [
                state_next.eq(state),
                command_next.eq(command),
            ]

        return m
