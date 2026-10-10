"""Equipment event workload shared by every protocol.

An event carries 2 bookkeeping values + N process variables:

    EventSeq     uint32   sequence number (0, 1, 2, ...)
    EventTimeNs  uint64   time.monotonic_ns() at event creation (CLOCK_MONOTONIC, system-wide on Linux)
    N variables  pattern by i % 10:  0..5 -> float64 sensor value (random walk, rounded to 4 decimals)
                                      6..8 -> uint32 counter (base + seq)
                                      9    -> 11-char ASCII id (lot.slot or recipe.step), changes every event

Every value changes on every event, so change-driven protocols (OPC UA data change,
Sparkplug report-by-exception) must still carry all N+2 values.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

SENSORS = ("Pressure", "RFPower", "GasFlow", "Temp", "Voltage", "Current")
COUNTERS = ("WaferCount", "RFHours", "AlarmCount")

SEQ_VID = 100
TIME_VID = 101
CEID = 5001
RPTID = 4001


@dataclass(frozen=True, slots=True)
class VarSpec:
    index: int  # position in the event value list
    name: str  # hierarchical name (JSON key, Sparkplug metric name, OPC UA string NodeId)
    kind: str  # "U4" | "U8" | "F8" | "A"
    vid: int  # SECS variable id
    alias: int  # Sparkplug metric alias


def var_specs(n: int) -> list[VarSpec]:
    specs = [VarSpec(0, "EventSeq", "U4", SEQ_VID, 1), VarSpec(1, "EventTimeNs", "U8", TIME_VID, 2)]
    for i in range(n):
        r = i % 10
        pm = f"PM{i % 3 + 1}"
        if r < 6:
            kind, name = "F8", f"{pm}/AI/{SENSORS[r]}_{i:02d}"
        elif r < 9:
            kind, name = "U4", f"{pm}/CNT/{COUNTERS[r - 6]}_{i:02d}"
        else:
            kind = "A"
            name = f"{pm}/ID/{'LotID' if (i // 10) % 2 == 0 else 'RecipeID'}_{i:02d}"
        specs.append(VarSpec(2 + i, name, kind, 1000 + i, 3 + i))
    return specs


def id_string(spec: VarSpec, seq: int) -> str:
    lot, slot = seq // 25, seq % 25 + 1
    if "LotID" in spec.name:
        return f"LOT{lot % 100000:05d}.{slot:02d}"  # 11 chars
    return f"RCP{lot % 1000:03d}.ST{slot:02d}"  # 11 chars


class EventSource:
    """Deterministic (seeded) value generator; timestamps are taken at make()."""

    def __init__(self, n: int, seed: int) -> None:
        self.specs = var_specs(n)
        self.rng = random.Random(seed)
        self._float = {s.index: self.rng.uniform(10.0, 500.0) for s in self.specs[2:] if s.kind == "F8"}
        self._base = {s.index: self.rng.randrange(10_000, 1_000_000) for s in self.specs[2:] if s.kind == "U4"}

    def make(self, seq: int) -> list:
        t = time.monotonic_ns()
        vals: list = [seq, t]
        gauss = self.rng.gauss
        for s in self.specs[2:]:
            if s.kind == "F8":
                x = self._float[s.index] + gauss(0.0, 0.5)
                self._float[s.index] = x
                vals.append(round(x, 4))
            elif s.kind == "U4":
                vals.append(self._base[s.index] + seq)
            else:
                vals.append(id_string(s, seq))
        return vals


@dataclass(frozen=True, slots=True)
class Block:
    """One measurement block of a schedule: rate in ev/s (0 = unpaced burst) for duration or count."""

    label: str
    rate: float
    duration: float = 0.0
    count: int = 0

    def n_events(self) -> int:
        return self.count if self.rate <= 0 else int(round(self.rate * self.duration))
