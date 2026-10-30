"""
Memory arbiter: shares one MemoryPort (towards MemorySelect) between several
initiators, each with its own address-space tag.

  portN    In(MemoryPort)   initiator N's port; its `spaceN` input says which
                            address space (Space.CODE / PSTACK / ...) its
                            requests are made in; the winner's tag goes out
                            on `space`, next to `mem`
  mem      Out(MemoryPort)  towards the MMU and MemorySelect
  space    Out(Space)
  fault    In(1)            the refusal of the request that is presented on
                            `mem` (see MemorySelect); it goes back as `faultN`
                            to the initiator whose request that is
  faultN   Out(1)
  lockN    In(1)            initiator N holds the memory: while it is up, no
                            other initiator is granted (SWAP does that from
                            its read to its write, so nothing gets in between)

Fixed priority: port0 wins over port1 and so on. Requests are passed through
combinationally, with no added latency; the loser simply sees ready low and
keeps its request up, as the MemoryPort protocol demands anyway.

It also keeps the non-pipelined MemorySelect's contract: at most one read
outstanding at any time, counted over all initiators. While a read is waiting
for its answer, further reads are held off (writes are not); `owner` remembers
whose read it is, so the response goes back to that initiator only. A new read
may be accepted in the very cycle the previous response arrives.

Starvation is possible in principle (a high-priority initiator issuing reads
back to back keeps the low-priority one out); fine for a CPU that stalls on
each of them anyway.
"""
from amaranth import Const, Module, Signal
from amaranth.lib.wiring import Component, In, Out

from memory_port import MemoryPort
from memory_select import Space


class MemoryArbiter(Component):
    def __init__(self, addr_width, n=2):
        members = {
            'mem': Out(MemoryPort(addr_width)),
            'space': Out(Space),
            'fault': In(1),
        }
        for i in range(n):
            members[f'port{i}'] = In(MemoryPort(addr_width))
            members[f'space{i}'] = In(Space)
            members[f'fault{i}'] = Out(1)
            members[f'lock{i}'] = In(1)
        super().__init__(members)
        self.n = n

    def elaborate(self, platform):
        m = Module()
        n = self.n
        mem = self.mem
        reqs = [getattr(self, f'port{i}').req for i in range(n)]
        resps = [getattr(self, f'port{i}').resp for i in range(n)]
        spaces = [getattr(self, f'space{i}') for i in range(n)]

        busy = Signal()           # a read is waiting for its answer
        owner = Signal(range(n))  # ... and this initiator issued it
        free = Signal()           # a read may go out now
        m.d.comb += free.eq(~busy | mem.resp.valid)

        # ---- fixed-priority grant ----
        grant = [Signal(name=f'grant{i}') for i in range(n)]
        locks = [getattr(self, f'lock{i}') for i in range(n)]
        blocked = Const(0)
        for i in range(n):
            # someone else holds the memory
            held = Const(0)
            for j in range(n):
                if j != i:
                    held = held | locks[j]
            eligible = reqs[i].valid & (reqs[i].we | free) & ~held
            m.d.comb += grant[i].eq(eligible & ~blocked)
            blocked = blocked | eligible

        # ---- request mux ----
        any_grant = Const(0)
        for g in grant:
            any_grant = any_grant | g
        m.d.comb += mem.req.valid.eq(any_grant)
        for i in range(n):
            with (m.If(grant[i]) if i == 0 else m.Elif(grant[i])):
                m.d.comb += [
                    mem.req.we.eq(reqs[i].we),
                    mem.req.byte_hi.eq(reqs[i].byte_hi),
                    mem.req.byte_lo.eq(reqs[i].byte_lo),
                    mem.req.addr.eq(reqs[i].addr),
                    mem.req.wdata.eq(reqs[i].wdata),
                    self.space.eq(spaces[i]),
                ]
        for i in range(n):
            m.d.comb += [
                reqs[i].ready.eq(grant[i] & mem.req.ready),
                getattr(self, f'fault{i}').eq(grant[i] & self.fault),
            ]

        # ---- who owns the outstanding read ----
        accept_read = Signal()
        m.d.comb += accept_read.eq(mem.req.valid & mem.req.ready & ~mem.req.we)
        with m.If(mem.resp.valid):
            m.d.sync += busy.eq(0)
        with m.If(accept_read):  # wins over the line above if both
            m.d.sync += busy.eq(1)
            for i in range(n):
                with m.If(grant[i]):
                    m.d.sync += owner.eq(i)

        # ---- response routing ----
        for i in range(n):
            m.d.comb += [
                resps[i].valid.eq(mem.resp.valid & (owner == i)),
                resps[i].data.eq(mem.resp.data),
            ]
        return m
