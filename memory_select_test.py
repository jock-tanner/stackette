"""
Randomized test for memory_select.py and memory_select_pipelined.py.

Three fake targets with different latencies and random `ready` sit behind the
selector. A random initiator drives it. Checked:

  * every request reaches exactly the target the memory map names, with the
    address cut/extended as documented and we / byte enables / wdata intact
    (each target logs what it saw; compared with what was expected),
  * writes to flash never reach the flash target,
  * read responses come back in request order with the right data, even though
    the targets answer at different speeds.
Two traffic styles, one checker:
  * "one read at a time": the initiator waits for each read's response before
    issuing the next read (writes go whenever). Both modules must pass.
  * "pipelined": reads are issued back to back, whatever the target. Only the
    pipelined module must pass; the plain one is not meant for this.
Run with `python test_memory_select.py` (or pytest).
"""
import random
from collections import deque

from amaranth import Module, Signal
from amaranth.sim import Simulator

from memory_select import MemorySelect, MemorySelectPipelined

FLASH, RAM, PERIPH = 0, 1, 2
LATENCY = {FLASH: (1, 6), RAM: (1, 3), PERIPH: (1, 2)}
P_READY = {FLASH: 0.5, RAM: 0.8, PERIPH: 0.9}
PERIPH_MASK = 0b11  # UART_ADDR_WIDTH = 2


def junk(tag, addr):
    return ((tag + 1) * 0x1111 ^ addr * 0x35) & 0xFFFF


def route(space, addr):
    """-> (target, address as the target sees it)"""
    if addr < 0x800:
        return FLASH, addr
    if space == 3 and addr >> 11 == 0xF:  # DATA space, top page
        return PERIPH, addr & PERIPH_MASK
    return RAM, addr


def run_case(module, seed, one_read_at_a_time, n_requests=3000):
    rng = random.Random(seed)
    dut = module()
    cpu, resp = dut.cpu.req, dut.cpu.resp
    ports = {FLASH: dut.flash, RAM: dut.ram, PERIPH: dut.periph}
    store = {t: {} for t in ports}  # what each fake target holds
    seen = {t: [] for t in ports}  # what each target was asked
    expected_seen = {t: [] for t in ports}
    ref = {t: {} for t in ports}
    expected_resp = deque()

    # The plain MemorySelect is purely combinational and has no "sync" domain,
    # which the simulator needs for add_clock() and ctx.tick(): give it one
    # through a top level with a dummy register.
    top = Module()
    top.submodules.dut = dut
    dummy = Signal()
    top.d.sync += dummy.eq(~dummy)

    sim = Simulator(top)
    sim.add_clock(1e-6)

    def make_target(tag):
        port = ports[tag]
        lo, hi = LATENCY[tag]

        async def target(ctx):
            pending = []  # [cycles left, data]
            ready_now = True
            ctx.set(port.req.ready, 1)
            while True:
                _, _, v, we, bh, bl, addr, wd = await ctx.tick().sample(
                    port.req.valid, port.req.we, port.req.byte_hi,
                    port.req.byte_lo, port.req.addr, port.req.wdata)
                for item in pending:
                    item[0] -= 1
                if v and ready_now:
                    seen[tag].append((we, bh, bl, addr, wd if we else None))
                    if we:
                        store[tag][addr] = wd
                    else:
                        data = store[tag].get(addr, junk(tag, addr))
                        pending.append([rng.randint(lo, hi) - 1, data])
                ready_now = rng.random() < P_READY[tag]
                ctx.set(port.req.ready, ready_now)
                if pending and pending[0][0] <= 0:  # in order: head only
                    head = pending.pop(0)
                    ctx.set(port.resp.valid, 1)
                    ctx.set(port.resp.data, head[1])
                else:
                    ctx.set(port.resp.valid, 0)
        return target

    for tag in ports:
        sim.add_testbench(make_target(tag), background=True)

    async def collector(ctx):
        while True:
            _, _, v, d = await ctx.tick().sample(resp.valid, resp.data)
            if v:
                assert expected_resp, 'response without a request'
                want = expected_resp.popleft()
                assert d == want, f'response {d:#06x}, expected {want:#06x}'

    sim.add_testbench(collector, background=True)

    async def bench(ctx):
        for _ in range(n_requests):
            while rng.random() < 0.3:  # idle gap
                ctx.set(cpu.valid, 0)
                await ctx.tick()
            we = rng.random() < 0.3
            if one_read_at_a_time and not we:
                ctx.set(cpu.valid, 0)
                for _ in range(5000):  # previous reads answered?
                    if not expected_resp:
                        break
                    await ctx.tick()
                else:
                    raise AssertionError("response never arrived")
            space = rng.randrange(4)
            addr = rng.randrange(0x8000)
            if rng.random() < 0.3:
                addr = rng.randrange(0x800)  # keep flash busy
            elif rng.random() < 0.3:
                addr = 0x7800 + rng.randrange(0x800)  # the top page
            bh, bl = rng.getrandbits(1), rng.getrandbits(1)
            wd = rng.getrandbits(16)
            ctx.set(dut.space, space)
            ctx.set(cpu.we, we)
            ctx.set(cpu.byte_hi, bh)
            ctx.set(cpu.byte_lo, bl)
            ctx.set(cpu.addr, addr)
            ctx.set(cpu.wdata, wd)
            ctx.set(cpu.valid, 1)
            for _ in range(5000):
                _, _, ready = await ctx.tick().sample(cpu.ready)
                if ready:
                    break
            else:
                raise AssertionError('Request never accepted: deadlock.')
            tag, taddr = route(space, addr)  # accepted on that edge
            if we and tag == FLASH:
                pass  # dropped
            else:
                expected_seen[tag].append((
                    int(we), bh, bl, taddr, wd if we else None
                ))
                if we:
                    ref[tag][taddr] = wd
                else:
                    expected_resp.append(ref[tag].get(taddr, junk(tag, taddr)))
        ctx.set(cpu.valid, 0)
        for _ in range(100):
            await ctx.tick()
        assert not expected_resp, f'{len(expected_resp)} responses missing'
        for tag in ports:
            assert seen[tag] == expected_seen[tag], (
                f'target {tag}: requests differ'
            )

    sim.add_testbench(bench)
    sim.run()


def test_memory_select():
    for seed in range(4):
        run_case(MemorySelect, seed, one_read_at_a_time=True)
        run_case(MemorySelectPipelined, seed, one_read_at_a_time=True)
        run_case(MemorySelectPipelined, seed, one_read_at_a_time=False)
    print('ok')


if __name__ == '__main__':
    test_memory_select()
