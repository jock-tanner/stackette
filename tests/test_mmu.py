"""
Test for mmu.py: the translation, the registers as a device, byte enables, the
refusal that passes back, and the reset of the system array. All that is run
for both kinds of MMU (asynchronous and synchronous read ports), and the
latency of the synchronous one is tested separately.

The MMU is driven directly: the testbench is the initiator on `cpu` and on
`regs`, and the target on `mem` (always ready).

Run with `python test_mmu.py` (or pytest).
"""
import random

from amaranth.sim import Simulator

from memory_select import FLASH_PAGE, PERIPH_PAGE
from mmu import MMU, default_page


def run_case(seed, sync=False):
    rng = random.Random(seed)
    dut = MMU(sync=sync)
    req = dut.cpu.req
    out = dut.mem.req
    rreq, rresp = dut.regs.req, dut.regs.resp
    sim = Simulator(dut)
    sim.add_clock(1e-6)

    async def request(ctx, space, user, word_addr, we=0):
        """present a request for one cycle, return (translated address, ready, valid)"""
        ctx.set(dut.space, space)
        ctx.set(dut.user, user)
        ctx.set(req.addr, word_addr)
        ctx.set(req.we, we)
        ctx.set(req.valid, 1)
        ctx.set(out.ready, 1)
        await ctx.tick()
        result = (ctx.get(out.addr), ctx.get(req.ready), ctx.get(out.valid))
        ctx.set(req.valid, 0)
        return result

    async def write_reg(ctx, index, value, hi=1, lo=1):
        ctx.set(rreq.addr, index)
        ctx.set(rreq.wdata, value)
        ctx.set(rreq.byte_hi, hi)
        ctx.set(rreq.byte_lo, lo)
        ctx.set(rreq.we, 1)
        ctx.set(rreq.valid, 1)
        await ctx.tick()
        ctx.set(rreq.valid, 0)
        ctx.set(rreq.we, 0)

    async def read_reg(ctx, index):
        ctx.set(rreq.addr, index)
        ctx.set(rreq.we, 0)
        ctx.set(rreq.valid, 1)
        await ctx.tick()
        ctx.set(rreq.valid, 0)
        assert ctx.get(rresp.valid) == 1, 'no response of the register read'
        return ctx.get(rresp.data)

    async def bench(ctx):
        # ---- the default map, both arrays, every space and page ----
        assert default_page(0) == FLASH_PAGE and default_page(3 * 16 + 15) == PERIPH_PAGE
        for user in (0, 1):
            for space in range(4):
                for page in range(16):
                    offset = rng.randrange(0x800)
                    got, ready, valid = await request(ctx, space, user, page << 11 | offset)
                    want = default_page(space * 16 + page)
                    assert (got, ready, valid) == (want << 11 | offset, 1, 1), (
                        user, space, page, hex(got))
        # the registers read back the same
        for index in (0, 5, 15, 16 + 15, 3 * 16 + 15, 64, 64 + 3 * 16 + 15):
            assert await read_reg(ctx, index) == default_page(index % 64), index

        # ---- programming: only the array and set that were written change ----
        await write_reg(ctx, 2 * 16 + 5, 0x0123)                # system PSTACK page 5
        await write_reg(ctx, 64 + 3 * 16 + 5, 0x1fff)           # user DATA page 5
        got, _, _ = await request(ctx, 2, 0, 5 << 11 | 0x10)
        assert got == 0x0123 << 11 | 0x10, hex(got)
        got, _, _ = await request(ctx, 2, 1, 5 << 11 | 0x10)
        assert got == 5 << 11 | 0x10, hex(got)
        got, _, _ = await request(ctx, 3, 1, 5 << 11 | 0x10)
        assert got == 0x1fff << 11 | 0x10, hex(got)
        got, _, _ = await request(ctx, 3, 0, 5 << 11 | 0x10)
        assert got == 5 << 11 | 0x10, hex(got)
        assert await read_reg(ctx, 2 * 16 + 5) == 0x0123

        # ---- byte enables of a register write ----
        await write_reg(ctx, 7, 0xabcd, hi=0, lo=1)
        assert await read_reg(ctx, 7) == (default_page(7) & 0xff00) | 0xcd
        await write_reg(ctx, 7, 0xabcd, hi=1, lo=0)
        assert await read_reg(ctx, 7) == 0xabcd

        # ---- the request passes its fields on, and the refusal comes back ----
        ctx.set(req.wdata, 0x5a5a)
        ctx.set(req.byte_hi, 1)
        ctx.set(req.byte_lo, 0)
        ctx.set(req.we, 1)
        ctx.set(req.addr, 0x123)
        ctx.set(req.valid, 1)
        ctx.set(out.ready, 0)
        ctx.set(dut.mem_fault, 1)
        await ctx.tick()
        assert ctx.get(out.wdata) == 0x5a5a and ctx.get(out.we) == 1
        assert ctx.get(out.byte_hi) == 1 and ctx.get(out.byte_lo) == 0
        assert ctx.get(dut.fault) == 1 and ctx.get(req.ready) == 0
        ctx.set(dut.mem_fault, 0)
        ctx.set(req.valid, 0)
        await ctx.tick()
        assert ctx.get(dut.fault) == 0

        # ---- the reset of the system array: 64 cycles, the user array stays ----
        await write_reg(ctx, 3 * 16 + 6, 0x0007)                # system DATA page 6
        await write_reg(ctx, 64 + 3 * 16 + 6, 0x0009)           # user DATA page 6
        ctx.set(dut.reset_system, 1)
        await ctx.tick()
        ctx.set(dut.reset_system, 0)
        waited = 0
        ctx.set(dut.space, 3)
        ctx.set(dut.user, 1)
        ctx.set(req.addr, 6 << 11)
        ctx.set(req.we, 0)
        ctx.set(req.valid, 1)
        ctx.set(out.ready, 1)
        for _ in range(200):
            await ctx.tick()
            if ctx.get(req.ready):
                break
            assert ctx.get(out.valid) == 0
            waited += 1
        else:
            raise AssertionError('the reset never ended')
        assert 60 <= waited <= 70, waited
        # the first request that got through sees the user map, as it was
        assert ctx.get(out.addr) == 0x0009 << 11
        ctx.set(req.valid, 0)
        assert await read_reg(ctx, 3 * 16 + 6) == 6             # back to the default
        assert await read_reg(ctx, 2 * 16 + 5) == 5
        assert await read_reg(ctx, 64 + 3 * 16 + 6) == 0x0009   # not touched
        assert await read_reg(ctx, 64 + 3 * 16 + 5) == 0x1fff

    sim.add_testbench(bench)
    sim.run()


