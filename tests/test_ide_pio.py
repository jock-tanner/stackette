"""
Test for ide_pio.py against the model of a CompactFlash card (ide_model.py):
the reset values, the cycle times of PIO mode 0 on the pins, the task file
registers, IDENTIFY DEVICE, a sector written and read back, IORDY held low
(and stuck low), the interrupt request, the registers that make the cycle
faster, and the requests that wait while a cycle goes on.

Run with `python test_ide_pio.py` (or pytest).
"""
from amaranth.sim import Simulator

from ide_model import CFCard, DRDY, DRQ, DSC
from ide_pio import (
    HOLD_CLOCKS, RECOVER_DEFAULT, REG_BB_CTRL, REG_BB_DD_IN, REG_BB_DD_OUT,
    REG_BB_PINS, SETUP_DEFAULT, STROBE_DEFAULT, IDEPio, REG_ALT_STATUS, REG_DATA, REG_RECOVER, REG_SETUP,
    REG_STATUS, REG_STROBE,
)

CLK_NS = 20
CYCLE_CLOCKS = 600 // CLK_NS


def run_test(body, card=None):
    card = card or CFCard()
    dut = IDEPio()
    sim = Simulator(dut)
    sim.add_clock(CLK_NS * 1e-9)
    log = []        # (clock, cs0_n, cs1_n, da, dior_n, diow_n, oe, dd_o)
    clock = [0]

    async def slave(ctx):
        prev = None
        while True:
            await ctx.tick()
            clock[0] += 1
            p = dut.pins
            cur = (
                ctx.get(p.cs0_n), ctx.get(p.cs1_n), ctx.get(p.da),
                ctx.get(p.dior_n), ctx.get(p.diow_n), ctx.get(p.dd_oe),
                ctx.get(p.dd_o),
            )
            if cur != prev:
                log.append((clock[0],) + cur)
            prev = cur
            card.pins(*cur[:5], cur[6], cur[5])
            ctx.set(p.dd_i, card.dd)
            ctx.set(p.intrq, card.intrq)
            ctx.set(p.iordy, card.iordy)

    async def main(ctx):
        await body(ctx, dut, card, log)

    sim.add_testbench(slave, background=True)
    sim.add_testbench(main)
    sim.run()


def cycles(log):
    """The cycles on the bus from the log of the pins: start (CS# low), low
    and high (the strobe), end (CS# high), the register, and whether DD was
    driven when the strobe fell and when it rose."""
    out = []
    cur = None
    for clock, cs0_n, cs1_n, da, dior_n, diow_n, oe, dd in log:
        selected = not (cs0_n and cs1_n)
        if selected and cur is None:
            cur = {
                'start': clock, 'cs1': not cs1_n, 'da': da, 'low': None,
                'high': None, 'end': None, 'write': False, 'oe': [],
            }
        if cur is not None:
            cur['oe'].append(oe)
            strobe = not (dior_n and diow_n)
            if strobe and cur['low'] is None:
                cur['low'] = clock
                cur['write'] = not diow_n
                cur['dd_low'] = (oe, dd)
            if not strobe and cur['low'] is not None and cur['high'] is None:
                cur['high'] = clock
                cur['dd_high'] = (oe, dd)
            if not selected:
                cur['end'] = clock
                out.append(cur)
                cur = None
    return out


async def access(ctx, dut, addr, we=0, wdata=0):
    """One access; -> (data, stalls) of a read, stalls of a write."""
    req, resp = dut.bus.req, dut.bus.resp
    ctx.set(req.valid, 1)
    ctx.set(req.addr, addr)
    ctx.set(req.we, we)
    ctx.set(req.wdata, wdata)
    ctx.set(req.byte_hi, 1)
    ctx.set(req.byte_lo, 1)
    stalls = 0
    while not ctx.get(req.ready):
        await ctx.tick()
        stalls += 1
        assert stalls < 200000, 'stuck'
    await ctx.tick()
    ctx.set(req.valid, 0)
    if we:
        assert ctx.get(resp.valid) == 0
        return stalls
    waited = 0
    while not ctx.get(resp.valid):
        await ctx.tick()
        waited += 1
        assert waited < 200000, 'no response'
    return ctx.get(resp.data), stalls + waited


