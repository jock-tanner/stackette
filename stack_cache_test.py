"""
Randomized test for stack_cache.py against a stack in flat memory.

The reference (Ref) is the architecture itself: a stack in ordinary memory,
item k at PSP + 2*k, with every command applied directly to that memory. The
cache is driven with random commands -- mostly back to back, so commands meet a
pending write-buffer entry -- against a slow memory (random `ready`, 1..4
cycles read latency). It must show:

  * every operand named in `need` is valid and equals the reference at the
    cycle the command executes,
  * whenever the cache is idle: PSP matches, every valid line matches, and
    wherever a line is invalid (or beyond the window) memory already holds the
    reference value,
  * after a final FLUSH the stack area of memory equals the reference.

Reads below PSP are never checked: a popped value is dead by definition.
Run with `python test_stack_cache.py` (or pytest).
"""
import random

from amaranth.sim import Simulator

from stack_cache import Maint, Shift, StackCache

BASE = 0x8000


def initial(byte_addr):
    """Deterministic junk for memory nobody has written yet."""
    return (byte_addr * 2654435761 >> 7) & 0xFFFF


class Ref:
    """The stack in flat memory (byte addresses)."""
    def __init__(self, psp):
        self.psp = psp
        self.mem = {}

    def rd(self, a):
        return self.mem.get(a, initial(a))

    def item(self, k):
        return self.rd(self.psp + 2 * k)

    def apply(self, shift, writes, arg):
        # writes use pre-shift numbering
        for pos, val in writes:
            self.mem[self.psp + 2 * pos] = val
        if shift == Shift.PUSH:
            self.psp -= 2
            self.mem[self.psp] = arg
        elif shift == Shift.PUSH_UNDER:
            top = self.item(0)
            self.psp -= 2
            self.mem[self.psp] = top
            self.mem[self.psp + 2] = arg
        elif shift == Shift.POP:
            self.psp += 2
        elif shift == Shift.POP_UNDER:
            top = self.item(0)
            self.psp += 2
            self.mem[self.psp] = top


