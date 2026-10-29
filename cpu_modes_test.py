"""
Test for the system and user contexts of cpu.py: TRAP, RETI, exceptions, soft
reset, the context device, and the asynchronous fault of a write-back of the
stack cache.

hwtest/hw7_modes.asm and hw8_mmu.asm are run on the whole System (CPU, arbiter, MMU,
memory select, peripheral bus with the context device and the MMU registers).
The flash (holding the program) and the SDRAM are behavioral models with
random ready and 1..4 cycles of read latency; the UART is a register that
logs what is written to it. The test expects the programs' output.

Run with `python test_cpu_modes.py` (or pytest). Needs asm.py and the
assembled programs: it assembles them itself.
"""
import os
import random

from amaranth.sim import Simulator

from asm.asm import assemble
from system import System

HERE = os.path.dirname(os.path.abspath(__file__))


def serve(port, store, rng, p_ready, uart=None):
    # with `uart` the port is the UART's: a write of word 0 is logged, reads give 0
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
            if v and ready_now:
                if we and uart is not None and addr == 0:
                    uart.append(wd & 0xff)
                elif we:
                    cur = store.get(addr, 0)
                    if bl:
                        cur = (cur & 0xff00) | (wd & 0x00ff)
                    if bh:
                        cur = (cur & 0x00ff) | (wd & 0xff00)
                    store[addr] = cur
                elif uart is not None:
                    pending.append([rng.randint(1, 4) - 1, 0])
                else:
                    pending.append([rng.randint(1, 4) - 1, store.get(addr, 0)])
            ready_now = rng.random() < p_ready
            ctx.set(port.req.ready, ready_now)
            if pending and pending[0][0] <= 0:
                head = pending.pop(0)
                ctx.set(port.resp.valid, 1)
                ctx.set(port.resp.data, head[1])
            else:
                ctx.set(port.resp.valid, 0)
    return target


def assembled(name):
    with open(os.path.join(HERE, 'hwtest', name)) as f:
        words, _ = assemble(f.read())
    return {i: w for i, w in enumerate(words)}


def run_program(name, seed, n_cycles, p_ready):
    rng = random.Random(seed)
    flash = assembled(name)
    ram = {}
    uart = []
    dut = System(crawl=0)
    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(serve(dut.flash, flash, rng, p_ready), background=True)
    sim.add_testbench(serve(dut.ram, ram, rng, p_ready), background=True)
    sim.add_testbench(serve(dut.uart, {}, rng, p_ready, uart), background=True)
    seen = {'aborts': 0}

    async def bench(ctx):
        for _ in range(n_cycles):
            await ctx.tick()
            seen['aborts'] += ctx.get(dut.cpu.aborted)

    sim.add_testbench(bench)
    sim.run()
    return bytes(uart), seen


def test_cpu_modes():
    expected = b'ABCBCDEFGHSITJ'
    for seed, p_ready in ((1, 1.0), (2, 0.6), (3, 0.3)):
        out, seen = run_program('hw7_modes.asm', seed, 6000, p_ready)
        assert out == expected, f'seed {seed}: the output is {out!r}, expected {expected!r}'
        # the illegal 0xffff and RETI of the user, the illegal 0xffff of the system
        assert seen['aborts'] == 3, seen
    # the MMU: pages mapped for the user, a page with no memory, the soft reset
    for seed, p_ready in ((1, 1.0), (2, 0.6), (3, 0.3)):
        out, seen = run_program('hw8_mmu.asm', seed, 8000, p_ready)
        assert out == b'ABCDEFG', f'seed {seed}: the output is {out!r}'
    print('ok')


if __name__ == '__main__':
    test_cpu_modes()