def run_latency():
    """The synchronous MMU: when a request goes through, cycle by cycle."""
    dut = MMU(sync=True)
    req = dut.cpu.req
    out = dut.mem.req
    rreq = dut.regs.req
    sim = Simulator(dut)
    sim.add_clock(1e-6)

    async def cycle(ctx):
        _, _, ready, valid, addr, fault = await ctx.tick().sample(
            req.ready, out.valid, out.addr, dut.fault
        )
        return ready, valid, addr, fault

    def present(ctx, page, space=3, user=0):
        ctx.set(dut.space, space)
        ctx.set(dut.user, user)
        ctx.set(req.addr, page << 11 | 0x15)
        ctx.set(req.we, 0)
        ctx.set(req.valid, 1)
        ctx.set(out.ready, 1)

    async def bench(ctx):
        # a request for a page that was not looked up: nothing goes out in
        # the first cycle, the request goes through in the second
        present(ctx, 3)
        ready, valid, _, _ = await cycle(ctx)
        assert (ready, valid) == (0, 0), 'no lookup cycle'
        ready, valid, addr, _ = await cycle(ctx)
        assert (ready, valid, addr) == (1, 1, 3 << 11 | 0x15)
        # the next request for the same page can go at once, a request
        # for another page has to wait for its lookup
        ready, valid, addr, _ = await cycle(ctx)
        assert (ready, valid, addr) == (1, 1, 3 << 11 | 0x15)
        present(ctx, 4)
        ready, valid, _, _ = await cycle(ctx)
        assert (ready, valid) == (0, 0)
        ready, valid, addr, _ = await cycle(ctx)
        assert (ready, valid, addr) == (1, 1, 4 << 11 | 0x15)
        # the space and the user flag make another register as well
        present(ctx, 4, space=2)
        ready, valid, _, _ = await cycle(ctx)
        assert (ready, valid) == (0, 0)
        present(ctx, 4, space=3, user=1)
        ready, valid, _, _ = await cycle(ctx)
        assert (ready, valid) == (0, 0)
        ready, valid, addr, _ = await cycle(ctx)
        assert (ready, valid, addr) == (1, 1, 4 << 11 | 0x15)

        # a target that is not ready: the request is held, nothing is looked
        # up again
        present(ctx, 5)
        ctx.set(out.ready, 0)
        await cycle(ctx)
        for _ in range(3):
            ready, valid, addr, _ = await cycle(ctx)
            assert (ready, valid, addr) == (0, 1, 5 << 11 | 0x15)
        ctx.set(out.ready, 1)
        ready, valid, _, _ = await cycle(ctx)
        assert (ready, valid) == (1, 1)

        # MemorySelect's refusal comes in the second cycle, with the request
        present(ctx, 6)
        await cycle(ctx)
        ctx.set(out.ready, 0)
        ctx.set(dut.mem_fault, 1)
        ready, valid, _, fault = await cycle(ctx)
        assert (ready, valid, fault) == (0, 1, 1)
        ctx.set(dut.mem_fault, 0)
        ctx.set(out.ready, 1)
        ctx.set(req.valid, 0)
        await cycle(ctx)

        # a register that is written while its page is being looked up: the
        # old translation is not used
        present(ctx, 7)
        ctx.set(rreq.addr, 3 * 16 + 7)
        ctx.set(rreq.wdata, 0x0abc)
        ctx.set(rreq.byte_hi, 1)
        ctx.set(rreq.byte_lo, 1)
        ctx.set(rreq.we, 1)
        ctx.set(rreq.valid, 1)
        ready, valid, _, _ = await cycle(ctx)
        assert (ready, valid) == (0, 0)
        ctx.set(rreq.valid, 0)
        ready, valid, _, _ = await cycle(ctx)
        assert (ready, valid) == (0, 0), 'the old lookup was used'
        ready, valid, addr, _ = await cycle(ctx)
        assert (ready, valid, addr) == (1, 1, 0x0abc << 11 | 0x15)

    sim.add_testbench(bench)
    sim.run()


def test_mmu():
    for seed in range(3):
        run_case(seed)
    print('ok')


def test_mmu_sync():
    for seed in range(3):
        run_case(seed, sync=True)
    run_latency()
    print('ok')


if __name__ == '__main__':
    test_mmu()
    test_mmu_sync()
