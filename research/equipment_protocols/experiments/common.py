"""Shared experiment helpers."""

from __future__ import annotations

import asyncio
import sys
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

SEED_BASE = 20261010


def seed_for(*parts) -> int:
    """Deterministic seed from the configuration name and repetition."""
    return (SEED_BASE + zlib.crc32(repr(parts).encode())) % (2**31)


@dataclass
class Ctx:
    workdir: Path
    reps: int = 5
    t0: float = field(default_factory=time.monotonic)
    failures: list = field(default_factory=list)

    def log(self, msg: str) -> None:
        sys.stdout.write(f"[{time.monotonic() - self.t0:7.1f}s] {msg}\n")
        sys.stdout.flush()

    def dir(self, *parts: str) -> Path:
        d = self.workdir.joinpath(*parts)
        d.mkdir(parents=True, exist_ok=True)
        return d


async def wait_until(cond, timeout: float = 10.0, poll: float = 0.005) -> bool:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(poll)
    return True


async def attempt(ctx: Ctx, label: str, make_coro, tries: int = 2):
    """Run one repetition; retry once on failure; record failures; None if it never succeeded."""
    for i in range(tries):
        try:
            return await make_coro()
        except Exception as exc:  # recorded in results.json, never silently dropped
            ctx.log(f"{label}: attempt {i + 1} failed: {exc!r}"[:400])
            ctx.failures.append({"label": label, "attempt": i + 1, "error": repr(exc)[:500]})
    return None
