"""
Test for the system and user contexts of cpu.py: TRAP, RETI, exceptions, soft
reset, the context device, and the asynchronous fault of a write-back of the
stack cache.

hwtest/hw7_modes.asm, hw8_mmu.asm, hw9_irq.asm, hw10_swap.asm,
hw11_uart_irq.asm and hw12_muldiv.asm are run on the whole System (CPU,
arbiter, MMU, memory select, peripheral bus with the context device, the MMU
registers, the interrupt controller and the timer).
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def serve(port, store, rng, p_ready, uart=None, rx=None, irq=None):
    # with `uart` the port is the UART's: a write of word 0 is logged. The
    # reads give 0, or with `rx` (a list of the received bytes) DATA takes the
    # first of them and STATUS tells if there is one; `irq` is the interrupt
    # line of the receiver, which goes down at once when the last byte is read
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
                    data = 0
                    if rx is not None and addr == 0 and rx:
                        data = rx.pop(0)
                        ctx.set(irq, int(bool(rx)))
                    elif rx is not None and addr == 1:
                        data = 1 | bool(rx) << 1
                    pending.append([rng.randint(1, 4) - 1, data])
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
    with open(os.path.join(ROOT, 'hwtest', name)) as f:
        words, _ = assemble(f.read())
    return {i: w for i, w in enumerate(words)}


def run_program(name, seed, n_cycles, p_ready, received=()):
    # `received`: (cycle, byte) pairs that arrive at the UART
    rng = random.Random(seed)
    flash = assembled(name)
    ram = {}
    uart = []
    dut = System()
    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(serve(dut.flash, flash, rng, p_ready), background=True)
    sim.add_testbench(serve(dut.ram, ram, rng, p_ready), background=True)
    rx = []
    sim.add_testbench(
        serve(dut.uart, {}, rng, p_ready, uart, rx, dut.irq_ext),
        background=True,
    )
    seen = {'aborts': 0, 'swaps': 0}

    async def bench(ctx):
        for cycle in range(n_cycles):
            await ctx.tick()
            seen['aborts'] += ctx.get(dut.cpu.aborted)
            for at, byte in received:
                if at == cycle:
                    rx.append(byte)
                    ctx.set(dut.irq_ext, 1)

    # SWAP is atomic: a request of the RAM that is accepted while the CPU
    # holds the memory is a read, and the next one is the write to the same
    # address, with nothing else in between
    async def atomic(ctx):
        waiting = None
        while True:
            _, _, v, ready, we, addr, lock = await ctx.tick().sample(
                dut.ram.req.valid, dut.ram.req.ready, dut.ram.req.we,
                dut.ram.req.addr, dut.cpu.mem_lock)
            if not (v and ready):
                assert waiting is None or lock, 'the lock dropped too early'
                continue
            if waiting is not None:
                assert lock and we and addr == waiting, (
                    f'between the read and the write of a SWAP: {we} {addr}'
                )
                waiting = None
                seen['swaps'] += 1
            elif lock:
                assert not we, 'a write that starts a SWAP'
                waiting = addr

    sim.add_testbench(bench)
    sim.add_testbench(atomic, background=True)
    sim.run()
    return bytes(uart), seen


def test_cpu_modes():
    expected = b'ABCBCDEFGHSITJ'
    for seed, p_ready in ((1, 1.0), (2, 0.6), (3, 0.3)):
        out, seen = run_program('hw7_modes.asm', seed, 6000, p_ready)
        assert out == expected, (
            f'seed {seed}: the output is {out!r}, expected {expected!r}'
        )
        # the illegal 0xffff and RETI of the user,
        # the illegal 0xffff of the system
        assert seen['aborts'] == 3, seen
    # the MMU: pages mapped for the user, a page with no memory, the soft reset
    for seed, p_ready in ((1, 1.0), (2, 0.6), (3, 0.3)):
        out, seen = run_program('hw8_mmu.asm', seed, 8000, p_ready)
        assert out == b'ABCDEFG', f'seed {seed}: the output is {out!r}'
    # SWAP: words, bytes, the stack as memory, a lock; atomic on the RAM
    for seed, p_ready in ((1, 1.0), (2, 0.6), (3, 0.3)):
        out, seen = run_program('hw10_swap.asm', seed, 4000, p_ready)
        assert out == b'ABCDEFG', f'seed {seed}: the output is {out!r}'
        assert seen['swaps'] == 7, seen
    # MUL and DIV: results, flags, bytes, the overflow, a division by zero
    for seed, p_ready in ((1, 1.0), (2, 0.6), (3, 0.3)):
        out, seen = run_program('hw12_muldiv.asm', seed, 6000, p_ready)
        assert out == b'ABCDEFG', f'seed {seed}: the output is {out!r}'
    # the UART receiver interrupts: the bytes are echoed by the handler, a
    # '?' is answered; two bytes come at the same time
    received = [(4000, 97), (4000, 98), (6000, 99), (9000, 63), (9001, 120)]
    for seed, p_ready in ((1, 1.0), (2, 0.6), (3, 0.3)):
        out, seen = run_program(
            'hw11_uart_irq.asm', seed, 14000, p_ready, received
        )
        assert out == b'>abcYx', f'seed {seed}: the output is {out!r}'
    # the interrupts: the timer, the mask, between SKIP and what it skips
    for seed, p_ready in ((1, 1.0), (2, 0.6), (3, 0.3)):
        out, seen = run_program('hw9_irq.asm', seed, 60000, p_ready)
        assert out == b'ABCDE', f'seed {seed}: the output is {out!r}'
    print('ok')


if __name__ == '__main__':
    test_cpu_modes()
