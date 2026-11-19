"""
Test for sdram_controller.py against a behavioural IS42S16160G-7 that checks
the datasheet timing.

The chip model decodes the commands on the pins every clock and fails on:
  * ACT to a bank that is not idle (tRC, tRP and the auto precharge that
    follows an access: tRAS, tDAL), ACT closer than tRRD to another ACT,
  * READ/WRITE before tRCD, to a bank without an open row, or without auto
    precharge (the controller always uses it),
  * REF while a bank is busy, or ACT/REF closer than tRC after a REF,
  * any command before the init sequence is complete (PALL, at least 8 REF,
    MRS with the expected CAS latency), ACT within tMRD of the MRS,
  * two REF commands further apart than tREF / 8192 once running,
  * the controller driving DQ while the chip drives read data.

Read data is put on dq_i so that the controller's own capture clock sees it:
the proven relationship on the board is that the data of a read is on the
pins `capture` clocks after the READ command (cas_latency + read_delay - 1,
see SDRAMController), the PLL's phase shift accounts for the difference to the
ideal-clock picture. Everything else is the datasheet's.

Random reads and writes (with random byte enables and random gaps, at most
one read outstanding as the arbiter guarantees) are compared with a reference
memory. It also checks the read latency, so a regression in speed shows up.
Run with `python test_sdram_controller.py` (or pytest).
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amaranth.sim import Simulator

import sdram_controller as sc
from sdram_controller import SDRAMController

CLK_NS = 1000 // sc.CLOCK_FREQ

# IS42S16160G-7, datasheet Rev. F
T_RCD = 15
T_RP = 15
T_RC = 60
T_RAS = 37
T_RRD = 14
T_DAL = 30
T_MRD = 14
T_REF_ROW = 64_000_000 // 8192      # ns between refreshes, evenly spread

IDLE, ACTIVE = 'idle', 'active'


class Chip:
    def __init__(self, cas_latency, read_delay):
        self.cl = cas_latency
        self.capture = cas_latency + read_delay - 1
        self.mem = {}
        self.banks = [dict(row=None, t_act=-10**9, ready=0) for _ in range(4)]
        self.last_act = -10**9
        self.last_ref = -10**9
        self.last_cmd_ok_after = 0      # tRC after REF, tMRD after MRS
        self.mrs_done = False
        self.n_ref = 0
        self.seen_pall = False
        self.read_data = {}             # cycle -> value (what dq_i shows)
        self.bus_busy = set()           # cycles with the chip driving DQ
        self.ref_times = []
        self.n_cmds = {'ACT': 0, 'READ': 0, 'WRITE': 0, 'REF': 0}

    def cmd(self, n, p):
        """One clock; p holds the pins as the chip sees them."""
        now = n * CLK_NS
        assert p['cke'] == 1, 'CKE must stay high'
        if p['dq_oe'] and n in self.bus_busy:
            raise AssertionError(f'cycle {n}: DQ driven during read data')
        code = (p['cs_n'], p['ras_n'], p['cas_n'], p['we_n'])
        if p['cs_n'] == 1 or code == (0, 1, 1, 1):
            return
        where = f'cycle {n}'
        a10 = (p['a'] >> 10) & 1
        if not self.mrs_done:
            if code == (0, 0, 1, 0):        # PRECHARGE
                assert a10, f'{where}: init PRECHARGE must be all banks'
                self.seen_pall = True
                self.n_ref = 0
                for b in self.banks:
                    b['ready'] = now + T_RP
            elif code == (0, 0, 0, 1):      # REF
                assert self.seen_pall, f'{where}: REF before PRECHARGE'
                assert now >= max(b['ready'] for b in self.banks), \
                    f'{where}: init REF before tRP'
                assert now >= self.last_ref + T_RC, f'{where}: tRC (init)'
                self.last_ref = now
                self.n_ref += 1
            elif code == (0, 0, 0, 0):      # MODE REGISTER SET
                assert self.n_ref >= sc.INIT_REFRESHES, \
                    f'{where}: MRS after only {self.n_ref} REF'
                assert now >= self.last_ref + T_RC, f'{where}: MRS before tRC'
                want = 0x200 | (self.cl << 4)
                assert p['a'] == want, \
                    f'{where}: mode register {p["a"]:#x}, expected {want:#x}'
                self.mrs_done = True
                self.last_cmd_ok_after = now + T_MRD
                self.mrs_at = n
            else:
                raise AssertionError(f'{where}: command {code} during init')
            return

        assert now >= self.last_cmd_ok_after, f'{where}: command before tMRD/tRC'
        if code == (0, 0, 0, 1):            # REF
            for i, b in enumerate(self.banks):
                assert now >= b['ready'], f'{where}: REF with bank {i} busy'
            assert now >= self.last_ref + T_RC, f'{where}: REF closer than tRC'
            self.last_ref = now
            self.last_cmd_ok_after = now + T_RC
            self.ref_times.append(now)
            self.n_cmds['REF'] += 1
        elif code == (0, 0, 1, 1):          # ACTIVE
            b = self.banks[p['ba']]
            assert b['row'] is None, f'{where}: ACT on open bank'
            assert now >= b['ready'], \
                f'{where}: ACT before the bank is precharged ' \
                f'({b["ready"] - now} ns early)'
            assert now >= b['t_act'] + T_RC, f'{where}: ACT-ACT closer than tRC'
            assert now >= self.last_act + T_RRD, f'{where}: tRRD'
            b['row'] = p['a']
            b['t_act'] = now
            self.last_act = now
            self.n_cmds['ACT'] += 1
        elif code in ((0, 1, 0, 0), (0, 1, 0, 1)):  # WRITE, READ
            b = self.banks[p['ba']]
            write = code == (0, 1, 0, 0)
            assert b['row'] is not None, f'{where}: column command, no row'
            assert now >= b['t_act'] + T_RCD, f'{where}: tRCD'
            assert a10, f'{where}: no auto precharge'
            key = (p['ba'], b['row'], p['a'] & 0x1ff)
            if write:
                assert p['dq_oe'], f'{where}: WRITE without driving DQ'
                old = self.mem.get(key, 0xdead)
                new = old
                if not p['dqm'] & 1:
                    new = (new & 0xff00) | (p['dq_o'] & 0x00ff)
                if not p['dqm'] & 2:
                    new = (new & 0x00ff) | (p['dq_o'] & 0xff00)
                self.mem[key] = new
                # auto precharge: tDAL after the data, tRAS after the ACT
                ready = max(now + T_DAL, b['t_act'] + T_RAS + T_RP)
                self.n_cmds['WRITE'] += 1
            else:
                data_at = n + self.capture
                self.read_data[data_at] = self.mem.get(key, 0xdead)
                self.bus_busy.update((data_at - 1, data_at, data_at + 1))
                # precharge starts cl - 1 clocks before the last data
                ready = max(now + CLK_NS + T_RP, b['t_act'] + T_RAS + T_RP)
                self.n_cmds['READ'] += 1
            b['row'] = None
            b['ready'] = ready
        else:
            raise AssertionError(f'{where}: unexpected command {code}')


def run_case(seed, n_ops, cas_latency=2, read_delay=1, p_gap=0.3):
    rng = random.Random(seed)
    dut = SDRAMController(cas_latency=cas_latency, read_delay=read_delay)
    chip = Chip(cas_latency, read_delay)
    req, resp, pins = dut.bus.req, dut.bus.resp, dut.sdram
    ref = {}                    # host word address -> value
    pending = {'n': 0}          # reads accepted, response not yet seen
    expected = []               # (address, byte enables, value) per read
    results = {'done': False, 'latencies': set()}

    sim = Simulator(dut)
    sim.add_clock(1e-6)

    async def chip_proc(ctx):
        """The chip, plus the monitor of the host port (same clock edge, so
        the cycle numbers of accept and response agree)."""
        n = 0
        accepted = []
        while True:
            (_, _, cke, cs_n, ras_n, cas_n, we_n, a, ba, dqm, dq_o, dq_oe,
             valid, ready, we, rvalid, rdata) = await ctx.tick().sample(
                pins.cke, pins.cs_n, pins.ras_n, pins.cas_n, pins.we_n,
                pins.a, pins.ba, pins.dqm, pins.dq_o, pins.dq_oe,
                req.valid, req.ready, req.we, resp.valid, resp.data)
            chip.cmd(n, dict(
                cke=cke, cs_n=cs_n, ras_n=ras_n, cas_n=cas_n, we_n=we_n,
                a=a, ba=ba, dqm=dqm, dq_o=dq_o, dq_oe=dq_oe))
            if valid and ready and not we:
                accepted.append(n)
            if rvalid:
                at = accepted.pop(0)
                addr, hi, lo, want = expected.pop(0)
                pending['n'] -= 1
                if want is not None:    # skip words never written
                    mask = (0xff00 if hi else 0) | (0x00ff if lo else 0)
                    assert rdata & mask == want & mask, \
                        (seed, addr, hex(rdata), hex(want))
                results['latencies'].add(n - at)
            n += 1
            # what the pins show in the next cycle
            ctx.set(pins.dq_i, chip.read_data.get(n, 0xbeef))

    sim.add_testbench(chip_proc, background=True)

    def host_addr():
        # a few banks/rows/columns so bank changes and row changes happen
        bank = rng.randrange(4)
        row = rng.randrange(3)
        col = rng.randrange(8)
        return (bank << 22) | (row << 9) | col

    async def host(ctx):
        # one read at a time, like the arbiter lets through
        for i in range(n_ops):
            addr = host_addr()
            we = rng.random() < 0.5
            hi, lo = rng.choice([(1, 1)] * 6 + [(1, 0), (0, 1)])
            data = rng.randrange(65536)
            for gap in range(rng.randrange(1, 4) if rng.random() < p_gap else 0):
                await ctx.tick()
            if not we:
                # wait for the previous response before another read
                while pending['n']:
                    await ctx.tick()
            ctx.set(req.valid, 1)
            ctx.set(req.we, we)
            ctx.set(req.byte_hi, hi)
            ctx.set(req.byte_lo, lo)
            ctx.set(req.addr, addr)
            ctx.set(req.wdata, data)
            for _ in range(5000):
                _, _, ready = await ctx.tick().sample(req.ready)
                if ready:
                    break
            else:
                raise AssertionError('request never accepted')
            if we:
                old = ref.get(addr, 0xdead)
                if lo:
                    old = (old & 0xff00) | (data & 0x00ff)
                if hi:
                    old = (old & 0x00ff) | (data & 0xff00)
                ref[addr] = old
            else:
                pending['n'] += 1
                expected.append((addr, hi, lo, ref.get(addr)))
            ctx.set(req.valid, 0)
        while pending['n']:
            await ctx.tick()
        results['done'] = True

    sim.add_testbench(host)
    sim.run()
    assert results['done']
    assert chip.mrs_done
    return chip, results


def check_case(seed, n_ops, **kw):
    chip, results = run_case(seed, n_ops, **kw)
    # refresh keeps up: gaps between REF commands within the spec
    t = chip.ref_times
    assert len(t) >= 5, 'too few refreshes to say anything'
    gaps = [b - a for a, b in zip(t, t[1:])]
    assert max(gaps) <= T_REF_ROW, (max(gaps), T_REF_ROW)
    assert chip.n_cmds['READ'] and chip.n_cmds['WRITE']
    return chip, results


def test_sdram_controller():
    # default: CL2, the proven capture relationship
    check_case(1, 4000)
    check_case(2, 4000, p_gap=0.0)          # back to back
    # CL3 as before still works
    check_case(3, 2000, cas_latency=3)
    # the read latency of the default configuration, accept to response:
    # ACT, READ (tRCD = 1 clock), cl clocks to the capture, the response
    chip, results = run_case(4, 500, p_gap=0.0)
    assert min(results['latencies']) == 5, results['latencies']


if __name__ == '__main__':
    test_sdram_controller()
    print('ok')
