"""Protocol-agnostic byte-counting TCP relay (asyncio).

    client --> [relay listen port] --> target server

Every chunk read from either socket is counted under the *current phase* label and its
direction ("c2s": client->server, "s2c": server->client), then forwarded unchanged.
Counts are TCP payload bytes (application bytes); TCP/IP headers are not visible here.
The orchestrator switches phases (e.g. "setup" -> "steady") after waiting for quiescence.

``blackhole(True)`` keeps all sockets open but silently discards traffic in both directions
(a silent network partition as seen by both ends).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class CountingRelay:
    def __init__(self, target_host: str, target_port: int, *, name: str = "relay") -> None:
        self.target = (target_host, target_port)
        self.name = name
        self.phase = "setup"
        self.counts: dict[str, dict[str, int]] = defaultdict(lambda: {"c2s": 0, "s2c": 0})
        self.chunks: dict[str, dict[str, int]] = defaultdict(lambda: {"c2s": 0, "s2c": 0})
        self.connections = 0
        self.last_activity = time.monotonic()
        self._blackhole = False
        self._server: asyncio.base_events.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task] = set()

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self._server = await asyncio.start_server(self._on_client, host, port)
        return self._server.sockets[0].getsockname()[1]

    def set_phase(self, phase: str) -> None:
        self.phase = phase

    def blackhole(self, on: bool) -> None:
        self._blackhole = on

    def bytes(self, phase: str, direction: str) -> int:
        return self.counts[phase][direction] if phase in self.counts else 0

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {p: dict(v) for p, v in self.counts.items()}

    async def wait_quiet(self, quiet: float = 0.15, timeout: float = 5.0) -> bool:
        """Wait until no byte has crossed the relay for `quiet` seconds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            idle = time.monotonic() - self.last_activity
            if idle >= quiet:
                return True
            await asyncio.sleep(quiet - idle + 0.005)
        return False

    async def _on_client(self, c_reader: asyncio.StreamReader, c_writer: asyncio.StreamWriter) -> None:
        try:
            s_reader, s_writer = await asyncio.open_connection(*self.target)
        except OSError:
            c_writer.close()
            return
        self.connections += 1
        self._writers.update((c_writer, s_writer))
        t1 = asyncio.create_task(self._pump(c_reader, s_writer, "c2s"))
        t2 = asyncio.create_task(self._pump(s_reader, c_writer, "s2c"))
        self._tasks.update((t1, t2))
        await asyncio.gather(t1, t2, return_exceptions=True)
        self._tasks.difference_update((t1, t2))
        self._writers.difference_update((c_writer, s_writer))

    async def _pump(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, direction: str) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                self.last_activity = time.monotonic()
                if self._blackhole:
                    self.counts["blackholed"][direction] += len(data)
                    continue
                self.counts[self.phase][direction] += len(data)
                self.chunks[self.phase][direction] += 1
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def close(self) -> None:
        if self._server:
            self._server.close()
        for w in list(self._writers):
            try:
                w.close()
            except Exception:
                pass
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
