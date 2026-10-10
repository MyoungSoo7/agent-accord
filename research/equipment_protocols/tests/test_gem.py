"""GEM equipment/host over loopback: report setup, ordered delivery, spooling vs no spooling."""

import asyncio

from eqp.analysis import seq_stats
from eqp.gem import Equipment, Host
from eqp.workload import EventSource


async def _wait(cond, timeout=3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError
        await asyncio.sleep(0.01)


def test_setup_acks_and_ordered_delivery():
    async def main():
        eq = Equipment(10)
        port = await eq.listen()
        got = []
        host = Host(10, lambda v: got.append(v))
        acks = await host.connect("127.0.0.1", port)
        src = EventSource(10, seed=1)
        sent = [src.make(i) for i in range(50)]
        for v in sent:
            eq.emit(v)
        await _wait(lambda: len(got) == 50)
        await host.close()
        await eq.close()
        return acks, sent, got, eq.stats

    acks, sent, got, stats = asyncio.run(main())
    assert acks == {"commack": 0, "drack": 0, "lrack": 0, "erack": 0}
    assert [g[0] for g in got] == list(range(50))
    assert got[7] == sent[7]  # every value survives the round trip
    assert stats["acked"] == 50


def _outage(spool: bool):
    async def main():
        eq = Equipment(1, spool=spool)
        port = await eq.listen()
        got = []
        src = EventSource(1, seed=2)
        host = Host(1, lambda v: got.append(v[0]))
        await host.connect("127.0.0.1", port)
        for i in range(10):
            eq.emit(src.make(i))
        await _wait(lambda: len(got) == 10)
        host.conn.close()  # abrupt link loss
        await _wait(lambda: not eq.communicating)
        for i in range(10, 30):  # created while not communicating
            eq.emit(src.make(i))
        host2 = Host(1, lambda v: got.append(v[0]))
        await host2.connect("127.0.0.1", port, define_reports=False, request_spool=spool)
        for i in range(30, 40):
            eq.emit(src.make(i))
        await _wait(lambda: len(got) >= (40 if spool else 20))
        await asyncio.sleep(0.05)
        await host2.close()
        await eq.close()
        return got, eq.stats

    return asyncio.run(main())


def test_without_spool_events_during_outage_are_lost():
    got, stats = _outage(spool=False)
    s = seq_stats(got, 0, 39)
    assert s["lost"] == 20 and s["lost_ranges"] == [[10, 29]] and s["duplicates"] == 0
    assert stats["discarded"] == 20


def test_with_spool_everything_arrives_in_order_after_s6f23():
    got, stats = _outage(spool=True)
    s = seq_stats(got, 0, 39)
    assert s["lost"] == 0 and s["out_of_order"] == 0 and s["duplicates"] == 0
    assert got == list(range(40))
    assert stats["spooled"] >= 20
