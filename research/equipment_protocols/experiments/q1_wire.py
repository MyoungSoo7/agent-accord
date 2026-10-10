"""Q1 - wire efficiency: TCP payload bytes per equipment event, per direction, setup separately.

Everything except Mosquitto runs inside this process; every TCP connection goes through a
CountingRelay. Phases are switched only after the relay has been quiet (no bytes) for a while.

    SECS/GEM :  host --relay-- equipment                         (one hop)
    OPC UA   :  client --relay-- server                          (one hop)
    MQTT     :  publisher --relay A-- broker --relay B-- subscriber  (two hops, reported per leg)
"""

from __future__ import annotations

import asyncio
import json
import threading

from eqp import spb
from eqp.analysis import spread
from eqp.broker import Mosquitto, free_port
from eqp.gem import Equipment, Host
from eqp.io_util import connect_blocking, mqtt_client, subscribe_blocking
from eqp.relay import CountingRelay
from eqp.workload import EventSource, var_specs

from .common import Ctx, attempt, seed_for, wait_until

NS = (1, 10, 50)
K = 50  # steady-state events per run
QUIET = 0.12

VARIANTS = {
    "secs": "SECS-II S6F11 + S6F12 over HSMS",
    "opcua": "OPC UA data change, 1 event per PublishResponse (10 ms publishing interval)",
    "opcua_batch10": "OPC UA data change, ~10 events per PublishResponse (100 ms interval, 10 ms spacing)",
    "json_q0": "MQTT 3.1.1, JSON payload, QoS 0",
    "json_q1": "MQTT 3.1.1, JSON payload, QoS 1",
    "spb_alias_q0": "Sparkplug B DDATA, metric aliases, QoS 0",
    "spb_name_q0": "Sparkplug B DDATA, metric names, QoS 0",
    "spb_alias_q1": "Sparkplug B DDATA, metric aliases, QoS 1",
    "spb_alias_nots_q0": "Sparkplug B DDATA, aliases, no per-metric timestamp, QoS 0",
}


def _phases(relay: CountingRelay) -> dict:
    return relay.snapshot()


# --------------------------------------------------------------------------- SECS/GEM

async def run_secs(n: int, rep: int) -> dict:
    eq = Equipment(n)
    port = await eq.listen()
    relay = CountingRelay("127.0.0.1", port)
    rport = await relay.start()
    got: list[int] = []
    host = Host(n, lambda v: got.append(v[0]))
    relay.set_phase("connect")
    await host.open("127.0.0.1", rport)
    await relay.wait_quiet(QUIET)
    relay.set_phase("app_setup")
    acks = await host.setup()
    await relay.wait_quiet(QUIET)
    relay.set_phase("steady")
    src = EventSource(n, seed_for("q1", "secs", n, rep))
    payload = 0
    for i in range(K):
        v = src.make(i)
        payload += len(eq.encoder.encode(i, v))
        eq.emit(v)
        await asyncio.sleep(0.002)
    ok = await wait_until(lambda: len(got) == K)
    await relay.wait_quiet(QUIET)
    relay.set_phase("teardown")
    await host.close()
    await relay.wait_quiet(QUIET)
    await eq.close()
    await relay.close()
    return {"legs": {"link": _phases(relay)}, "events": K, "complete": ok and got == list(range(K)),
            "app_payload_per_event": payload / K, "acks": acks}


# --------------------------------------------------------------------------- OPC UA

