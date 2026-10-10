"""OPC UA tag-model event re-assembly and the durable event recorder."""

from types import SimpleNamespace as NS

from eqp.io_util import Recorder, read_events
from eqp.opcua_io import SEQ_SENTINEL, TagEventAssembler


def item(handle, value):
    return NS(ClientHandle=handle, Value=NS(Value=NS(Value=value)))


def test_initial_values_in_creation_order_do_not_shift_pairing():
    got = []
    asm = TagEventAssembler(lambda s, tc, tr: got.append((s, tc, tr)))
    # initial notification: items listed in creation order (seq first), sentinel seq, time 0
    asm.on_items([item(0, SEQ_SENTINEL), item(1, 0), item(2, 1.0)], t_recv=10)
    # one publish carrying three events: asyncua lists each item's queue in write order
    asm.on_items([item(2, 1.1), item(2, 1.2), item(2, 1.3),
                  item(1, 1001), item(1, 1002), item(1, 1003),
                  item(0, 0), item(0, 1), item(0, 2)], t_recv=20)
    assert got == [(0, 1001, 20), (1, 1002, 20), (2, 1003, 20)]
    assert asm.mismatched_batches == 0


def test_mismatch_is_counted():
    asm = TagEventAssembler(lambda *a: None)
    asm.on_items([item(1, 5), item(0, 1), item(0, 2)], t_recv=1)
    assert asm.mismatched_batches == 1


def test_recorder_durable_and_buffered(tmp_path):
    for durable in (True, False):
        path = tmp_path / f"log_{durable}"
        rec = Recorder(path, durable=durable)
        for i in range(5):
            rec.event(i, 100 + i, 200 + i)
        rec.state(t=1, type="NBIRTH", bdseq=0)
        if durable:  # readable before close: survives a SIGKILL of the writer
            assert len(read_events(path)) == 5
        rec.close()
        assert read_events(path) == [(i, 100 + i, 200 + i) for i in range(5)]