def run_case(seed, p_ready, n_commands):
    rng = random.Random(seed)
    dut = StackCache()
    cmd = dut.cmd
    req, resp = dut.mem.req, dut.mem.resp
    store = {}  # word address -> value

    def mem_rd(word_addr):
        return store.get(word_addr, initial(2 * word_addr))

    sim = Simulator(dut)
    sim.add_clock(1e-6)

    async def memory(ctx):
        """Slow memory: random ready, 1..4 cycles read latency, in order."""
        pending = []  # [cycles left, data]
        ready_now = True
        ctx.set(req.ready, 1)
        while True:
            _, _, valid, we, addr, wdata = await ctx.tick().sample(
                req.valid, req.we, req.addr, req.wdata
            )
            for item in pending:
                item[0] -= 1
            if valid and ready_now:
                # accepted on this edge
                if we:
                    store[addr] = wdata
                else:
                    pending.append([rng.randrange(1, 5) - 1, mem_rd(addr)])
            ready_now = rng.random() < p_ready
            ctx.set(req.ready, ready_now)
            due = [item for item in pending if item[0] <= 0]
            if due:
                pending.remove(due[0])
                ctx.set(resp.valid, 1)
                ctx.set(resp.data, due[0][1])
            else:
                ctx.set(resp.valid, 0)

    sim.add_testbench(memory, background=True)

    # command helpers
    async def issue(ctx, **fields):
        """
        Present a command until accepted. Returns the lines as the command
        saw them on the executing edge.
        """
        values = dict(
            maint=Maint.NONE.value, shift=Shift.NONE.value, need=0, arg=0,
            w0_en=0, w0_pos=0, w0_data=0, w1_en=0, w1_pos=0, w1_data=0
        )
        values.update(fields)
        for name, value in values.items():
            ctx.set(getattr(cmd, name), value)
        ctx.set(cmd.valid, 1)
        for _ in range(2000):
            _, _, ready, d0, d1, d2, d3, lv = await ctx.tick().sample(
                cmd.ready, dut.d0, dut.d1, dut.d2, dut.d3, dut.line_valid
            )
            if ready:
                break
        else:
            raise AssertionError('Command never accepted: deadlock.')
        ctx.set(cmd.valid, 0)
        return [d0, d1, d2, d3], lv

    async def wait_idle(ctx):
        for _ in range(2000):
            _, _, idle = await ctx.tick().sample(dut.idle)
            if idle:
                return
        raise AssertionError('Cache never became idle.')

    def check_quiet(ctx, ref, where):
        """Cache idle: its state must agree with the reference."""
        assert ctx.get(dut.psp) == ref.psp, (where, 'psp')
        lv = ctx.get(dut.line_valid)
        lines = [
            ctx.get(dut.d0), ctx.get(dut.d1), ctx.get(dut.d2), ctx.get(dut.d3),
        ]
        for k in range(4):
            if (lv >> k) & 1:
                assert lines[k] == ref.item(k), (where, 'line', k)
            else:
                assert mem_rd((ref.psp >> 1) + k) == ref.item(k), (
                    where, 'memory under invalid line', k
                )
        for k in range(4, 12):
            assert mem_rd((ref.psp >> 1) + k) == ref.item(k), (
                where, 'beyond the window', k
            )

    async def bench(ctx):
        ref = Ref(BASE)
        # power up: PSP is only ever changed through the command interface
        await issue(ctx, maint=Maint.SET_PSP.value, arg=BASE)
        await wait_idle(ctx)
        check_quiet(ctx, ref, 'power-up')

        for step in range(n_commands):
            if rng.random() < 0.05:
                kind = rng.choice([
                    'FLUSH',
                    'FLUSH_INVALIDATE',
                    'SET_PSP',
                    'FLUSH_THEN_INVALIDATE',
                ])
                if kind == 'FLUSH_THEN_INVALIDATE':
                    # INVALIDATE drops data: only after a flush
                    await issue(ctx, maint=Maint.FLUSH.value)
                    await wait_idle(ctx)
                    await issue(ctx, maint=Maint.INVALIDATE.value)
                elif kind == 'SET_PSP':
                    # never expose popped (dead) memory
                    up = rng.randrange(0, 3)
                    await issue(
                        ctx,
                        maint=Maint.SET_PSP.value,
                        arg=(ref.psp + 2 * up) & 0xFFFF
                    )
                    ref.psp += 2 * up
                else:
                    await issue(ctx, maint=Maint[kind].value)
            else:
                depth = (BASE - ref.psp) // 2
                options = [Shift.NONE, Shift.PUSH, Shift.PUSH_UNDER]
                if depth > -40:
                    options += [
                        Shift.POP, Shift.POP_UNDER, Shift.POP, Shift.POP_UNDER
                    ]
                if depth > 60:
                    options = [Shift.POP, Shift.POP_UNDER, Shift.NONE]
                shift = rng.choice(options)
                need = rng.randrange(16)
                positions = rng.sample(range(4), rng.randrange(0, 3))
                writes = [(p, rng.getrandbits(16)) for p in positions]
                arg = rng.getrandbits(16)
                fields = dict(shift=shift.value, need=need, arg=arg)
                for i, (pos, val) in enumerate(writes):
                    fields.update({
                        f'w{i}_en': 1, f'w{i}_pos': pos, f'w{i}_data': val,
                    })
                lines, lv = await issue(ctx, **fields)
                for k in range(4):
                    if (need >> k) & 1:
                        assert (lv >> k) & 1, (seed, step, 'operand not valid', k)
                        assert lines[k] == ref.item(k), (
                            seed, step, shift, need, 'operand', k
                        )
                ref.apply(shift, writes, arg)
            if rng.random() < 0.15:
                # sometimes let everything settle and compare
                await wait_idle(ctx)
                check_quiet(ctx, ref, (seed, step))

        await issue(ctx, maint=Maint.FLUSH.value)
        await wait_idle(ctx)
        check_quiet(ctx, ref, (seed, 'final'))
        for k in range(200):
            assert mem_rd((ref.psp >> 1) + k) == ref.item(k), (
                seed, 'final memory', k
            )

    sim.add_testbench(bench)
    sim.run()


def test_stack_cache():
    for seed, p_ready in enumerate([1.0, 0.6, 0.3, 0.8]):
        run_case(seed, p_ready, n_commands=1500)
    print('ok')


if __name__ == '__main__':
    test_stack_cache()
