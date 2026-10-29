"""
4-line write-back stack window ("TOS/NOS cache") for the parameter stack.

Architecture (what the CPU sees): a stack in ordinary memory, growing down.
PSP points at the top item, item k (k = 0 is TOS, 1 is NOS, ...) lives at
byte address PSP + 2*k. Push is PSP -= 2, pop is PSP += 2.

Implementation: the four topmost items are held in registers (line 0..3, so
reading an operand costs no extra cycle), each with a valid and a dirty bit.
There are no address tags: a line's address is implied by its position,
PSP + 2*position, which is why nothing here depends on the address width or
on an MMU (the cache emits the same virtual word addresses the CPU would).

  * push / pop only RENAME lines (they shift one position and PSP moves by 2),
    so a line keeps its address, valid and dirty bits for free.
  * push allocates without reading memory. Pushing evicts line 3; if it is
    valid and dirty it goes to a one-entry write buffer and is written out in
    the background.
  * pop discards the old TOS even if it is dirty ("dead pop": memory below PSP
    is undefined) and invalidates line 3, which is NOT refetched until some
    instruction names it. So push/pop pairs at the window edge cost no memory
    traffic after the first.
  * "under" operations (PUSH_UNDER inserts below TOS, POP_UNDER removes NOS)
    leave line 0 in place but its address changes, so line 0 becomes dirty.
  * a line is filled from memory on demand, when a command names it in `need`
    and it is invalid; the command waits (ready low) until all needed lines
    are valid. Fills wait for the write buffer, so a fill never reads memory
    that is about to be overwritten.
  * changing PSP any other way (SET_PSP) writes back dirty lines first and
    invalidates everything: the old PSP is needed to compute their addresses.

Command interface (`cmd`), valid/ready like MemoryPort: hold valid and all
fields stable until ready. ready may depend combinationally on the command's
fields, so valid must not depend on ready. The command executes on the edge
where valid & ready.

  A stack command (maint = NONE) does, in one cycle and in this order:
    1. write w0 / w1 (optional): line w?_pos (numbered as before the command)
       gets w?_data, marked valid and dirty. The two positions must differ.
    2. shift:
         NONE        nothing
         PUSH        insert `arg` as the new TOS, everything moves down
         PUSH_UNDER  insert `arg` as the new NOS, TOS stays
         POP         drop TOS
         POP_UNDER   drop NOS, TOS stays
  `need` is the set of lines (bit k = line k, numbered as before the command)
  whose current values the instruction reads: they are valid and unchanged on
  d0..d3 in the cycle the command executes, so the CPU can compute the ALU
  result from d0..d3 and present it in w0_data in the same cycle.
  PUSH_UNDER and POP_UNDER need line 0 and fetch it themselves if necessary.

  Examples (dst codes of the ISA):
    add tos, src       need=0b0001 (+ src), w0 = (pos 0, result)
    add push, src ...  PUSH with arg = value
    sub 10, tos        need=0b0011, w0 = (pos 1, result), shift = POP
    sub 11, nos        need=0b0011, w0 = (pos 0, result), shift = POP_UNDER
    swap tos, nos      need=0b0011, w0 = (pos 0, d1), w1 = (pos 1, d0)

  A maintenance command (maint != NONE) is accepted when the cache is idle:
    FLUSH             write back dirty lines; lines stay valid and clean
    INVALIDATE        drop all lines WITHOUT writing back (only after FLUSH, or
                      when the stack contents are no longer needed)
    FLUSH_INVALIDATE  write back, then drop all lines
    SET_PSP           FLUSH_INVALIDATE, then PSP = arg
  While one runs, cmd.ready stays low.

Coherence with other memory accesses is the CPU's job (there are no address
comparators): before a load that may read the stack area through a pointer,
issue FLUSH; before a store that may write it, FLUSH_INVALIDATE; and let the
other master wait for `idle` so the write buffer has been accepted by memory.

Memory port (`mem`, initiator): word addresses = byte address >> 1, always full
words. Requests are in order, one outstanding fill at a time.

Faults: `mem_fault` is the memory's way to refuse a request instead of
accepting it (an address that has no memory behind it, a page that is not
mapped). It is high in the cycle the request is presented, and then
req.ready is low. A fault of a FILL read is reported on `fault` in that cycle,
the fill is abandoned and the line stays invalid; the command that wanted the
line has not executed, and the CPU takes it back (it drops cmd.valid). A
command executes completely or not at all, so a fault never leaves a command
half done. Faults of write-backs are not reported: a line only becomes dirty
if its address was usable when it was written, which the CPU side of the MMU
has to guarantee (it probes the address when a line is allocated without
reading it).

Outputs: d0..d3 (line data), line_valid / line_dirty (bit k = line k), psp,
idle (no command executing and the write buffer is empty), fault.
"""
from amaranth import Array, Cat, Const, Module, Mux, Signal
from amaranth.lib import enum, wiring
from amaranth.lib.wiring import In, Out

