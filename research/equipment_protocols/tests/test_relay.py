"""Byte-counting relay: exact per-phase, per-direction counts; blackhole; close propagation."""

import asyncio

from eqp.relay import CountingRelay


async def _echo_server():
    async def handle(r, w):
        try:
            while data := await r.read(65536):
                w.write(data)
                await w.drain()
        finally:
            w.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def test_counts_per_phase_and_direction():
    async def main():
        server, port = await _echo_server()
        relay = CountingRelay("127.0.0.1", port)
        rport = await relay.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", rport)
        writer.write(b"a" * 100)
        assert await reader.readexactly(100) == b"a" * 100
        await relay.wait_quiet(0.05)
        relay.set_phase("steady")
        for _ in range(5):
            writer.write(b"b" * 10)
            await reader.readexactly(10)
        writer.write(b"c" * 70000)  # larger than one read chunk
        await reader.readexactly(70000)
        writer.close()
        await relay.wait_quiet(0.05)
        await relay.close()
        server.close()
        return relay

    relay = asyncio.run(main())
    assert relay.counts["setup"] == {"c2s": 100, "s2c": 100}
    assert relay.counts["steady"] == {"c2s": 70050, "s2c": 70050}
    assert relay.connections == 1


def test_blackhole_discards_both_directions_and_counts_them():
    async def main():
        server, port = await _echo_server()
        relay = CountingRelay("127.0.0.1", port)
        rport = await relay.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", rport)
        relay.blackhole(True)
        writer.write(b"x" * 33)
        try:
            await asyncio.wait_for(reader.read(1), 0.2)
            delivered = True
        except asyncio.TimeoutError:
            delivered = False
        relay.blackhole(False)
        writer.write(b"y" * 7)
        echoed = await asyncio.wait_for(reader.readexactly(7), 1.0)
        writer.close()
        await relay.close()
        server.close()
        return relay, delivered, echoed

    relay, delivered, echoed = asyncio.run(main())
    assert not delivered and echoed == b"y" * 7
    assert relay.counts["blackholed"]["c2s"] == 33
    assert relay.counts["setup"] == {"c2s": 7, "s2c": 7}


def test_client_close_propagates_to_server():
    async def main():
        closed = asyncio.Event()

        async def handle(r, w):
            await r.read()  # until EOF
            closed.set()
            w.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        relay = CountingRelay("127.0.0.1", server.sockets[0].getsockname()[1])
        rport = await relay.start()
        _r, w = await asyncio.open_connection("127.0.0.1", rport)
        w.write(b"hi")
        await asyncio.sleep(0.05)
        w.close()
        await asyncio.wait_for(closed.wait(), 1.0)
        await relay.close()
        server.close()

    asyncio.run(main())
