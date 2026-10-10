"""Q3 - delivery semantics that are fixed by the protocols, not by these implementations.

a) consumer outage: 100 ev/s stream; the consumer process is SIGKILLed at t=1.5 s and a new one
   (same MQTT client id / same HSMS host role) is started at t=3.5 s. Lost / duplicated /
   out-of-order counted by sequence number over the union of both consumers' durable logs.
b) OPC UA coalescing: one variable written ~1000 times/s, subscription publishing interval 100 ms,
   monitored item queueSize 1 / 10 / 100 and 0 ("server default"), discardOldest=true.
c) Sparkplug B: gateway (edge node) failure while streaming. "crash" = SIGKILL (the OS closes the
   socket); "hang" = SIGSTOP (socket stays open, broker must rely on MQTT keepalive). Time until
   the host application sees NDEATH (the gateway's Last Will) and until NBIRTH after restart.
d) gateway acknowledgement point: the gateway->broker link is blackholed (relay discards traffic,
   sockets stay open) for 2 s, then the gateway is SIGKILLed and restarted. S6F12 sent on receipt
   vs. only after the broker's PUBACK; the equipment spools in both cases.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from pathlib import Path

from eqp.analysis import seq_stats, spread
from eqp.broker import Mosquitto, dropped_warnings, free_port
from eqp.io_util import read_events, read_states
from eqp.procs import Proc, stop_all, wait_drain
from eqp.relay import CountingRelay

from .common import Ctx, attempt, seed_for

N = 10

# ----------------------------------------------------------------------------------- Q3a
RATE_A = 100
DURATION_A = 6.0
KILL_AT = 1.5
RESTART_AT = 3.5
CONFIGS_A = {
    "mqtt_qos0_persistent": "MQTT QoS 0, persistent session (cleanSession=0)",
    "mqtt_qos1_clean": "MQTT QoS 1, clean session (cleanSession=1)",
    "mqtt_qos1_persistent": "MQTT QoS 1, persistent session (cleanSession=0)",
    "secs_nospool": "SECS/GEM, no spooling (events while not COMMUNICATING are discarded)",
    "secs_spool": "SECS/GEM, spooling + S6F23 unload after reconnect",
}


async def _sleep_until(t_mono: float) -> None:
    d = t_mono - time.monotonic()
    if d > 0:
        await asyncio.sleep(d)


async def outage_rep(ctx: Ctx, name: str, rep: int, broker_port: int | None) -> dict:
    rundir = ctx.dir("q3a", name, f"rep{rep}")
    logs = [str(rundir / "consumer_a.log"), str(rundir / "consumer_b.log")]
    blocks = [{"label": "stream", "rate": RATE_A, "duration": DURATION_A}]
    seed = seed_for("q3a", name, rep)
    procs: list[Proc] = []
    is_mqtt = name.startswith("mqtt")
    qos = 1 if "qos1" in name else 0
    clean = "clean" in name
    spool = name == "secs_spool"

    def consumer(i: int, port: int) -> Proc:
        if is_mqtt:
            return Proc("spb_consumer", {"n": N, "port": broker_port, "qos": qos, "clean_session": clean,
                                         "client_id": f"q3a-{name}-{rep}-host", "log": logs[i], "durable": True},
                        rundir, f"consumer_{'ab'[i]}")
        return Proc("secs_host", {"n": N, "port": port, "log": logs[i], "durable": True,
                                  "define_reports": i == 0, "request_spool": spool and i == 1},
                    rundir, f"host_{'ab'[i]}")

    try:
        port = 0
        if is_mqtt:
            ca = consumer(0, 0)
            procs.append(ca)
            first = await ca.start()
            prod = Proc("spb_publisher", {"n": N, "port": broker_port, "qos": qos, "blocks": blocks, "seed": seed,
                                          "client_id": f"q3a-{name}-{rep}-edge"}, rundir, "publisher")
            procs.append(prod)
            await prod.start()
        else:
            prod = Proc("secs_equipment", {"n": N, "blocks": blocks, "seed": seed, "spool": spool}, rundir, "equipment")
            procs.append(prod)
            port = (await prod.start())["port"]
            ca = consumer(0, port)
            procs.append(ca)
            first = await ca.start()
        t_go = time.monotonic()
        prod.send("GO")
        await _sleep_until(t_go + KILL_AT)
        t_kill = time.monotonic_ns()
        ca.kill()
        await ca.wait(5)
        await _sleep_until(t_go + RESTART_AT)
        cb = consumer(1, port)
        procs.append(cb)
        t_restart = time.monotonic_ns()
        second = await cb.start()
        t_ready = time.monotonic_ns()
        done = await prod.expect("DONE", 60)
        total = done["blocks"][-1]["last"] + 1
        await wait_drain(cb, total, idle=1.5, timeout=30)
        byes = {p.tag: await p.exit() for p in procs if p is not ca}
    finally:
        await stop_all(procs)
    ev_a, ev_b = read_events(logs[0]), read_events(logs[1])
    stats = seq_stats([e[0] for e in ev_a + ev_b], 0, total - 1)
    created = {}
    for s, tc, _tr in ev_a + ev_b:
        created.setdefault(s, tc)
    # events created while no consumer was attached (between kill and new consumer READY)
    outage_seqs = [s for s, tc in created.items() if t_kill <= tc <= t_ready]
    b_first_rx = min((tr for _s, _tc, tr in ev_b), default=None)
    b_last_backlog = None
    if ev_b:
        backlog = [tr for s, tc, tr in ev_b if tc < t_ready]
        b_last_backlog = max(backlog) if backlog else None
    return {
        "config": name, "rep": rep, **stats, "total": total,
        "received_a": len(ev_a), "received_b": len(ev_b),
        "outage_s": (t_ready - t_kill) / 1e9, "restart_to_ready_s": (t_ready - t_restart) / 1e9,
        "created_during_outage_received": len(outage_seqs),
        "backlog_drain_ms": (b_last_backlog - t_ready) / 1e6 if b_last_backlog else None,
        "first_rx_after_ready_ms": (b_first_rx - t_ready) / 1e6 if b_first_rx else None,
        "consumer_a": first, "consumer_b": second, "byes": byes,
    }


async def run_a(ctx: Ctx) -> dict:
    """The repetitions of one configuration run concurrently, each with its own broker/processes."""
    runs, notes = [], {}
    for name in CONFIGS_A:
        ctx.log(f"Q3a {name}")
        brokers = []
        if name.startswith("mqtt"):
            brokers = [Mosquitto(ctx.dir("q3a", name, f"broker{rep}")) for rep in range(ctx.reps)]
            for b in brokers:
                b.start()
        try:
            got = await asyncio.gather(*(attempt(ctx, f"q3a {name} rep={rep}", lambda rep=rep: outage_rep(
                ctx, name, rep, brokers[rep].port if brokers else None)) for rep in range(ctx.reps)))
            runs += [r for r in got if r]
        finally:
            for b in brokers:
                b.stop()
            notes[name] = {"dropped_notices": sum(dropped_warnings(b.log_path) for b in brokers)}
    summary = {}
    for name in CONFIGS_A:
        rs = [r for r in runs if r["config"] == name]
        summary[name] = {k: spread(r[k] for r in rs) for k in
                         ("lost", "duplicates", "out_of_order", "outage_s", "total", "backlog_drain_ms")}
        summary[name]["lost_total"] = sum(r["lost"] for r in rs)
        summary[name]["duplicates_total"] = sum(r["duplicates"] for r in rs)
        summary[name]["out_of_order_total"] = sum(r["out_of_order"] for r in rs)
        summary[name]["expected_total"] = sum(r["total"] for r in rs)
    return {"configs": CONFIGS_A, "rate": RATE_A, "duration_s": DURATION_A, "kill_at_s": KILL_AT,
            "restart_at_s": RESTART_AT, "concurrent_reps": True, "runs": runs, "summary": summary,
            "broker": notes}


# ----------------------------------------------------------------------------------- Q3b
QUEUE_SIZES = (1, 10, 100, 0)
FAST_COUNT = 1000
FAST_PERIOD = 0.001
PUBLISHING_B = 100.0


async def run_b(ctx: Ctx) -> dict:
    """Per repetition: one client per queueSize subscribes, then the server writes FastSignal
    0..999 once; every client observes the same write sequence through its own subscription."""
    rundir = ctx.dir("q3b")
    server = Proc("opcua_server", {"n_max": 1, "port": free_port()}, rundir, "server")
    runs = []
    try:
        r = await server.start(90)
        for rep in range(ctx.reps):
            ctx.log(f"Q3b rep {rep}")
            clients, infos, logs = {}, {}, {}
            for q in QUEUE_SIZES:
                logs[q] = str(rundir / f"q{q}_rep{rep}.log")
                clients[q] = Proc("opcua_client", {"url": r["url"], "ns": r["ns"], "log": logs[q], "fast": True,
                                                   "publishing_interval": PUBLISHING_B, "queue_size": q,
                                                   "keepalive_count": 100}, rundir, f"client_q{q}_{rep}")
            started = await asyncio.gather(*(c.start(90) for c in clients.values()))
            infos = dict(zip(clients, started))
            await asyncio.sleep(0.3)  # initial values delivered before the writes start
            t_start = time.monotonic_ns()
            server.send(f"FAST {json.dumps({'count': FAST_COUNT, 'period': FAST_PERIOD})}")
            wrote = await server.expect("DONE", 60)
            await asyncio.sleep(2.5 * PUBLISHING_B / 1000)
            byes = await asyncio.gather(*(c.exit() for c in clients.values()))
            for q, bye in zip(clients, byes):
                values = json.loads(Path(logs[q] + ".fast.json").read_text())
                got = [(v, t) for v, t in values if t > t_start]
                distinct = sorted({int(v) for v, _t in got if 0 <= v < FAST_COUNT})
                per_publish: dict[int, int] = {}
                for _v, t in got:
                    per_publish[t] = per_publish.get(t, 0) + 1
                runs.append({
                    "queue_size": q, "rep": rep, "revised_queue_size": infos[q]["revised_queue_size"],
                    "revised_sampling_interval": infos[q]["revised_sampling_interval"],
                    "written": wrote["written"], "write_seconds": wrote["seconds"],
                    "write_rate": wrote["written"] / wrote["seconds"],
                    "received": len(got), "distinct": len(distinct),
                    "vanished": FAST_COUNT - len(distinct),
                    "vanished_pct": 100 * (FAST_COUNT - len(distinct)) / FAST_COUNT,
                    "last_value_received": int(max(distinct)) if distinct else None,
                    "publish_responses": len(per_publish),
                    "values_per_response_max": max(per_publish.values()) if per_publish else 0,
                    "client_bye": bye,
                })
    finally:
        await server.exit()
        await stop_all([server])
    summary = {}
    for q in QUEUE_SIZES:
        rs = [x for x in runs if x["queue_size"] == q]
        summary[str(q)] = {
            "revised_queue_size": rs[0]["revised_queue_size"],
            "distinct": spread(x["distinct"] for x in rs),
            "vanished_pct": spread(x["vanished_pct"] for x in rs),
            "publish_responses": spread(x["publish_responses"] for x in rs),
            "values_per_response_max": spread(x["values_per_response_max"] for x in rs),
            "write_rate": spread(x["write_rate"] for x in rs),
            "last_value_received": spread(x["last_value_received"] for x in rs),
        }
    return {"queue_sizes": list(QUEUE_SIZES), "count": FAST_COUNT, "period_s": FAST_PERIOD,
            "publishing_interval_ms": PUBLISHING_B, "runs": runs, "summary": summary}


# ----------------------------------------------------------------------------------- Q3c
SCENARIOS_C = {
    "crash_k5": {"keepalive": 5, "signal": "SIGKILL"},
    "hang_k2": {"keepalive": 2, "signal": "SIGSTOP"},
    "hang_k4": {"keepalive": 4, "signal": "SIGSTOP"},
}
FAIL_AT = 1.0
RATE_C = 100


async def _wait_state(log: str, mtype: str, after_ns: int, timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for st in read_states(log):
            if st["type"] == mtype and st["t"] > after_ns:
                return st
        await asyncio.sleep(0.01)
    return None


async def ndeath_rep(ctx: Ctx, name: str, rep: int, broker_port: int) -> dict:
    sc = SCENARIOS_C[name]
    k = sc["keepalive"]
    rundir = ctx.dir("q3c", name, f"rep{rep}")
    log = str(rundir / "host_app.log")
    # long enough to keep streaming past NDEATH + rebirth (Mosquitto detects silence coarsely)
    duration = FAIL_AT + (3.5 * k + 1.0 if sc["signal"] == "SIGSTOP" else 3.5)
    blocks = [{"label": "stream", "rate": RATE_C, "duration": duration}]
    procs: list[Proc] = []
    gw_cfg = {"n": N, "broker_port": broker_port, "qos": 1, "ack": "puback", "keepalive": k,
              "bdseq_path": str(rundir / "bdseq"), "client_id": f"q3c-{name}-{rep}-edge"}
    try:
        cons = Proc("spb_consumer", {"n": N, "port": broker_port, "qos": 1, "client_id": f"q3c-{name}-{rep}-host",
                                     "log": log, "durable": True, "state_log": True}, rundir, "host_app")
        procs.append(cons)
        await cons.start()
        eq = Proc("secs_equipment", {"n": N, "blocks": blocks, "seed": seed_for("q3c", name, rep), "spool": True},
                  rundir, "equipment")
        procs.append(eq)
        eq_port = (await eq.start())["port"]
        gw_cfg["eq_port"] = eq_port
        gw1 = Proc("gateway", gw_cfg, rundir, "gateway_1")
        procs.append(gw1)
        ready1 = await gw1.start()
        t_go = time.monotonic()
        eq.send("GO")
        await _sleep_until(t_go + FAIL_AT)
        t_fail = time.monotonic_ns()
        gw1.signal(getattr(signal, sc["signal"]))
        death = await _wait_state(log, "NDEATH", t_fail, timeout=3 * k + 5)
        if sc["signal"] == "SIGSTOP":
            await asyncio.sleep(0.2)
            gw1.kill()  # watchdog replaces the hung gateway
        else:
            await _sleep_until(t_go + FAIL_AT + 1.0)
        await gw1.wait(5)
        gw2 = Proc("gateway", gw_cfg, rundir, "gateway_2")
        procs.append(gw2)
        t_restart = time.monotonic_ns()
        ready2 = await gw2.start()
        birth = await _wait_state(log, "NBIRTH", t_restart, timeout=10)
        done = await eq.expect("DONE", 60)
        total = done["blocks"][-1]["last"] + 1
        await wait_drain(cons, total, idle=1.5, timeout=30)
        byes = {p.tag: await p.exit() for p in procs if p is not gw1}
    finally:
        await stop_all(procs)
    events = read_events(log)
    states = read_states(log)
    stats = seq_stats([e[0] for e in events], 0, total - 1)
    last_data_before_fail = max((tr for _s, _tc, tr in events if tr <= t_fail), default=None)
    data_while_dead = 0
    if death and birth:
        data_while_dead = sum(1 for _s, _tc, tr in events if death["t"] < tr < birth["t"])
    births = [s for s in states if s["type"] == "NBIRTH"]
    return {
        "scenario": name, "rep": rep, "keepalive_s": k, "signal": sc["signal"],
        "ndeath_after_fail_ms": (death["t"] - t_fail) / 1e6 if death else None,
        "ndeath_after_last_data_ms": (death["t"] - last_data_before_fail) / 1e6 if death and last_data_before_fail else None,
        "ndeath_bdseq": death["bdseq"] if death else None,
        "nbirth_bdseqs": [b["bdseq"] for b in births],
        "bdseq_match": bool(death and births and death["bdseq"] == births[0]["bdseq"]),
        "nbirth_after_restart_ms": (birth["t"] - t_restart) / 1e6 if birth else None,
        "nbirth_after_fail_ms": (birth["t"] - t_fail) / 1e6 if birth else None,
        "ddata_between_ndeath_and_nbirth": data_while_dead,
        **{k2: stats[k2] for k2 in ("expected", "unique", "lost", "duplicates", "out_of_order")},
        "gateway_1": ready1, "gateway_2": ready2, "byes": byes,
    }


PROBE_KS = (1, 2, 3, 4, 5, 6, 8)
STAGGER_S = 1.2  # hang reps start 1.2 s apart, so their failures sample different broker timer phases
PROBE_PER_K = 3


def _mqtt_connect_packet(client_id: str, keepalive: int, will_topic: str, will_payload: bytes) -> bytes:
    """Raw MQTT 3.1.1 CONNECT with clean session and a QoS 1 will (no library involved)."""
    def s16(b: bytes) -> bytes:
        return len(b).to_bytes(2, "big") + b

    body = s16(b"MQTT") + bytes([4, 0b00001110]) + keepalive.to_bytes(2, "big")
    body += s16(client_id.encode()) + s16(will_topic.encode()) + s16(will_payload)
    rl, n = b"", len(body)
    while True:
        d, n = n % 128, n // 128
        rl += bytes([d | (0x80 if n else 0)])
        if not n:
            break
    return b"\x10" + rl + body


async def keepalive_probe(ctx: Ctx) -> dict:
    """Silent clients (CONNECT, then nothing): when does Mosquitto publish their will?"""
    from eqp.io_util import connect_blocking, mqtt_client, subscribe_blocking

    arrivals: dict[str, int] = {}
    loop = asyncio.get_running_loop()
    with Mosquitto(ctx.dir("q3c", "probe_broker")) as broker:
        sub = mqtt_client("q3c-probe-sub")
        sub.on_message = lambda c, u, m: arrivals.setdefault(m.topic, time.monotonic_ns())
        await loop.run_in_executor(None, lambda: connect_blocking(sub, broker.port))
        await loop.run_in_executor(None, lambda: subscribe_blocking(sub, "probe/#", 1))
        conns, t_conn = [], {}
        for k in PROBE_KS:
            for i in range(PROBE_PER_K):
                topic = f"probe/{k}/{i}"
                reader, writer = await asyncio.open_connection("127.0.0.1", broker.port)
                writer.write(_mqtt_connect_packet(f"silent-{k}-{i}", k, topic, b"dead"))
                await reader.readexactly(4)  # CONNACK
                t_conn[topic] = time.monotonic_ns()
                conns.append(writer)
        deadline = time.monotonic() + 1.5 * max(PROBE_KS) + 15
        while len(arrivals) < len(t_conn) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        for w in conns:
            w.close()
        sub.disconnect()
        sub.loop_stop()
    per_k = {}
    for k in PROBE_KS:
        ds = [(arrivals[t] - t_conn[t]) / 1e9 for t in t_conn if t.startswith(f"probe/{k}/") and t in arrivals]
        per_k[str(k)] = {"detect_s": spread(ds), "samples": [round(d, 3) for d in ds], "one_and_half_k": 1.5 * k}
    return per_k


async def run_c(ctx: Ctx) -> dict:
    """crash: repetitions sequential (millisecond timing). hang: repetitions concurrent, each with its
    own broker/equipment/gateway/host (second-scale timing set by the broker's keepalive handling)."""
    runs, notes = [], {}
    for name, sc in SCENARIOS_C.items():
        ctx.log(f"Q3c {name}")
        concurrent = sc["signal"] == "SIGSTOP"
        brokers = [Mosquitto(ctx.dir("q3c", name, f"broker{rep}")) for rep in range(ctx.reps)]
        for b in brokers:
            b.start()
        try:
            if concurrent:
                async def staggered(rep: int) -> dict | None:
                    await asyncio.sleep(rep * STAGGER_S)
                    return await attempt(ctx, f"q3c {name} rep={rep}",
                                         lambda: ndeath_rep(ctx, name, rep, brokers[rep].port))

                runs += [r for r in await asyncio.gather(*(staggered(rep) for rep in range(ctx.reps))) if r]
            else:
                for rep in range(ctx.reps):
                    r = await attempt(ctx, f"q3c {name} rep={rep}",
                                      lambda: ndeath_rep(ctx, name, rep, brokers[rep].port))
                    if r:
                        runs.append(r)
        finally:
            for b in brokers:
                b.stop()
            notes[name] = {"dropped_notices": sum(dropped_warnings(b.log_path) for b in brokers),
                           "concurrent_reps": concurrent}
    ctx.log("Q3c keepalive probe (silent raw MQTT clients)")
    probe = await keepalive_probe(ctx)
    summary = {}
    for name in SCENARIOS_C:
        rs = [r for r in runs if r["scenario"] == name]
        summary[name] = {k: spread(r[k] for r in rs) for k in
                         ("ndeath_after_fail_ms", "ndeath_after_last_data_ms", "nbirth_after_restart_ms",
                          "nbirth_after_fail_ms", "lost", "duplicates", "out_of_order",
                          "ddata_between_ndeath_and_nbirth")}
        summary[name]["bdseq_match_all"] = all(r["bdseq_match"] for r in rs)
        summary[name]["keepalive_s"] = SCENARIOS_C[name]["keepalive"]
        summary[name]["signal"] = SCENARIOS_C[name]["signal"]
        summary[name]["lost_total"] = sum(r["lost"] for r in rs)
        summary[name]["duplicates_total"] = sum(r["duplicates"] for r in rs)
        summary[name]["expected_total"] = sum(r["expected"] for r in rs)
    return {"scenarios": SCENARIOS_C, "fail_at_s": FAIL_AT, "rate": RATE_C, "runs": runs,
            "summary": summary, "broker": notes, "keepalive_probe": probe,
            "probe_per_k": PROBE_PER_K, "stagger_s": STAGGER_S}


# ----------------------------------------------------------------------------------- Q3d
CONFIGS_D = {
    "ack_receipt": "gateway sends S6F12 as soon as the S6F11 is decoded",
    "ack_puback": "gateway sends S6F12 only after the broker's PUBACK (QoS 1)",
}
BLACKHOLE_AT, KILL_AT_D, RESTART_AT_D, DURATION_D = 1.0, 3.0, 3.5, 5.0


async def gwack_rep(ctx: Ctx, name: str, rep: int, broker_port: int) -> dict:
    rundir = ctx.dir("q3d", name, f"rep{rep}")
    log = str(rundir / "host_app.log")
    relay = CountingRelay("127.0.0.1", broker_port)  # gateway <-> broker link only
    rport = await relay.start()
    procs: list[Proc] = []
    gw_cfg = {"n": N, "broker_port": rport, "qos": 1, "ack": "receipt" if name == "ack_receipt" else "puback",
              "keepalive": 5, "bdseq_path": str(rundir / "bdseq"), "client_id": f"q3d-{name}-{rep}-edge"}
    try:
        cons = Proc("spb_consumer", {"n": N, "port": broker_port, "qos": 1, "client_id": f"q3d-{name}-{rep}-host",
                                     "log": log, "durable": True, "state_log": True}, rundir, "host_app")
        procs.append(cons)
        await cons.start()
        eq = Proc("secs_equipment", {"n": N, "seed": seed_for("q3d", name, rep), "spool": True,
                                     "blocks": [{"label": "stream", "rate": RATE_C, "duration": DURATION_D}]},
                  rundir, "equipment")
        procs.append(eq)
        gw_cfg["eq_port"] = (await eq.start())["port"]
        gw1 = Proc("gateway", gw_cfg, rundir, "gateway_1")
        procs.append(gw1)
        await gw1.start()
        t_go = time.monotonic()
        eq.send("GO")
        await _sleep_until(t_go + BLACKHOLE_AT)
        t_bh = time.monotonic_ns()
        relay.blackhole(True)
        await _sleep_until(t_go + KILL_AT_D)
        t_kill = time.monotonic_ns()
        gw1.kill()
        await gw1.wait(5)
        relay.blackhole(False)
        await _sleep_until(t_go + RESTART_AT_D)
        gw2 = Proc("gateway", gw_cfg, rundir, "gateway_2")
        procs.append(gw2)
        ready2 = await gw2.start()
        done = await eq.expect("DONE", 60)
        total = done["blocks"][-1]["last"] + 1
        await wait_drain(cons, total, idle=1.5, timeout=30)
        byes = {p.tag: await p.exit() for p in procs if p is not gw1}
    finally:
        await stop_all(procs)
        await relay.close()
    events = read_events(log)
    stats = seq_stats([e[0] for e in events], 0, total - 1)
    return {"config": name, "rep": rep, **{k: stats[k] for k in ("expected", "unique", "lost", "duplicates",
                                                                  "out_of_order", "lost_ranges")},
            "blackhole_s": (t_kill - t_bh) / 1e9, "equipment": byes.get("equipment"),
            "rsda_after_restart": ready2.get("rsda"),
            "relay_blackholed_bytes": dict(relay.counts["blackholed"]) if "blackholed" in relay.counts else {}}


async def run_d(ctx: Ctx) -> dict:
    runs = []
    for name in CONFIGS_D:
        ctx.log(f"Q3d {name}")
        brokers = [Mosquitto(ctx.dir("q3d", name, f"broker{rep}")) for rep in range(ctx.reps)]
        for b in brokers:
            b.start()
        try:
            got = await asyncio.gather(*(attempt(ctx, f"q3d {name} rep={rep}", lambda rep=rep: gwack_rep(
                ctx, name, rep, brokers[rep].port)) for rep in range(ctx.reps)))
            runs += [r for r in got if r]
        finally:
            for b in brokers:
                b.stop()
    summary = {}
    for name in CONFIGS_D:
        rs = [r for r in runs if r["config"] == name]
        summary[name] = {k: spread(r[k] for r in rs) for k in ("lost", "duplicates", "out_of_order", "blackhole_s")}
        summary[name]["lost_total"] = sum(r["lost"] for r in rs)
        summary[name]["duplicates_total"] = sum(r["duplicates"] for r in rs)
        summary[name]["expected_total"] = sum(r["expected"] for r in rs)
    return {"configs": CONFIGS_D, "rate": RATE_C, "blackhole_at_s": BLACKHOLE_AT, "kill_at_s": KILL_AT_D,
            "restart_at_s": RESTART_AT_D, "duration_s": DURATION_D, "runs": runs, "summary": summary}


async def run(ctx: Ctx) -> dict:
    return {"a_outage": await run_a(ctx), "b_coalescing": await run_b(ctx), "c_ndeath": await run_c(ctx),
            "d_gateway_ack": await run_d(ctx)}