async def rd(ctx, dut, addr):
    return (await access(ctx, dut, addr))[0]


async def wr(ctx, dut, addr, v):
    await access(ctx, dut, addr, we=1, wdata=v)


async def settle(ctx, n=60):
    for _ in range(n):
        await ctx.tick()


def test_reset_values():
    async def body(ctx, dut, card, log):
        assert await rd(ctx, dut, REG_SETUP) == SETUP_DEFAULT == 5
        assert await rd(ctx, dut, REG_STROBE) == STROBE_DEFAULT == 16
        assert await rd(ctx, dut, REG_RECOVER) == RECOVER_DEFAULT == 8
        # IORDY is high, no interrupt, no timeout
        assert await rd(ctx, dut, REG_STATUS) == 0b100
        assert ctx.get(dut.irq) == 0
        # the bus is idle: both selects and both strobes high, DD not driven
        p = dut.pins
        assert ctx.get(p.cs0_n) == 1 and ctx.get(p.cs1_n) == 1
        assert ctx.get(p.dior_n) == 1 and ctx.get(p.diow_n) == 1
        assert ctx.get(p.dd_oe) == 0
        # the log has only the first sample: idle, DD not driven
        assert [e[1:] for e in log] == [(1, 1, 0, 1, 1, 0, 0)]

    run_test(body)


def test_cycle_of_a_read():
    async def body(ctx, dut, card, log):
        await rd(ctx, dut, 7)
        await rd(ctx, dut, 7)
        await settle(ctx)
        c = cycles(log)
        assert len(c) == 2, log
        a = c[0]
        assert (a['cs1'], a['da'], a['write']) == (False, 7, False)
        # PIO mode 0: address setup 70 ns, strobe 290 ns
        assert (a['low'] - a['start']) * CLK_NS >= 70
        assert (a['high'] - a['low']) * CLK_NS >= 290
        # the address stays after the strobe
        assert a['end'] - a['high'] >= HOLD_CLOCKS
        # the bus is not driven during a read
        assert not any(a['oe'])
        # the next cycle comes after the 600 ns of the cycle
        assert (c[1]['start'] - a['start']) * CLK_NS >= 600, c

    run_test(body)

def test_task_file():
    async def body(ctx, dut, card, log):
        for reg, v in ((2, 3), (3, 0x11), (4, 0x22), (5, 0x33), (6, 0xe5)):
            await wr(ctx, dut, reg, v)
            assert await rd(ctx, dut, reg) == v
        assert card.count == 3 and card.lba == [0x11, 0x22, 0x33, 0]
        assert card.lba28 == 0x05332211
        # a write sends the low byte of the word to a register, and a read
        # gives the low byte
        await wr(ctx, dut, 3, 0xab77)
        await settle(ctx)
        assert card.lba[0] == 0x77
        assert await rd(ctx, dut, 3) == 0x77
        # the writes are cycles with DD driven
        c = [x for x in cycles(log) if x['write']]
        assert len(c) == 6 and all(x['dd_low'][0] for x in c)

    run_test(body)

def test_identify():
    async def body(ctx, dut, card, log):
        await wr(ctx, dut, 6, 0xe0)
        await wr(ctx, dut, 7, 0xec)
        assert (await rd(ctx, dut, 7)) & DRQ
        words = [await rd(ctx, dut, REG_DATA) for _ in range(256)]
        text = bytes(b for w in words[27:47] for b in (w >> 8, w & 255))
        assert text.startswith(b'STACKETTE TEST CARD'), text
        assert words[60] == 2048
        assert (await rd(ctx, dut, 7)) == DRDY | DSC

    run_test(body)