from memory_port import MemoryPort

ADDR_WIDTH = 15  # word address of a 64 KiB byte-addressed space


class Shift(enum.Enum, shape=3):
    """Stack commands."""
    NONE = 0
    PUSH = 1
    PUSH_UNDER = 2
    POP = 3
    POP_UNDER = 4


class Maint(enum.Enum, shape=3):
    """Cache maintenance commands."""
    NONE = 0  # a stack command: writes and/or a shift
    FLUSH = 1
    INVALIDATE = 2
    FLUSH_INVALIDATE = 3
    SET_PSP = 4


class StackCommand(wiring.Signature):
    """Command channel, from the CPU's (initiator's) point of view."""
    def __init__(self):
        super().__init__({
            'valid':   Out(1),
            'ready':   In(1),
            'maint':   Out(Maint),
            'shift':   Out(Shift),
            'need':    Out(4),
            'arg':     Out(16),     # PUSH/PUSH_UNDER data, SET_PSP value
            'w0_en':   Out(1),
            'w0_pos':  Out(2),
            'w0_data': Out(16),
            'w1_en':   Out(1),
            'w1_pos':  Out(2),
            'w1_data': Out(16),
        })


class StackCache(wiring.Component):
    cmd: In(StackCommand())
    mem: Out(MemoryPort(ADDR_WIDTH))

    d0: Out(16)
    d1: Out(16)
    d2: Out(16)
    d3: Out(16)
    line_valid: Out(4)
    line_dirty: Out(4)
    psp: Out(16)
    idle: Out(1)
    mem_fault: In(1)
    fault: Out(1)

    def elaborate(self, platform):
        m = Module()
        cmd = self.cmd
        req, resp = self.mem.req, self.mem.resp

        data = [Signal(16, name=f'data{k}') for k in range(4)]
        vld = [Signal(name=f'vld{k}') for k in range(4)]
        drt = [Signal(name=f'drt{k}') for k in range(4)]
        data_a, vld_a, drt_a = Array(data), Array(vld), Array(drt)

        wb_valid = Signal()
        wb_addr = Signal(ADDR_WIDTH)
        wb_data = Signal(16)
        fill_k = Signal(2)
        flush_k = Signal(3)
        maint_r = Signal(Maint)
        arg_r = Signal(16)

        m.d.comb += [
            self.d0.eq(data[0]),
            self.d1.eq(data[1]),
            self.d2.eq(data[2]),
            self.d3.eq(data[3]),
            self.line_valid.eq(Cat(*vld)),
            self.line_dirty.eq(Cat(*drt)),
        ]

        # Stage 1: the lines as they look after the command's writes.
        wd = [Signal(16, name=f'wd{k}') for k in range(4)]
        wv = [Signal(name=f'wv{k}') for k in range(4)]
        wr = [Signal(name=f'wr{k}') for k in range(4)]
        for k in range(4):
            hit0 = cmd.w0_en & (cmd.w0_pos == k)
            hit1 = cmd.w1_en & (cmd.w1_pos == k)
            m.d.comb += [
                wd[k].eq(Mux(hit1, cmd.w1_data, Mux(hit0, cmd.w0_data, data[k]))),
                wv[k].eq(vld[k] | hit0 | hit1),
                wr[k].eq(drt[k] | hit0 | hit1),
            ]

        # Command classification.
        is_stack = Signal()
        is_push = Signal()
        is_under = Signal()
        m.d.comb += [
            is_stack.eq(cmd.maint == Maint.NONE),
            is_push.eq((cmd.shift == Shift.PUSH) | (cmd.shift == Shift.PUSH_UNDER)),
            is_under.eq((cmd.shift == Shift.PUSH_UNDER) | (cmd.shift == Shift.POP_UNDER)),
        ]

        # Lines that must be fetched before the command can execute: operands
        # that are invalid, and line 0 for the "under" operations (it stays,
        # so it must exist unless the command overwrites it).
        miss = Signal(4)
        m.d.comb += miss.eq(
            (cmd.need & ~Cat(*vld)) | Cat(is_under & ~wv[0], Const(0, 3))
        )
        spill = Signal()  # pushing would evict a valid, dirty line 3
        m.d.comb += spill.eq(is_push & wv[3] & wr[3])
        stack_ok = Signal()
        m.d.comb += stack_ok.eq((miss == 0) & ~(spill & wb_valid))

        lowest = Signal(2)
        with m.If(miss[0]):
            m.d.comb += lowest.eq(0)
        with m.Elif(miss[1]):
            m.d.comb += lowest.eq(1)
        with m.Elif(miss[2]):
            m.d.comb += lowest.eq(2)
        with m.Else():
            m.d.comb += lowest.eq(3)

        accept = Signal()
        m.d.comb += accept.eq(cmd.valid & cmd.ready)

        word_psp = self.psp[1:]  # PSP as a word address (bit 0 is ignored)

        with m.FSM() as fsm:
            with m.State('IDLE'):
                m.d.comb += cmd.ready.eq(Mux(is_stack, stack_ok, 1))

                with m.If(accept):
                    with m.If(is_stack):
                        # writes apply in every case
                        for k in range(4):
                            m.d.sync += [
                                data[k].eq(wd[k]),
                                vld[k].eq(wv[k]),
                                drt[k].eq(wr[k]),
                            ]
                        with m.Switch(cmd.shift):
                            with m.Case(Shift.PUSH):
                                for k in (1, 2, 3):
                                    m.d.sync += [
                                        data[k].eq(wd[k - 1]),
                                        vld[k].eq(wv[k - 1]),
                                        drt[k].eq(wr[k - 1]),
                                    ]
                                m.d.sync += [
                                    data[0].eq(cmd.arg),
                                    vld[0].eq(1),
                                    drt[0].eq(1),
                                    self.psp.eq(self.psp - 2),
                                ]
                            with m.Case(Shift.PUSH_UNDER):
                                for k in (2, 3):
                                    m.d.sync += [
                                        data[k].eq(wd[k - 1]),
                                        vld[k].eq(wv[k - 1]),
                                        drt[k].eq(wr[k - 1]),
                                    ]
                                m.d.sync += [
                                    drt[0].eq(1),       # line 0 changes address
                                    data[1].eq(cmd.arg),
                                    vld[1].eq(1),
                                    drt[1].eq(1),
                                    self.psp.eq(self.psp - 2),
                                ]
                            with m.Case(Shift.POP):
                                for k in (0, 1, 2):
                                    m.d.sync += [
                                        data[k].eq(wd[k + 1]),
                                        vld[k].eq(wv[k + 1]),
                                        drt[k].eq(wr[k + 1]),
                                    ]
                                m.d.sync += [
                                    vld[3].eq(0),
                                    drt[3].eq(0),
                                    self.psp.eq(self.psp + 2),
                                ]
                            with m.Case(Shift.POP_UNDER):
                                for k in (1, 2):
                                    m.d.sync += [
                                        data[k].eq(wd[k + 1]),
                                        vld[k].eq(wv[k + 1]),
                                        drt[k].eq(wr[k + 1]),
                                    ]
                                m.d.sync += [
                                    drt[0].eq(1),  # line 0 changes address
                                    vld[3].eq(0),
                                    drt[3].eq(0),
                                    self.psp.eq(self.psp + 2),
                                ]
                        with m.If(spill):
                            m.d.sync += [
                                wb_valid.eq(1),
                                wb_addr.eq(word_psp + 3),
                                wb_data.eq(wd[3]),
                            ]
                    with m.Else():
                        # maintenance command
                        m.d.sync += [maint_r.eq(cmd.maint), arg_r.eq(cmd.arg)]
                        with m.If(cmd.maint == Maint.INVALIDATE):
                            for k in range(4):
                                m.d.sync += [vld[k].eq(0), drt[k].eq(0)]
                        with m.Else():
                            m.d.sync += flush_k.eq(0)
                            m.next = 'FLUSH'
                with m.Elif(cmd.valid & is_stack & (miss != 0) & ~wb_valid):
                    # wait for the write buffer first, then fetch a missing line
                    m.d.sync += fill_k.eq(lowest)
                    m.next = 'FILL_ISSUE'

            with m.State('FILL_ISSUE'):
                # The write buffer is empty here and stays empty: only an
                # accepted stack command fills it, and none is accepted
                # outside IDLE. That is what makes req.ready below really
                # mean "the fill read was accepted" (the buffer's write would
                # otherwise win the port and the FSM would wait for a
                # response that never comes).
                m.d.comb += [
                    req.valid.eq(1),
                    req.we.eq(0),
                    req.byte_hi.eq(1),
                    req.byte_lo.eq(1),
                    req.addr.eq(word_psp + fill_k),
                ]
                with m.If(self.mem_fault):
                    m.d.comb += self.fault.eq(1)
                    m.next = 'IDLE'
                with m.Elif(req.ready):
                    m.next = 'FILL_WAIT'

            with m.State('FILL_WAIT'):
                with m.If(resp.valid):
                    m.d.sync += [
                        data_a[fill_k].eq(resp.data),
                        vld_a[fill_k].eq(1),
                        drt_a[fill_k].eq(0),
                    ]
                    m.next = 'IDLE'

            with m.State('FLUSH'):
                with m.If(flush_k == 4):
                    with m.If(~wb_valid):  # every write has been accepted
                        with m.If(maint_r != Maint.FLUSH):
                            for k in range(4):
                                m.d.sync += [vld[k].eq(0), drt[k].eq(0)]
                        with m.If(maint_r == Maint.SET_PSP):
                            m.d.sync += self.psp.eq(arg_r)
                        m.next = 'IDLE'
                with m.Elif(~(vld_a[flush_k] & drt_a[flush_k])):
                    m.d.sync += flush_k.eq(flush_k + 1)
                with m.Elif(~wb_valid):
                    m.d.comb += [
                        req.valid.eq(1),
                        req.we.eq(1),
                        req.byte_hi.eq(1),
                        req.byte_lo.eq(1),
                        req.addr.eq(word_psp + flush_k),
                        req.wdata.eq(data_a[flush_k]),
                    ]
                    with m.If(req.ready):
                        m.d.sync += [
                            drt_a[flush_k].eq(0),
                            flush_k.eq(flush_k + 1),
                        ]

        # Write buffer: drains in the background, ahead of any other request
        # (the other states only drive the port while it is empty).
        with m.If(wb_valid):
            m.d.comb += [
                req.valid.eq(1),
                req.we.eq(1),
                req.byte_hi.eq(1),
                req.byte_lo.eq(1),
                req.addr.eq(wb_addr),
                req.wdata.eq(wb_data),
            ]
            with m.If(req.ready):
                m.d.sync += wb_valid.eq(0)

        m.d.comb += self.idle.eq(fsm.ongoing('IDLE') & ~wb_valid)

        return m
