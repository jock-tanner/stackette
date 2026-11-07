"""
Test for the system and user contexts of cpu.py: TRAP, RETI, exceptions, soft
reset, the context device, and the asynchronous fault of a write-back of the
stack cache.

hwtest/hw7_modes.asm, hw8_mmu.asm, hw9_irq.asm, hw10_swap.asm,
hw11_uart_irq.asm, hw12_muldiv.asm and hw13_lea_ptr.asm are run on the whole
System (CPU, arbiter, MMU, memory select, peripheral bus with the context device, the MMU
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
import pytest

from asm.asm import assemble
from system import System

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECEIVED = [(4000, 97), (4000, 98), (6000, 99), (9000, 63), (9001, 120)]


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


def run_program(
    name, seed, n_cycles, p_ready, expected, received=(), mmu_sync=False
):
    # `received`: (cycle, byte) pairs that arrive at the UART
    rng = random.Random(seed)
    flash = assembled(name)
    ram = {}
    uart = []
    dut = System(mmu_sync=mmu_sync)
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
        left = None
        for cycle in range(n_cycles):
            await ctx.tick()
            seen['aborts'] += ctx.get(dut.cpu.aborted)
            for at, byte in received:
                if at == cycle:
                    rx.append(byte)
                    ctx.set(dut.irq_ext, 1)
            if expected is not None and len(uart) >= len(expected):
                left = 200 if left is None else left - 1
                if not left:
                    break

    # SWAP is atomic: the requests of the RAM that are accepted while the CPU
    # holds the memory are the read and the write of the same address, or, for
    # a word at an odd address, the reads of both bytes and then their writes,
    # with nothing else in between
    async def atomic(ctx):
        held = []
        while True:
            _, _, v, ready, we, addr, lock = await ctx.tick().sample(
                dut.ram.req.valid, dut.ram.req.ready, dut.ram.req.we,
                dut.ram.req.addr, dut.cpu.mem_lock)
            if v and ready and lock:
                held.append((we, addr))
            elif held and not lock:
                a = held[0][1]
                assert held in (
                    [(0, a), (1, a)],
                    [(0, a), (0, a + 1), (1, a), (1, a + 1)],
                ), f'the requests of a SWAP: {held}'
                held = []
                seen['swaps'] += 1


    sim.add_testbench(bench)
    sim.add_testbench(atomic, background=True)
    sim.run()
    return bytes(uart), seen


@pytest.mark.parametrize('name, expected, cycles, received', [
    ('hw7_modes.asm', b'ABCBCDEFGHSITJ', 12000, ()),
    ('hw8_mmu.asm', b'ABCDEFG', 16000, ()),
    ('hw10_swap.asm', b'ABCDEFG', 8000, ()),
    ('hw12_muldiv.asm', b'ABCDEFG', 12000, ()),
    ('hw13_lea_ptr.asm', b'ABCDEFGHI', 16000, ()),
    ('hw11_uart_irq.asm', b'>abcYx', 28000, RECEIVED),
    ('hw9_irq.asm', b'ABCDE', 120000, ()),
    ('hw14_operands.asm', b'ABCDEFGHIJ', 20000, ()),
    ('hw15_cmp_tst.asm', b'ABCDEFGH', 12000, ()),
    ('hw16_shift.asm', b'ABCDEFGH', 20000, ()),
    ('hw17_variadic.asm', b'ABCDEFG', 24000, ()),
    ('hw18_kernel.asm', b'ABCDEFTGHZ', 120000, ()),
    ('hw19_ipc.asm', b'ABCDEFGHIJKZ', 160000, ()),
    ('hw20_timer.asm', b'ACBTTTSRZ', 120000, ()),
    ('hw21_setjmp.asm', b'ABCDEFGHZ', 60000, ()),
    ('hw22_unaligned.asm', b'ABCDEFGH', 12000, ()),
])
@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.5)])
def test_sync_mmu(name, expected, cycles, received, seed, p_ready):
    # the MMU with synchronous read ports: one more clock for a request
    out, seen = run_program(
        name, seed, cycles, p_ready, expected, received, mmu_sync=True
    )
    assert out == expected, (
        f'{name}, synchronous MMU, seed {seed}: the output is {out!r}'
    )


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_cpu_modes(seed, p_ready):
    expected = b'ABCBCDEFGHSITJ'
    out, seen = run_program('hw7_modes.asm', seed, 6000, p_ready, expected)
    assert out == expected, (
        f'seed {seed}: the output is {out!r}, expected {expected!r}'
    )
    # the illegal 0xffff and RETI of the user,
    # the illegal 0xffff of the system
    assert seen['aborts'] == 3, seen


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_mmu(seed, p_ready):
    expected = b'ABCDEFG'
    # the MMU: pages mapped for the user, a page with no memory, the soft reset
    out, seen = run_program('hw8_mmu.asm', seed, 8000, p_ready, expected)
    assert out == expected, f'seed {seed}: the output is {out!r}'


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_swap(seed, p_ready):
    expected = b'ABCDEFG'
    # SWAP: words, bytes, the stack as memory, a lock; atomic on the RAM
    out, seen = run_program('hw10_swap.asm', seed, 4000, p_ready, expected)
    assert out == expected, f'seed {seed}: the output is {out!r}'
    assert seen['swaps'] == 7, seen


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_muldiv(seed, p_ready):
    expected = b'ABCDEFG'
    # MUL and DIV: results, flags, bytes, the overflow, a division by zero
    out, seen = run_program('hw12_muldiv.asm', seed, 6000, p_ready, expected)
    assert out == expected, f'seed {seed}: the output is {out!r}'


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_lea(seed, p_ready):
    expected = b'ABCDEFGHI'
    # LEA and [tos]: addresses of frame items, pointers on the stack
    out, seen = run_program('hw13_lea_ptr.asm', seed, 8000, p_ready, expected)
    assert out == expected, f'seed {seed}: the output is {out!r}'


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_uart_irq(seed, p_ready):
    expected = b'>abcYx'
    # the UART receiver interrupts: the bytes are echoed by the handler, a
    # '?' is answered; two bytes come at the same time
    out, seen = run_program(
        'hw11_uart_irq.asm', seed, 14000, p_ready, expected, RECEIVED
    )
    assert out == expected, f'seed {seed}: the output is {out!r}'


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_irqs(seed, p_ready):
    expected = b'ABCDE'
    # the interrupts: the timer, the mask, between SKIP and what it skips
    out, seen = run_program('hw9_irq.asm', seed, 60000, p_ready, expected)
    assert out == expected, f'seed {seed}: the output is {out!r}'


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_operands(seed, p_ready):
    expected = b'ABCDEFGHIJ'
    # ld.bs from [abs], [tos], [sp+k]; [sp+31]; e of a swap; illegal codes
    out, seen = run_program('hw14_operands.asm', seed, 20000, p_ready, expected)
    assert out == expected, f'seed {seed}: the output is {out!r}'


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_cmp_tst(seed, p_ready):
    # CMP and TST: the item compared, pop and under, flags, bytes, memory
    # operands
    expected = b'ABCDEFGH'
    out, seen = run_program('hw15_cmp_tst.asm', seed, 6000, p_ready, expected)
    assert out == expected, f'seed {seed}: the output is {out!r}'


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_shifts(seed, p_ready):
    # arithmetic and logical right shifts
    expected = b'ABCDEFGH'
    out, seen = run_program('hw16_shift.asm', seed, 20000, p_ready, expected)
    assert out == expected, f'seed {seed}: the output is {out!r}'


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_variadic(seed, p_ready):
    # variadic calls: the count word, the result through a pointer, the
    # variadic RET, a pad word for a long result, nested calls
    expected = b'ABCDEFG'
    out, seen = run_program('hw17_variadic.asm', seed, 12000, p_ready, expected)
    assert out == b'ABCDEFG', f'seed {seed}: the output is {out!r}'


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_kernel(seed, p_ready):
    # creating and running user processes with different memory maps
    expected = b'ABCDEFTGHZ'
    out, seen = run_program('hw18_kernel.asm', seed, 60000, p_ready, expected)
    assert out == expected, f'seed {seed}: the output is {out!r}'


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_ipc(seed, p_ready):
    # passing messages between system and user modes
    expected = b'ABCDEFGHIJKZ'
    out, seen = run_program('hw19_ipc.asm', seed, 80000, p_ready, expected)
    assert out == expected, f'seed {seed}: the output is {out!r}'


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_timer(seed, p_ready):
    # test system timer interrupts
    expected = b'ACBTTTSRZ'
    out, seen = run_program('hw20_timer.asm', seed, 80000, p_ready, expected)
    assert out == expected, f'seed {seed}: the output is {out!r}'


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_setjmp_longjmp(seed, p_ready):
    # setjmp/longjmp syscall
    expected = b'ABCDEFGHZ'
    out, seen = run_program('hw21_setjmp.asm', seed, 60000, p_ready, expected)
    assert out == expected, f'seed {seed}: the output is {out!r}'


@pytest.mark.parametrize('seed, p_ready', [(1, 1.0), (2, 0.6), (3, 0.3)])
def test_unaligned(seed, p_ready):
    # unaligned word memory access
    expected = b'ABCDEFGH'
    out, seen = run_program('hw22_unaligned.asm', seed, 8000, p_ready, expected)
    assert out == expected, f'seed {seed}: the output is {out!r}'
