"""Q2 - end-to-end latency and throughput on loopback (separate OS processes per role).

Latency = consumer's time.monotonic_ns() when the decoded event reaches application code minus
the EventTimeNs stamped at event creation in the producer process (CLOCK_MONOTONIC, same host).
Each repetition starts fresh processes and runs the blocks: Poisson arrivals at 10, 100 and
1000 ev/s, then an unpaced burst whose consumer-side delivery rate is reported as the
saturation throughput ("max sustainable rate" of this implementation stack).
"""

from __future__ import annotations

from eqp.analysis import latency_summary, seq_stats, spread
from eqp.broker import Mosquitto, dropped_warnings, free_port
from eqp.io_util import read_events
from eqp.procs import Proc, stop_all, wait_drain

from .common import Ctx, attempt, seed_for

N = 10
GAP = 0.3
BLOCKS = [
    {"label": "r10", "rate": 10, "duration": 2.5},
    {"label": "r100", "rate": 100, "duration": 2.0},
    {"label": "r1000", "rate": 1000, "duration": 2.0},
    {"label": "burst", "rate": 0, "count": 3000},
]
NAGLE_BLOCKS = BLOCKS[:2]
OPCUA_PUBLISHING_MS = 10.0

CONFIGS = {
    "secs_direct": "SECS/GEM: equipment -> host (HSMS, S6F11 W-bit, 1 open transaction)",
    "opcua": f"OPC UA: server -> client subscription ({OPCUA_PUBLISHING_MS:g} ms publishing interval, queue 1000)",
    "mqtt_qos0": "MQTT QoS 0 via Mosquitto (Sparkplug B DDATA, aliases)",
    "mqtt_qos1": "MQTT QoS 1 via Mosquitto (Sparkplug B DDATA, aliases)",
    "gateway": "SECS/GEM -> gateway -> MQTT QoS 1 (S6F12 after PUBACK)",
    "gateway_ack_receipt": "SECS/GEM -> gateway -> MQTT QoS 1 (S6F12 on receipt, before PUBACK)",
    "mqtt_qos0_nagle": "sensitivity: mqtt_qos0 with Nagle on (paho/Mosquitto default, no TCP_NODELAY)",
}


def analyze(events: list[tuple[int, int, int]], blocks: list[dict]) -> dict:
    out = {}
    for b in blocks:
        sel = [e for e in events if b["first"] <= e[0] <= b["last"]]
        st = seq_stats([e[0] for e in sel], b["first"], b["last"])
        first_rx: dict[int, tuple[int, int]] = {}
        for s, tc, tr in sel:
            first_rx.setdefault(s, (tc, tr))
        lat = [(tr - tc) / 1e6 for tc, tr in first_rx.values()]
        rx = sorted(tr for _tc, tr in first_rx.values())
        span = (rx[-1] - rx[0]) / 1e9 if len(rx) > 1 else 0.0
        gen_span = b["t1"] - b["t0"]
        out[b["label"]] = {
            **{k: st[k] for k in ("expected", "unique", "lost", "duplicates", "out_of_order")},
            "latency_ms": latency_summary(lat),
            "generated_rate": st["expected"] / gen_span if gen_span > 0 else None,
            "delivered_rate": (len(rx) - 1) / span if span > 0 else None,
        }
    return out