def test_sector_write_and_read():
    async def body(ctx, dut, card, log):
        data = [(0x3a5c * i + 7) & 0xffff for i in range(256)]
        await wr(ctx, dut, 6, 0xe0)
        await wr(ctx, dut, 2, 1)
        await wr(ctx, dut, 3, 9)
        await wr(ctx, dut, 7, 0x30)
        assert (await rd(ctx, dut, 7)) & DRQ
        for w in data:
            await wr(ctx, dut, REG_DATA, w)
        assert (await rd(ctx, dut, 7)) == DRDY | DSC
        assert card.image[9 * 512] == data[0] & 0xff
        assert card.image[9 * 512 + 1] == data[0] >> 8
        await wr(ctx, dut, 7, 0x20)
        back = [await rd(ctx, dut, REG_DATA) for _ in range(256)]
        assert back == data

    run_test(body)


def test_write_data_is_stable_around_the_strobe():
    async def body(ctx, dut, card, log):
        await wr(ctx, dut, 6, 0xe0)
        await wr(ctx, dut, 2, 1)
        await wr(ctx, dut, 7, 0x30)
        await wr(ctx, dut, REG_DATA, 0xbeef)
        await settle(ctx)
        data = [x for x in cycles(log) if x['da'] == 0 and not x['cs1']]
        assert len(data) == 1
        # DD is driven with the word when DIOW# falls, and still when it
        # has risen, and not any more at the end of the cycle
        assert data[0]['dd_low'] == (1, 0xbeef), data
        assert data[0]['dd_high'] == (1, 0xbeef), data
        assert data[0]['oe'][-1] == 0
        assert card.status & DRQ == DRQ

    run_test(body)

def test_iordy_wait():
    async def body(ctx, dut, card, log):
        await wr(ctx, dut, 6, 0xe0)
        await wr(ctx, dut, 7, 0xec)
        n0 = len(log)
        v = await rd(ctx, dut, REG_DATA)
        assert v == card.identify()[0]
        await settle(ctx)
        c = [x for x in cycles(log[n0:]) if x['da'] == 0 and not x['cs1']]
        # 15 clocks of the strobe, and the card holds IORDY for 40 more
        assert c[0]['high'] - c[0]['low'] >= 40, c
        assert (await rd(ctx, dut, REG_STATUS)) & 0b10 == 0

    run_test(body, CFCard(data_waits=40))


def test_iordy_stuck_low():
    async def body(ctx, dut, card, log):
        await wr(ctx, dut, 6, 0xe0)
        await wr(ctx, dut, 7, 0xec)
        await rd(ctx, dut, REG_DATA)
        # the timeout ends the cycle, and the flag stays until it is cleared
        assert (await rd(ctx, dut, REG_STATUS)) & 0b10
        await wr(ctx, dut, REG_STATUS, 0)
        assert (await rd(ctx, dut, REG_STATUS)) & 0b10 == 0

    run_test(body, CFCard(data_waits=1 << 20))


def test_interrupt():
    async def body(ctx, dut, card, log):
        await wr(ctx, dut, 6, 0xe0)
        await wr(ctx, dut, 7, 0xec)
        await settle(ctx, 60)
        assert ctx.get(dut.irq) == 1
        assert (await rd(ctx, dut, REG_STATUS)) & 1
        # the alternate status does not clear it
        await rd(ctx, dut, REG_ALT_STATUS)
        await settle(ctx, 60)
        assert ctx.get(dut.irq) == 1
        # the status does
        await rd(ctx, dut, 7)
        await settle(ctx, 60)
        assert ctx.get(dut.irq) == 0
        # nIEN (device control, bit 1) masks the request at the card
        await wr(ctx, dut, 7, 0xec)
        await wr(ctx, dut, REG_ALT_STATUS, 2)
        await settle(ctx, 60)
        assert ctx.get(dut.irq) == 0
        # the cycles for the alternate status use CS1# and DA = 6
        assert any(x['cs1'] and x['da'] == 6 for x in cycles(log))

    run_test(body)


def test_reset_pin():
    async def body(ctx, dut, card, log):
        assert ctx.get(dut.pins.reset_n) == 1
        assert await rd(ctx, dut, 12) == 0
        await wr(ctx, dut, 12, 1)
        await settle(ctx, 3)
        assert ctx.get(dut.pins.reset_n) == 0
        assert await rd(ctx, dut, 12) == 1
        await wr(ctx, dut, 12, 0)
        await settle(ctx, 3)
        assert ctx.get(dut.pins.reset_n) == 1
        # it is not a cycle on the bus
        assert len(log) == 1

    run_test(body)


