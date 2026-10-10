"""SECS/GEM -> MQTT Sparkplug B edge gateway.

Equipment side: GEM host (HSMS active). Sets up the dynamic event report, reads current values
with S1F3, then asks for spooled data with S6F23.
MQTT side: Sparkplug B edge node `edge` with one device `device`. CONNECT carries the NDEATH
Last Will (bdSeq); after CONNACK it publishes NBIRTH (seq 0, bdSeq) and DBIRTH (current values).
Each S6F11 becomes one DDATA. With ack="puback" the S6F12 to the equipment is sent only after the
broker's PUBACK (QoS 1), so an event leaves the equipment's responsibility only once the broker
has it; with ack="receipt" S6F12 is sent as soon as the S6F11 is decoded.
bdSeq is persisted in a file so a restarted gateway process continues the sequence.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from . import secs2, spb
from .gem import Host
from .io_util import AckWaiter, connect_blocking, mqtt_client
from .workload import var_specs


class Gateway:
    def __init__(self, n: int, *, eq_port: int, broker_port: int, qos: int = 1, keepalive: int = 5,
                 ack: str = "puback", bdseq_path: str | None = None, group: str = "FAB1",
                 edge: str = "GW-ETCH01", device: str = "EQP-ETCH01", client_id: str | None = None,
                 mode: str = "alias", nodelay: bool = True) -> None:
        self.n = n
        self.specs = var_specs(n)
        self.eq_port, self.broker_port = eq_port, broker_port
        self.qos, self.keepalive, self.ack = qos, keepalive, ack
        self.bdseq_path = Path(bdseq_path) if bdseq_path else None
        self.group, self.edge, self.device = group, edge, device
        self.client_id = client_id or f"{edge}"
        self.codec = spb.DeviceCodec(self.specs, mode=mode)
        self.nodelay = nodelay
        self.t_ddata = spb.topic(group, "DDATA", edge, device)
        self.ready = asyncio.Event()
        self._seq = 0
        self._seq_lock = threading.Lock()
        self.current: list | None = None
        self.published = 0
        self.host: Host | None = None
        self.mqtt = None
        self.bdseq = 0

    def _next_seq(self) -> int:
        with self._seq_lock:
            s = self._seq
            self._seq = (self._seq + 1) % 256
            return s

    def _load_bdseq(self) -> int:
        prev = -1
        if self.bdseq_path and self.bdseq_path.exists():
            prev = int(self.bdseq_path.read_text().strip() or -1)
        b = (prev + 1) % 256
        if self.bdseq_path:
            self.bdseq_path.write_text(str(b))
        return b

    def _births(self, client) -> None:
        with self._seq_lock:
            self._seq = 0
        client.publish(spb.topic(self.group, "NBIRTH", self.edge), spb.nbirth(self.bdseq), qos=0)
        self._next_seq()  # NBIRTH used seq 0
        client.publish(spb.topic(self.group, "DBIRTH", self.edge, self.device),
                       self.codec.dbirth(self.current, self._next_seq()), qos=0)

    async def start(self) -> dict:
        loop = asyncio.get_running_loop()
        self.host = Host(self.n, self._on_event)
        acks = await self.host.connect("127.0.0.1", self.eq_port, define_reports=True, request_spool=False)
        r = await self.host.conn.request(1, 3, secs2.encode(secs2.L(*(secs2.U4(s.vid) for s in self.specs))))
        self.current = [None if v.fmt == secs2.Fmt.L else v.scalar for v in r.item().items]

        self.bdseq = self._load_bdseq()
        c = mqtt_client(self.client_id, clean_session=True, nodelay=self.nodelay)
        c.will_set(spb.topic(self.group, "NDEATH", self.edge), spb.ndeath(self.bdseq), qos=1, retain=False)
        self.acks = AckWaiter(loop)
        c.on_publish = self.acks.on_publish
        c.on_connect = lambda cl, u, f, rc: self._births(cl) if rc == 0 else None
        self.mqtt = c
        await loop.run_in_executor(None, lambda: connect_blocking(c, self.broker_port, keepalive=self.keepalive))
        self.ready.set()
        r = await self.host.conn.request(6, 23, secs2.encode(secs2.U1(0)))
        acks["rsda"] = r.item().scalar
        acks["bdseq"] = self.bdseq
        return acks

    async def _on_event(self, values: list) -> None:
        await self.ready.wait()
        self.current = values
        info = self.mqtt.publish(self.t_ddata, self.codec.ddata(values, self._next_seq()), qos=self.qos)
        self.published += 1
        if self.ack == "puback" and self.qos > 0:
            await self.acks.wait(info.mid)

    async def stop(self) -> None:
        if self.mqtt is not None:
            # graceful shutdown: publish NDEATH ourselves, because a clean DISCONNECT discards the will
            info = self.mqtt.publish(spb.topic(self.group, "NDEATH", self.edge), spb.ndeath(self.bdseq), qos=1)
            try:
                await asyncio.wait_for(self.acks.wait(info.mid), 2.0)
            except asyncio.TimeoutError:
                pass
            self.mqtt.disconnect()
            self.mqtt.loop_stop()
        if self.host is not None:
            await self.host.close()
