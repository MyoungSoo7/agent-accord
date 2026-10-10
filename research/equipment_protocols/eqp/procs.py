"""Orchestrator side of the role processes (asyncio subprocesses)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

STUDY_DIR = Path(__file__).resolve().parent.parent


class Proc:
    def __init__(self, role: str, cfg: dict, logdir: Path, tag: str) -> None:
        self.role, self.cfg, self.tag = role, cfg, tag
        self.logdir = Path(logdir)
        self.p: asyncio.subprocess.Process | None = None
        self.msgs: asyncio.Queue = asyncio.Queue()
        self._reader: asyncio.Task | None = None
        self.started_at = 0.0

    async def start(self, timeout: float = 30.0) -> dict:
        self.logdir.mkdir(parents=True, exist_ok=True)
        err = open(self.logdir / f"{self.tag}.stderr", "ab")
        self.started_at = time.monotonic()
        self.p = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "eqp.roles", self.role, json.dumps(self.cfg),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=err, cwd=str(STUDY_DIR),
            env={**os.environ, "PYTHONUNBUFFERED": "1"})
        err.close()
        self._reader = asyncio.create_task(self._read())
        return await self.expect("READY", timeout)

    async def _read(self) -> None:
        assert self.p and self.p.stdout
        while True:
            line = await self.p.stdout.readline()
            if not line:
                await self.msgs.put(("EOF", {}))
                return
            head, _, rest = line.decode().strip().partition(" ")
            try:
                payload = json.loads(rest) if rest else {}
            except json.JSONDecodeError:
                payload = {"raw": rest}
            await self.msgs.put((head, payload))

    async def expect(self, tag: str, timeout: float = 30.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{self.tag}: no {tag} within {timeout}s")
            head, payload = await asyncio.wait_for(self.msgs.get(), remaining)
            if head == tag:
                return payload
            if head == "EOF":
                errf = self.logdir / f"{self.tag}.stderr"
                tail = errf.read_text()[-2000:] if errf.exists() else ""
                raise RuntimeError(f"{self.tag} ({self.role}) exited while waiting for {tag}:\n{tail}")

    def send(self, line: str) -> None:
        if self.p and self.p.stdin and self.p.returncode is None:
            self.p.stdin.write(line.encode() + b"\n")

    @property
    def pid(self) -> int:
        assert self.p
        return self.p.pid

    def signal(self, sig: int) -> None:
        if self.p and self.p.returncode is None:
            os.kill(self.p.pid, sig)

    def kill(self) -> None:
        self.signal(signal.SIGKILL)

    async def wait(self, timeout: float = 10.0) -> None:
        if not self.p:
            return
        try:
            await asyncio.wait_for(self.p.wait(), timeout)
        except asyncio.TimeoutError:
            self.kill()
            await self.p.wait()
        if self._reader:
            self._reader.cancel()

    async def exit(self, timeout: float = 10.0) -> dict | None:
        """Ask the role to finish (EXIT), return its BYE payload; kill if unresponsive."""
        if not self.p or self.p.returncode is not None:
            await self.wait(1)
            return None
        self.send("EXIT")
        try:
            bye = await self.expect("BYE", timeout)
        except (TimeoutError, RuntimeError, asyncio.TimeoutError):
            bye = None
        await self.wait(timeout)
        return bye


async def stop_all(procs: list[Proc]) -> None:
    for p in procs:
        if p.p and p.p.returncode is None:
            p.kill()
    for p in procs:
        await p.wait(5)


async def wait_drain(consumer: Proc, expected: int, *, idle: float = 0.6, timeout: float = 20.0,
                     poll: float = 0.1) -> int:
    """Poll the consumer's record count until it reaches `expected` or stops growing for `idle` s."""
    deadline = time.monotonic() + timeout
    last, last_change = -1, time.monotonic()
    while time.monotonic() < deadline:
        consumer.send("STATUS")
        count = (await consumer.expect("STATUS", 5.0))["count"]
        now = time.monotonic()
        if count >= expected:
            return count
        if count != last:
            last, last_change = count, now
        elif now - last_change >= idle:
            return count
        await asyncio.sleep(poll)
    return last