async def run_opcua(srv, srv_port: int, n: int, rep: int, batch: bool) -> dict:
    from asyncua import ua

    from eqp.opcua_io import Consumer, TagEventAssembler

    await srv.reset()
    relay = CountingRelay("127.0.0.1", srv_port)
    rport = await relay.start()
    got: list[int] = []
    asm = TagEventAssembler(lambda s, tc, tr: got.append(s))
    cons = Consumer(f"opc.tcp://127.0.0.1:{rport}/eqp", asm.on_items)
    relay.set_phase("connect")
    await cons.connect()
    await relay.wait_quiet(QUIET)
    relay.set_phase("app_setup")
    specs = var_specs(n)
    pi = 100.0 if batch else 10.0
    info = await cons.subscribe([ua.NodeId(s.name, srv.ns) for s in specs], [s.index for s in specs],
                                publishing_interval=pi, queue_size=1000, keepalive_count=1000)
    await relay.wait_quiet(0.25)
    relay.set_phase("steady")
    responses_before = cons.publish_responses
    k = 2 * K if batch else K
    spacing = 0.010 if batch else 0.030
    src = EventSource(n, seed_for("q1", "opcua", n, rep))
    for i in range(k):
        await srv.write_event(src.make(i), n)
        await asyncio.sleep(spacing)
    ok = await wait_until(lambda: len(got) == k)
    await relay.wait_quiet(0.25)
    responses_steady = cons.publish_responses - responses_before
    relay.set_phase("teardown")
    await cons.disconnect()
    await relay.wait_quiet(QUIET)
    await relay.close()
    return {"legs": {"link": _phases(relay)}, "events": k, "complete": ok and got == list(range(k)),
            "publish_responses": responses_steady, "events_per_response": k / max(responses_steady, 1),
            "subscription": info,
            "mismatched_batches": asm.mismatched_batches}


# --------------------------------------------------------------------------- MQTT

def _json_payload(specs, values) -> bytes:
    return json.dumps({s.name: v for s, v in zip(specs, values)}, separators=(",", ":")).encode()


async def run_mqtt(broker_port: int, variant: str, n: int, rep: int) -> dict:
    qos = 1 if variant.endswith("_q1") else 0
    is_spb = variant.startswith("spb")
    specs = var_specs(n)
    ra, rb = CountingRelay("127.0.0.1", broker_port), CountingRelay("127.0.0.1", broker_port)
    pa, pb = await ra.start(), await rb.start()
    loop = asyncio.get_running_loop()
    received = {"data": 0, "other": 0}
    lock = threading.Lock()
    tag = f"q1-{variant}-{n}-{rep}"
    if is_spb:
        codec = spb.DeviceCodec(specs, mode="name" if "name" in variant else "alias",
                                metric_ts="nots" not in variant)
        group, edge, device = "FAB1", "EDGE-ETCH01", "EQP-ETCH01"
        t_data = spb.topic(group, "DDATA", edge, device)
        sub_filter = f"spBv1.0/{group}/#"
    else:
        t_data = "fab/FAB1/EQP-ETCH01/event"
        sub_filter = t_data

    def on_message(c, u, msg):
        with lock:
            if msg.topic == t_data:
                received["data"] += 1
            else:
                received["other"] += 1

    for r in (ra, rb):
        r.set_phase("connect")
    sub = mqtt_client(f"{tag}-sub")
    sub.on_message = on_message
    await loop.run_in_executor(None, lambda: connect_blocking(sub, pb, keepalive=60))
    await loop.run_in_executor(None, lambda: subscribe_blocking(sub, sub_filter, qos))
    pub = mqtt_client(f"{tag}-pub")
    if is_spb:
        pub.will_set(spb.topic(group, "NDEATH", edge), spb.ndeath(0), qos=1)
    await loop.run_in_executor(None, lambda: connect_blocking(pub, pa, keepalive=60))
    await ra.wait_quiet(QUIET)
    await rb.wait_quiet(QUIET)
    seq = 0
    if is_spb:
        for r in (ra, rb):
            r.set_phase("app_setup")
        pub.publish(spb.topic(group, "NBIRTH", edge), spb.nbirth(0), qos=0)
        pub.publish(spb.topic(group, "DBIRTH", edge, device),
                    codec.dbirth(EventSource(n, 1).make(0), 1), qos=0)
        seq = 2
        await wait_until(lambda: received["other"] >= 2)
        await ra.wait_quiet(QUIET)
        await rb.wait_quiet(QUIET)
    for r in (ra, rb):
        r.set_phase("steady")
    src = EventSource(n, seed_for("q1", variant, n, rep))
    payload = 0
    for i in range(K):
        v = src.make(i)
        if is_spb:
            data = codec.ddata(v, seq)
            seq = (seq + 1) % 256
        else:
            data = _json_payload(specs, v)
        payload += len(data)
        pub.publish(t_data, data, qos=qos)
        await asyncio.sleep(0.002)
    ok = await wait_until(lambda: received["data"] == K)
    await ra.wait_quiet(QUIET)
    await rb.wait_quiet(QUIET)
    for r in (ra, rb):
        r.set_phase("teardown")
    pub.disconnect()
    sub.disconnect()
    await ra.wait_quiet(QUIET)
    await rb.wait_quiet(QUIET)
    pub.loop_stop()
    sub.loop_stop()
    await ra.close()
    await rb.close()
    return {"legs": {"pub": _phases(ra), "sub": _phases(rb)}, "events": K, "complete": ok,
            "app_payload_per_event": payload / K, "topic_len": len(t_data)}


