"""Sparkplug B payloads: decodable by pysparkplug's own model (aliases resolved via DBIRTH)."""

import pysparkplug as psp
import pytest

from eqp import spb
from eqp.workload import EventSource, var_specs

TS = 1_791_650_000_000


@pytest.mark.parametrize("n", [1, 10, 50])
def test_ddata_alias_resolves_through_dbirth_in_pysparkplug(n):
    specs = var_specs(n)
    codec = spb.DeviceCodec(specs, mode="alias")
    values = EventSource(n, seed=7).make(123)
    birth = psp.DBirth.decode(codec.dbirth(values, seq=1, ts=TS))
    data = psp.DData.decode(codec.ddata(values, seq=2, ts=TS), birth=birth)
    assert data.seq == 2 and data.timestamp == TS
    by_name = {m.name: m.value for m in data.metrics}
    for s in specs:
        assert by_name[s.name] == pytest.approx(values[s.index]) if s.kind == "F8" else by_name[s.name] == values[s.index]
    assert all(m.alias == s.alias for m, s in zip(birth.metrics, specs))


def test_alias_payload_has_no_names_and_is_smaller():
    specs = var_specs(10)
    values = EventSource(10, seed=1).make(0)
    alias = spb.DeviceCodec(specs, mode="alias").ddata(values, 5, TS)
    names = spb.DeviceCodec(specs, mode="name").ddata(values, 5, TS)
    assert b"PM1/AI" not in alias and b"PM1/AI" in names
    assert len(alias) < len(names)
    no_ts = spb.DeviceCodec(specs, mode="alias", metric_ts=False).ddata(values, 5, TS)
    assert len(alias) - len(no_ts) == 7 * len(specs)  # tag + 6-byte varint ms timestamp per metric


def test_round_trip_and_birth_death_bdseq():
    specs = var_specs(10)
    codec = spb.DeviceCodec(specs, mode="alias")
    values = EventSource(10, seed=2).make(9)
    got, seq, ts = codec.decode(codec.ddata(values, 200, TS))
    assert (seq, ts) == (200, TS)
    assert got[0] == 9 and got[1] == values[1] and got == values
    assert spb.bdseq_of(spb.nbirth(3, TS)) == 3 and spb.bdseq_of(spb.ndeath(3, TS)) == 3
    assert spb.parse(spb.nbirth(3, TS)).seq == 0
    assert not spb.parse(spb.ndeath(3, TS)).HasField("seq")
    assert spb.topic("FAB1", "DDATA", "GW", "EQP") == "spBv1.0/FAB1/DDATA/GW/EQP"
