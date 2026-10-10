"""Sparkplug B payloads (Eclipse Tahu ``sparkplug_b.proto``) for the equipment event.

The protobuf classes are the Tahu-generated ``sparkplug_b_pb2`` shipped inside the
``pysparkplug`` PyPI package (``pysparkplug._protobuf``).  Transport is plain paho-mqtt.

Topic namespace: spBv1.0/<group>/<NBIRTH|NDEATH|DBIRTH|DDATA|...>/<edge node>[/<device>]

Encoding choices (documented in the report):
  * DBIRTH carries name + alias + datatype + value + timestamp for every metric.
  * DDATA carries, per metric, alias *or* name, the value and (by default) a per-metric timestamp
    (the Tahu python helper ``addMetric`` always sets one); datatype is omitted in DDATA.
  * Payload.seq is 0..255 and wraps; NBIRTH has seq 0; NDEATH carries only bdSeq.
"""

from __future__ import annotations

import time

from pysparkplug._protobuf import Payload  # Tahu-generated message class

NAMESPACE = "spBv1.0"

# Tahu DataType enum values
DT_BOOLEAN = 11
DT_UINT32 = 7
DT_UINT64 = 8
DT_DOUBLE = 10
DT_STRING = 12

_DT = {"U4": DT_UINT32, "U8": DT_UINT64, "F8": DT_DOUBLE, "A": DT_STRING}
_FIELD = {"U4": "int_value", "U8": "long_value", "F8": "double_value", "A": "string_value"}


def topic(group: str, mtype: str, edge: str, device: str | None = None) -> str:
    return f"{NAMESPACE}/{group}/{mtype}/{edge}" + (f"/{device}" if device else "")


def now_ms() -> int:
    return int(time.time() * 1000)


def nbirth(bdseq: int, ts: int | None = None) -> bytes:
    ts = now_ms() if ts is None else ts
    p = Payload()
    p.timestamp = ts
    p.seq = 0
    m = p.metrics.add()
    m.name, m.timestamp, m.datatype, m.long_value = "bdSeq", ts, DT_UINT64, bdseq
    m = p.metrics.add()
    m.name, m.timestamp, m.datatype, m.boolean_value = "Node Control/Rebirth", ts, DT_BOOLEAN, False
    return p.SerializeToString()


def ndeath(bdseq: int, ts: int | None = None) -> bytes:
    ts = now_ms() if ts is None else ts
    p = Payload()
    p.timestamp = ts
    m = p.metrics.add()
    m.name, m.timestamp, m.datatype, m.long_value = "bdSeq", ts, DT_UINT64, bdseq
    return p.SerializeToString()


class DeviceCodec:
    """DBIRTH / DDATA for one device whose metrics are the event variables."""

    def __init__(self, specs, *, mode: str = "alias", metric_ts: bool = True) -> None:
        if mode not in ("alias", "name"):
            raise ValueError(mode)
        self.specs = specs
        self.mode = mode
        self.metric_ts = metric_ts
        self.fields = [_FIELD[s.kind] for s in specs]
        self.by_alias = {s.alias: s.index for s in specs}
        self.by_name = {s.name: s.index for s in specs}

    def dbirth(self, values: list | None, seq: int, ts: int | None = None) -> bytes:
        ts = now_ms() if ts is None else ts
        p = Payload()
        p.timestamp = ts
        p.seq = seq
        for s in self.specs:
            m = p.metrics.add()
            m.name = s.name
            m.alias = s.alias
            m.timestamp = ts
            m.datatype = _DT[s.kind]
            if values is None or values[s.index] is None:
                m.is_null = True
            else:
                setattr(m, _FIELD[s.kind], values[s.index])
        return p.SerializeToString()

    def ddata(self, values: list, seq: int, ts: int | None = None) -> bytes:
        ts = now_ms() if ts is None else ts
        p = Payload()
        p.timestamp = ts
        p.seq = seq
        add = p.metrics.add
        by_alias = self.mode == "alias"
        mts = self.metric_ts
        for s, field, v in zip(self.specs, self.fields, values):
            m = add()
            if by_alias:
                m.alias = s.alias
            else:
                m.name = s.name
            if mts:
                m.timestamp = ts
            setattr(m, field, v)
        return p.SerializeToString()

    def decode(self, raw: bytes) -> tuple[list, int, int]:
        """-> (values aligned to specs, payload seq, payload timestamp)."""
        p = Payload.FromString(raw)
        values: list = [None] * len(self.specs)
        for m in p.metrics:
            idx = self.by_alias.get(m.alias) if m.HasField("alias") else self.by_name.get(m.name)
            if idx is None:
                continue
            values[idx] = getattr(m, m.WhichOneof("value"))
        return values, p.seq, p.timestamp


def parse(raw: bytes) -> Payload:
    return Payload.FromString(raw)


def bdseq_of(raw: bytes) -> int | None:
    for m in Payload.FromString(raw).metrics:
        if m.name == "bdSeq":
            return m.long_value
    return None