# --------------------------------------------------------------------------- driver

def per_event(run: dict, leg: str, direction: str) -> float:
    return run["legs"][leg].get("steady", {}).get(direction, 0) / run["events"]


def setup_bytes(run: dict, leg: str) -> dict:
    out = {}
    for ph in ("connect", "app_setup"):
        d = run["legs"][leg].get(ph, {"c2s": 0, "s2c": 0})
        out[ph] = d["c2s"] + d["s2c"]
    return out


def summarize(runs: list[dict]) -> dict:
    """variant -> n -> leg -> {fwd, rev, setup_*} each as median/min/max over repetitions."""
    out: dict = {}
    for r in runs:
        out.setdefault(r["variant"], {}).setdefault(str(r["n"]), []).append(r)
    summary: dict = {}
    for variant, by_n in out.items():
        summary[variant] = {}
        for n, rs in by_n.items():
            legs = {}
            for leg in rs[0]["legs"]:
                # forward = direction in which events travel: equipment/server/publisher -> consumer
                fwd_dir = {"link": "s2c", "pub": "c2s", "sub": "s2c"}[leg]
                rev_dir = "c2s" if fwd_dir == "s2c" else "s2c"
                legs[leg] = {
                    "fwd_per_event": spread(per_event(r, leg, fwd_dir) for r in rs),
                    "rev_per_event": spread(per_event(r, leg, rev_dir) for r in rs),
                    "setup_connect": spread(setup_bytes(r, leg)["connect"] for r in rs),
                    "setup_app": spread(setup_bytes(r, leg)["app_setup"] for r in rs),
                }
            summary[variant][n] = {
                "legs": legs,
                "app_payload_per_event": spread(r.get("app_payload_per_event") for r in rs),
                "complete": all(r["complete"] for r in rs),
                "reps": len(rs),
            }
    return summary


async def run(ctx: Ctx) -> dict:
    from eqp.opcua_io import EquipmentServer

    runs: list[dict] = []
    reps = ctx.reps
    ctx.log("Q1 SECS/GEM")
    for n in NS:
        for rep in range(reps):
            r = await attempt(ctx, f"q1 secs n={n} rep={rep}", lambda: run_secs(n, rep))
            if r:
                runs.append({"variant": "secs", "n": n, "rep": rep, **r})
    ctx.log("Q1 OPC UA")
    srv = EquipmentServer(max(NS))
    srv_port = free_port()
    await srv.start(srv_port)
    try:
        for batch in (False, True):
            for n in NS:
                for rep in range(reps):
                    r = await attempt(ctx, f"q1 opcua batch={batch} n={n} rep={rep}",
                                      lambda: run_opcua(srv, srv_port, n, rep, batch))
                    if r:
                        runs.append({"variant": "opcua_batch10" if batch else "opcua", "n": n, "rep": rep, **r})
    finally:
        await srv.stop()
    ctx.log("Q1 MQTT / Sparkplug B")
    with Mosquitto(ctx.dir("q1", "broker")) as broker:
        for variant in [v for v in VARIANTS if v.startswith(("json", "spb"))]:
            for n in NS:
                for rep in range(reps):
                    r = await attempt(ctx, f"q1 {variant} n={n} rep={rep}",
                                      lambda: run_mqtt(broker.port, variant, n, rep))
                    if r:
                        runs.append({"variant": variant, "n": n, "rep": rep, **r})
    incomplete = [(r["variant"], r["n"], r["rep"]) for r in runs if not r["complete"]]
    if incomplete:
        ctx.log(f"Q1 WARNING incomplete runs: {incomplete}")
    return {"variants": VARIANTS, "ns": list(NS), "events_per_run": K, "runs": runs,
            "summary": summarize(runs), "incomplete": incomplete}
