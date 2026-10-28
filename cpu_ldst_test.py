"""
Randomized test for cpu.py: LD, ST, the ALU and the control flow instructions (calls, returns,
jumps, SKIP, ADDP).

A random program (cpu_ldst_model.Program) is put into code memory at address 0
and the CPU runs it. Both of the CPU's memory ports (code/data `mem`, and the
stack cache's `stack_mem`) are served by behavioral memories sharing one store:
random ready, 1..4 cycles read latency, in order, byte enables honored.

The program ends by dumping MBP, MIDX and the whole parameter stack to a result
area with ST instructions, so the stack and both registers are compared through
memory. When the CPU asks for the word after the program, the test stops
serving the code port, lets the stack cache drain, then compares the memory
ranges that cpu_ldst_model.compare_ranges names, and the flags, with the
reference interpreter.

Run with `python test_cpu_ldst.py` (or pytest). Needs cpu_ldst_model.py.
"""
import random

from amaranth.sim import Simulator

from cpu import CPU
from cpu_ldst_model import Program, compare_ranges, junk


def run_case(seed, n_instr=150):
    rng = random.Random(seed)
    prog = Program(random.Random(seed + 1000)).build(n_instr)
    end_word = prog.end >> 1
    store = {i: w for i, w in enumerate(prog.words)}
    state = {'halted': False}
    dut = CPU(crawl=0)
    sim = Simulator(dut)
    sim.add_clock(1e-6)

    def serve(port, p_ready, is_cpu):
        async def target(ctx):
            pending = []
            ready_now = True
            ctx.set(port.req.ready, 1)
            while True:
                _, _, v, we, bh, bl, addr, wd = await ctx.tick().sample(
                    port.req.valid, port.req.we, port.req.byte_hi,
                    port.req.byte_lo, port.req.addr, port.req.wdata)
                for item in pending:
                    item[0] -= 1
                if is_cpu and v and not we and addr == end_word:
                    state['halted'] = True
                if v and ready_now and not (is_cpu and state['halted']):
                    if we:
                        cur = store.get(addr, junk(2 * addr))
                        if bl:
                            cur = (cur & 0xff00) | (wd & 0x00ff)
                        if bh:
                            cur = (cur & 0x00ff) | (wd & 0xff00)
                        store[addr] = cur
                    else:
                        pending.append([rng.randint(1, 4) - 1, store.get(addr, junk(2 * addr))])
                ready_now = rng.random() < p_ready and not (is_cpu and state['halted'])
                ctx.set(port.req.ready, ready_now)
                if pending and pending[0][0] <= 0:
                    head = pending.pop(0)
                    ctx.set(port.resp.valid, 1)
                    ctx.set(port.resp.data, head[1])
                else:
                    ctx.set(port.resp.valid, 0)
        return target

    sim.add_testbench(serve(dut.mem, rng.choice([1.0, 0.7, 0.4]), True), background=True)
    sim.add_testbench(serve(dut.stack_mem, rng.choice([1.0, 0.7, 0.4]), False), background=True)

    async def bench(ctx):
        for _ in range(400000):
            if state['halted']:
                break
            await ctx.tick()
        else:
            raise AssertionError(f'seed {seed}: the program never finished')
        for _ in range(300):                # let the stack cache drain
            await ctx.tick()
        flags = ctx.get(dut.sw) & 0x1f
        assert flags == prog.ref.flags, f'seed {seed}: flags {flags:05b}, expected {prog.ref.flags:05b}'
        assert (ctx.get(dut.sfp), ctx.get(dut.rsp)) == (prog.ref.sfp, prog.ref.rsp), (
            f'seed {seed}: SFP and RSP are {ctx.get(dut.sfp):#06x}, {ctx.get(dut.rsp):#06x}')
        for lo, hi in compare_ranges(prog):
            for a in range(lo, hi):
                got = store.get(a, junk(2 * a))
                want = prog.ref.w.get(a, junk(2 * a))
                assert got == want, (f'seed {seed}: word at {2 * a:#06x} is {got:#06x}, '
                                     f'expected {want:#06x}')

    sim.add_testbench(bench)
    sim.run()


def test_cpu_ldst():
    for seed in range(6):
        run_case(seed)
    print('ok')


if __name__ == '__main__':
    test_cpu_ldst()