def test_pins_by_hand():
    async def body(ctx, dut, card, log):
        p = dut.pins
        # CS0# low, DA = 5, DD driven with 0x1234, the others idle
        await wr(ctx, dut, REG_BB_CTRL, 1 | 0b11100 | 5 << 5 | 1 << 8 | 1 << 9)
        await wr(ctx, dut, REG_BB_DD_OUT, 0x1234)
        await settle(ctx, 3)
        assert ctx.get(p.cs0_n) == 0 and ctx.get(p.cs1_n) == 1
        assert ctx.get(p.dior_n) == 1 and ctx.get(p.diow_n) == 1
        assert ctx.get(p.da) == 5
        assert ctx.get(p.dd_oe) == 1 and ctx.get(p.dd_o) == 0x1234
        assert ctx.get(p.reset_n) == 1
        # DD as the card drives it, and INTRQ and IORDY
        ctx.set(p.dd_i, 0xa55a)
        assert await rd(ctx, dut, REG_BB_DD_IN) == 0xa55a
        assert await rd(ctx, dut, REG_BB_PINS) == 0b10
        # RESET# low, nothing is driven
        await wr(ctx, dut, REG_BB_CTRL, 1 | 0b11110)
        await settle(ctx, 3)
        assert ctx.get(p.reset_n) == 0 and ctx.get(p.dd_oe) == 0
        # off: the cycles of the bus again
        await wr(ctx, dut, REG_BB_CTRL, 0)
        await rd(ctx, dut, 7)
        await settle(ctx)
        assert len(cycles(log)) == 1

    run_test(body)


def test_software_reset():
    async def body(ctx, dut, card, log):
        await wr(ctx, dut, 2, 7)
        await wr(ctx, dut, REG_ALT_STATUS, 4)
        await settle(ctx)
        assert card.count == 1
        await wr(ctx, dut, REG_ALT_STATUS, 0)

    run_test(body)


def test_faster_cycle():
    async def body(ctx, dut, card, log):
        # PIO mode 4: 25 ns, 70 ns, 120 ns at 50 MHz
        await wr(ctx, dut, REG_SETUP, 2)
        await wr(ctx, dut, REG_STROBE, 4)
        await wr(ctx, dut, REG_RECOVER, 0)
        assert await rd(ctx, dut, REG_STROBE) == 4
        await rd(ctx, dut, 7)
        await rd(ctx, dut, 7)
        await settle(ctx)
        c = cycles(log)
        assert len(c) == 2, log
        assert c[0]['low'] - c[0]['start'] == 2
        assert c[0]['high'] - c[0]['low'] == 4
        assert (c[1]['start'] - c[0]['start']) * CLK_NS >= 120, c
        # the strobe has 4 clocks at the least, whatever is set
        await wr(ctx, dut, REG_STROBE, 0)
        n0 = len(log)
        await rd(ctx, dut, 7)
        await settle(ctx)
        c = cycles(log[n0:])
        assert c[-1]['high'] - c[-1]['low'] == 4, c

    run_test(body)


def test_requests_wait_for_the_cycle():
    async def body(ctx, dut, card, log):
        # a write is accepted at once, the next request waits
        stalls = await access(ctx, dut, 2, we=1, wdata=1)
        assert stalls == 0
        _, stalls = await access(ctx, dut, 2)
        assert stalls >= CYCLE_CLOCKS - 10, stalls
        # the registers of the controller never wait, in the middle of a cycle
        await access(ctx, dut, 3, we=1, wdata=5)
        assert (await access(ctx, dut, REG_STATUS))[1] == 0
        # the word 13 is not there
        assert await rd(ctx, dut, 13) == 0

    run_test(body)


if __name__ == '__main__':
    for name, f in list(globals().items()):
        if name.startswith('test_'):
            f()
            print(name, 'ok')