async def run_rep(ctx: Ctx, name: str, rep: int, broker_port: int | None, blocks: list[dict]) -> dict:
    rundir = ctx.dir("q2", name, f"rep{rep}")
    log = str(rundir / "consumer.log")
    seed = seed_for("q2", name, rep)
    procs: list[Proc] = []
    try:
        if name == "secs_direct":
            prod = Proc("secs_equipment", {"n": N, "blocks": blocks, "gap": GAP, "seed": seed}, rundir, "equipment")
            procs.append(prod)
            port = (await prod.start())["port"]
            cons = Proc("secs_host", {"n": N, "port": port, "log": log}, rundir, "host")
            procs.append(cons)
            await cons.start()
        elif name == "opcua":
            prod = Proc("opcua_server", {"n_max": N, "n": N, "port": free_port(), "blocks": blocks, "gap": GAP,
                                         "seed": seed}, rundir, "server")
            procs.append(prod)
            r = await prod.start(90)
            cons = Proc("opcua_client", {"n": N, "url": r["url"], "ns": r["ns"], "log": log,
                                         "publishing_interval": OPCUA_PUBLISHING_MS, "queue_size": 1000}, rundir, "client")
            procs.append(cons)
            await cons.start(90)
        elif name.startswith("mqtt"):
            qos = 1 if "qos1" in name else 0
            nodelay = "nagle" not in name
            cons = Proc("spb_consumer", {"n": N, "port": broker_port, "qos": qos, "client_id": f"q2-{name}-{rep}-host",
                                         "log": log, "nodelay": nodelay}, rundir, "consumer")
            procs.append(cons)
            await cons.start()
            prod = Proc("spb_publisher", {"n": N, "port": broker_port, "qos": qos, "blocks": blocks, "gap": GAP,
                                          "seed": seed, "client_id": f"q2-{name}-{rep}-edge", "nodelay": nodelay},
                        rundir, "publisher")
            procs.append(prod)
            await prod.start()
        elif name.startswith("gateway"):
            cons = Proc("spb_consumer", {"n": N, "port": broker_port, "qos": 1, "client_id": f"q2-{name}-{rep}-host",
                                         "log": log}, rundir, "consumer")
            procs.append(cons)
            await cons.start()
            prod = Proc("secs_equipment", {"n": N, "blocks": blocks, "gap": GAP, "seed": seed}, rundir, "equipment")
            procs.append(prod)
            port = (await prod.start())["port"]
            gw = Proc("gateway", {"n": N, "eq_port": port, "broker_port": broker_port, "qos": 1,
                                  "ack": "receipt" if name.endswith("receipt") else "puback",
                                  "bdseq_path": str(rundir / "bdseq"), "client_id": f"q2-{name}-{rep}-edge"},
                      rundir, "gateway")
            procs.append(gw)
            await gw.start()
        else:
            raise ValueError(name)
        prod.send("GO")
        done = await prod.expect("DONE", 180)
        total = done["blocks"][-1]["last"] + 1
        received = await wait_drain(cons, total, idle=1.5, timeout=60)
        byes = {p.tag: await p.exit() for p in procs}
    finally:
        await stop_all(procs)
    res = analyze(read_events(log), done["blocks"])
    return {"config": name, "rep": rep, "blocks": res, "received": received, "total": total, "byes": byes}


def summarize(runs: list[dict]) -> dict:
    out: dict = {}
    for name in sorted({r["config"] for r in runs}):
        rs = [r for r in runs if r["config"] == name]
        out[name] = {}
        for label in rs[0]["blocks"]:
            bs = [r["blocks"][label] for r in rs]
            out[name][label] = {
                "p50": spread(b["latency_ms"]["p50"] for b in bs),
                "p95": spread(b["latency_ms"]["p95"] for b in bs),
                "p99": spread(b["latency_ms"]["p99"] for b in bs),
                "max": spread(b["latency_ms"]["max"] for b in bs),
                "delivered_rate": spread(b["delivered_rate"] for b in bs),
                "generated_rate": spread(b["generated_rate"] for b in bs),
                "lost": spread(b["lost"] for b in bs),
                "lost_total": sum(b["lost"] for b in bs),
                "duplicates_total": sum(b["duplicates"] for b in bs),
                "out_of_order_total": sum(b["out_of_order"] for b in bs),
                "expected_per_rep": bs[0]["expected"],
                "samples_per_rep": spread(b["latency_ms"]["n"] for b in bs),
                "reps": len(bs),
            }
    return out


async def run(ctx: Ctx) -> dict:
    """Repetition-major order (rep 0 of every config, then rep 1, ...), so that transient load from
    other processes on the host is spread over all configurations instead of hitting one."""
    import os

    runs: list[dict] = []
    broker_notes: dict = {name: {"dropped_notices": 0} for name in CONFIGS if name.startswith(("mqtt", "gateway"))}
    for rep in range(ctx.reps):
        for name in CONFIGS:
            ctx.log(f"Q2 rep {rep} {name}")
            blocks = NAGLE_BLOCKS if name.endswith("nagle") else BLOCKS
            broker = None
            if name.startswith(("mqtt", "gateway")):
                broker = Mosquitto(ctx.dir("q2", name, f"broker{rep}"), nodelay="nagle" not in name)
                broker.start()
            load = os.getloadavg()[0]
            try:
                r = await attempt(ctx, f"q2 {name} rep={rep}",
                                  lambda: run_rep(ctx, name, rep, broker.port if broker else None, blocks))
                if r:
                    r["loadavg_1m_at_start"] = load
                    runs.append(r)
            finally:
                if broker:
                    broker.stop()
                    broker_notes[name]["dropped_notices"] += dropped_warnings(broker.log_path)
    summary = summarize(runs)
    for name in summary:
        loads = [r["loadavg_1m_at_start"] for r in runs if r["config"] == name]
        summary[name]["_loadavg_1m"] = spread(loads)
    return {"configs": CONFIGS, "n": N, "blocks": BLOCKS, "nagle_blocks": NAGLE_BLOCKS,
            "opcua_publishing_ms": OPCUA_PUBLISHING_MS, "order": "repetition-major", "runs": runs,
            "summary": summary, "broker": broker_notes}
