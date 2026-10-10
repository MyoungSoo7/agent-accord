"""Process entry points used by the experiments:  python -m eqp.roles <role> '<json config>'

Protocol with the orchestrator: the role prints `READY {json}` on stdout when set up, then reads
commands from stdin (`GO`, `FAST <json>`, `EXIT`), printing `DONE {json}` after a schedule and
`BYE {json}` before exiting. Received events are written by io_util.Recorder to cfg["log"].
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import sys
import time

from .workload import Block, EventSource, var_specs


def out(tag: str, **payload) -> None:
    sys.stdout.write(f"{tag} {json.dumps(payload, default=str)}\n")
    sys.stdout.flush()


async def stdin_reader() -> asyncio.StreamReader:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    return reader


async def next_cmd(reader: asyncio.StreamReader) -> tuple[str, dict]:
    line = await reader.readline()
    if not line:
        return "EXIT", {}
    head, _, rest = line.decode().strip().partition(" ")
    return head, (json.loads(rest) if rest else {})


async def run_schedule(emit, source: EventSource, blocks: list[Block], *, gap: float, is_async: bool,
                       start_seq: int = 0, seed: int = 0) -> list[dict]:
    """Paced blocks: Poisson arrivals at the block's mean rate (seeded exponential gaps on an absolute
    schedule, catch-up when late). Poisson rather than periodic so that the arrival phase does not
    lock onto periodic loops such as an OPC UA publishing interval. rate 0 = unpaced burst."""
    seq = start_seq
    res = []
    for bi, b in enumerate(blocks):
        n = b.n_events()
        first = seq
        t0 = time.monotonic()
        if b.rate > 0:
            rng = random.Random(seed * 1000 + bi)
            t_next = t0
            for _ in range(n):
                d = t_next - time.monotonic()
                # behind schedule: still yield once, so tasks sharing this event loop (HSMS sender,
                # asyncua publishing loop) are not starved while the generator catches up
                await asyncio.sleep(d if d > 0 else 0)
                r = emit(source.make(seq))
                if is_async:
                    await r
                seq += 1
                t_next += rng.expovariate(b.rate)
        else:
            for k in range(n):
                r = emit(source.make(seq))
                if is_async:
                    await r
                seq += 1
                if k % 20 == 19:
                    await asyncio.sleep(0)
        res.append({"label": b.label, "rate": b.rate, "first": first, "last": seq - 1,
                    "t0": t0, "t1": time.monotonic()})
        if gap:
            await asyncio.sleep(gap)
    return res


async def serve_status(stdin: asyncio.StreamReader, rec=None) -> None:
    """Answer STATUS with the number of recorded events until EXIT (or stdin EOF)."""
    while True:
        cmd, _ = await next_cmd(stdin)
        if cmd == "EXIT":
            return
        if cmd == "STATUS":
            out("STATUS", count=rec.count if rec is not None else 0)


def blocks_of(cfg: dict) -> list[Block]:
    return [Block(**b) for b in cfg.get("blocks", [])]


# ----------------------------------------------------------------------------- SECS/GEM

async def secs_equipment(cfg: dict) -> None:
    from .gem import Equipment

    eq = Equipment(cfg["n"], spool=cfg.get("spool", False), window=cfg.get("window", 1))
    port = await eq.listen(port=cfg.get("port", 0))
    out("READY", port=port)
    stdin = await stdin_reader()
    src = EventSource(cfg["n"], cfg.get("seed", 1))
    while True:
        cmd, _arg = await next_cmd(stdin)
        if cmd != "GO":
            break
        res = await run_schedule(eq.emit, src, blocks_of(cfg), gap=cfg.get("gap", 0.0), is_async=False,
                                 seed=cfg.get("seed", 1))
        out("DONE", blocks=res, stats=eq.stats)
    out("BYE", stats=eq.stats, queued=len(eq.queue))
    await eq.close()


async def secs_host(cfg: dict) -> None:
    from .gem import Host
    from .io_util import Recorder

    rec = Recorder(cfg["log"], durable=cfg.get("durable", False))
    mono = time.monotonic_ns
    host = Host(cfg["n"], lambda v: rec.event(v[0], v[1], mono()))
    deadline = time.monotonic() + 10
    while True:
        try:
            acks = await host.connect("127.0.0.1", cfg["port"], define_reports=cfg.get("define_reports", True),
                                      request_spool=cfg.get("request_spool", False))
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(0.05)
    out("READY", acks=acks)
    stdin = await stdin_reader()
    await serve_status(stdin, rec)
    rec.close()
    out("BYE", received=host.received)
    await host.close()


# ----------------------------------------------------------------------------- OPC UA

async def opcua_server(cfg: dict) -> None:
    from .opcua_io import EquipmentServer

    srv = EquipmentServer(cfg["n_max"])
    url = await srv.start(cfg["port"])
    out("READY", url=url, ns=srv.ns)
    stdin = await stdin_reader()
    n = cfg.get("n", cfg["n_max"])
    src = EventSource(n, cfg.get("seed", 1))
    while True:
        cmd, arg = await next_cmd(stdin)
        if cmd == "GO":
            res = await run_schedule(lambda v: srv.write_event(v, n), src, blocks_of(cfg),
                                     gap=cfg.get("gap", 0.0), is_async=True, seed=cfg.get("seed", 1))
            out("DONE", blocks=res)
        elif cmd == "FAST":  # Q3b: write FastSignal = 0,1,2,... every `period` seconds
            count, period = arg["count"], arg["period"]
            t0 = time.monotonic()
            for k in range(count):
                d = t0 + k * period - time.monotonic()
                if d > 0:
                    await asyncio.sleep(d)
                await srv.write_fast(float(k))
            out("DONE", written=count, seconds=time.monotonic() - t0)
        else:
            break
    out("BYE")
    await srv.stop()


async def opcua_client(cfg: dict) -> None:
    from asyncua import ua

    from .io_util import Recorder
    from .opcua_io import FAST_NAME, Consumer, TagEventAssembler

    rec = Recorder(cfg["log"], durable=cfg.get("durable", False))
    ns = cfg.get("ns", 2)
    if cfg.get("fast"):  # Q3b: record every FastSignal value with its receive time
        values: list = []

        def on_items(items, t):
            for it in items:
                values.append((it.Value.Value.Value, t))

        cons = Consumer(cfg["url"], on_items)
        await cons.connect()
        info = await cons.subscribe([ua.NodeId(FAST_NAME, ns)], [7], publishing_interval=cfg["publishing_interval"],
                                    queue_size=cfg["queue_size"], keepalive_count=cfg.get("keepalive_count", 100))
    else:
        asm = TagEventAssembler(rec.event)
        cons = Consumer(cfg["url"], asm.on_items)
        await cons.connect()
        specs = var_specs(cfg["n"])
        info = await cons.subscribe([ua.NodeId(s.name, ns) for s in specs], [s.index for s in specs],
                                    publishing_interval=cfg["publishing_interval"], queue_size=cfg["queue_size"],
                                    keepalive_count=cfg.get("keepalive_count", 1000))
    out("READY", **info)
    stdin = await stdin_reader()
    await serve_status(stdin, rec)
    rec.close()
    extra = {}
    if cfg.get("fast"):
        with open(cfg["log"] + ".fast.json", "w") as f:
            json.dump(values, f)
        extra["values"] = len(values)
    if not cfg.get("fast"):
        extra["mismatched_batches"] = asm.mismatched_batches
    out("BYE", notifications=cons.notifications, publish_responses=cons.publish_responses,
        keepalives=cons.keepalives, **extra)
    await cons.disconnect()


# ----------------------------------------------------------------------------- MQTT / Sparkplug B

async def spb_publisher(cfg: dict) -> None:
    from . import spb
    from .io_util import connect_blocking, mqtt_client

    n, qos = cfg["n"], cfg["qos"]
    group, edge, device = cfg.get("group", "FAB1"), cfg.get("edge", "EDGE-ETCH01"), cfg.get("device", "EQP-ETCH01")
    codec = spb.DeviceCodec(var_specs(n), mode=cfg.get("mode", "alias"), metric_ts=cfg.get("metric_ts", True))
    t_ddata = spb.topic(group, "DDATA", edge, device)
    seq = [0]

    def next_seq():
        s = seq[0]
        seq[0] = (s + 1) % 256
        return s

    c = mqtt_client(cfg.get("client_id", edge), clean_session=True, nodelay=cfg.get("nodelay", True))
    c.will_set(spb.topic(group, "NDEATH", edge), spb.ndeath(0), qos=1)

    def on_connect(cl, u, f, rc):
        seq[0] = 0
        cl.publish(spb.topic(group, "NBIRTH", edge), spb.nbirth(0, spb.now_ms()), qos=0)
        next_seq()
        cl.publish(spb.topic(group, "DBIRTH", edge, device), codec.dbirth(None, next_seq()), qos=0)

    c.on_connect = on_connect
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: connect_blocking(c, cfg["port"], keepalive=cfg.get("keepalive", 30)))
    out("READY")
    stdin = await stdin_reader()
    src = EventSource(n, cfg.get("seed", 1))
    publish = c.publish

    def emit(values):
        publish(t_ddata, codec.ddata(values, next_seq()), qos=qos)

    while True:
        cmd, _ = await next_cmd(stdin)
        if cmd != "GO":
            break
        res = await run_schedule(emit, src, blocks_of(cfg), gap=cfg.get("gap", 0.0), is_async=False,
                                 seed=cfg.get("seed", 1))
        out("DONE", blocks=res)
    out("BYE")
    c.disconnect()
    c.loop_stop()


async def spb_consumer(cfg: dict) -> None:
    from . import spb
    from .io_util import Recorder, connect_blocking, mqtt_client, subscribe_blocking

    rec = Recorder(cfg["log"], durable=cfg.get("durable", False))
    codec = spb.DeviceCodec(var_specs(cfg["n"]))  # alias/name table known statically (see report)
    mono = time.monotonic_ns
    state_log = cfg.get("state_log", False)
    counts = {"DDATA": 0, "other": 0, "dup_flag": 0}

    def on_message(cl, u, msg):
        mtype = msg.topic.split("/")[2]
        if msg.dup:
            counts["dup_flag"] += 1
        if mtype == "DDATA":
            counts["DDATA"] += 1
            values, _pseq, _ts = codec.decode(msg.payload)
            rec.event(values[0], values[1], mono())  # receive time taken after decoding, as for SECS/OPC UA
        else:
            t = mono()
            counts["other"] += 1
            if state_log:
                p = spb.parse(msg.payload)
                bd = next((m.long_value for m in p.metrics if m.name == "bdSeq"), None)
                rec.state(t=t, type=mtype, bdseq=bd, seq=p.seq if p.HasField("seq") else None, topic=msg.topic)

    c = mqtt_client(cfg["client_id"], clean_session=cfg.get("clean_session", True), nodelay=cfg.get("nodelay", True))
    c.on_message = on_message
    loop = asyncio.get_running_loop()
    flags = await loop.run_in_executor(None, lambda: connect_blocking(c, cfg["port"], keepalive=30))
    granted = await loop.run_in_executor(None, lambda: subscribe_blocking(
        c, f"{spb.NAMESPACE}/{cfg.get('group', 'FAB1')}/#", cfg["qos"]))
    out("READY", session_present=flags.get("session present"), granted=granted)
    stdin = await stdin_reader()
    await serve_status(stdin, rec)
    c.disconnect()
    c.loop_stop()
    rec.close()
    out("BYE", **counts)


async def gateway(cfg: dict) -> None:
    from .gateway import Gateway

    gw = Gateway(cfg["n"], eq_port=cfg["eq_port"], broker_port=cfg["broker_port"], qos=cfg.get("qos", 1),
                 keepalive=cfg.get("keepalive", 5), ack=cfg.get("ack", "puback"), bdseq_path=cfg.get("bdseq_path"),
                 client_id=cfg.get("client_id"), nodelay=cfg.get("nodelay", True))
    acks = await gw.start()
    out("READY", **acks)
    stdin = await stdin_reader()
    await serve_status(stdin)
    out("BYE", published=gw.published)
    await gw.stop()


ROLES = {f.__name__: f for f in (secs_equipment, secs_host, opcua_server, opcua_client, spb_publisher,
                                 spb_consumer, gateway)}


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    role, cfg = sys.argv[1], json.loads(sys.argv[2])
    asyncio.run(ROLES[role](cfg))


if __name__ == "__main__":
    main()
