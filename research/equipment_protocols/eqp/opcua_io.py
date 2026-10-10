"""OPC UA (asyncua) side: equipment server exposing the event variables as nodes, and a
consumer that subscribes with raw service calls (to see RevisedQueueSize and raw PublishResults).

Modelling ("tag model"): every event variable is one Variable node (string NodeId = the variable
name). One event = N+2 server-side writes, in the order process variables, EventTimeNs, EventSeq.
asyncua runs its publishing loop as an asyncio task and the writes do not yield, so all writes of
one event land in the same publishing cycle. Within one PublishResult asyncua lists the queued
notifications item by item, each in write order, so the consumer pairs the k-th EventSeq with the
k-th EventTimeNs of the same PublishResult (valid as long as no queue overflow coalesces values).
SecurityPolicy None / MessageSecurityMode None.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

from asyncua import Client, Server, ua

from .workload import var_specs

for _name in ("asyncua",):
    logging.getLogger(_name).setLevel(logging.ERROR)

VT = {"U4": ua.VariantType.UInt32, "U8": ua.VariantType.UInt64, "F8": ua.VariantType.Double,
      "A": ua.VariantType.String}
INIT = {"U4": 0, "U8": 0, "F8": 0.0, "A": ""}
SEQ_SENTINEL = 0xFFFFFFFF  # initial EventSeq value, never a real sequence number
NS_URI = "urn:eqp-sim:etch01"
FAST_NAME = "PM1/AI/FastSignal"


class EquipmentServer:
    def __init__(self, n_max: int) -> None:
        self.specs = var_specs(n_max)
        self.nodeids: list[ua.NodeId] = []
        self.fast_nodeid: ua.NodeId | None = None
        self.server: Server | None = None
        self.ns = 2

    async def start(self, port: int) -> str:
        server = Server()
        await server.init()
        url = f"opc.tcp://127.0.0.1:{port}/eqp"
        server.set_endpoint(url)
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        self.ns = await server.register_namespace(NS_URI)
        obj = await server.nodes.objects.add_object(self.ns, "EQP-ETCH01")
        for s in self.specs:
            init = SEQ_SENTINEL if s.index == 0 else INIT[s.kind]
            node = await obj.add_variable(ua.NodeId(s.name, self.ns), s.name, ua.Variant(init, VT[s.kind]))
            self.nodeids.append(node.nodeid)
        fast = await obj.add_variable(ua.NodeId(FAST_NAME, self.ns), "FastSignal",
                                      ua.Variant(-1.0, ua.VariantType.Double))
        self.fast_nodeid = fast.nodeid
        await server.start()
        self.server = server
        return url

    async def reset(self) -> None:
        """EventSeq back to the sentinel, so a new subscription's initial values are not an event."""
        now = datetime.now(timezone.utc)
        for i, v in ((0, SEQ_SENTINEL), (1, 0)):
            await self.server.write_attribute_value(  # type: ignore[union-attr]
                self.nodeids[i], ua.DataValue(Value=ua.Variant(v, VT[self.specs[i].kind]),
                                              SourceTimestamp=now, ServerTimestamp=now))

    def order(self, n: int) -> list[int]:
        return list(range(2, 2 + n)) + [1, 0]

    async def write_event(self, values: list, n: int) -> None:
        now = datetime.now(timezone.utc)
        write = self.server.write_attribute_value  # type: ignore[union-attr]
        specs, nodeids = self.specs, self.nodeids
        for i in self.order(n):
            await write(nodeids[i], ua.DataValue(Value=ua.Variant(values[i], VT[specs[i].kind]),
                                                 SourceTimestamp=now, ServerTimestamp=now))

    async def write_fast(self, value: float) -> None:
        now = datetime.now(timezone.utc)
        await self.server.write_attribute_value(  # type: ignore[union-attr]
            self.fast_nodeid, ua.DataValue(Value=ua.Variant(value, ua.VariantType.Double),
                                           SourceTimestamp=now, ServerTimestamp=now))

    async def stop(self) -> None:
        if self.server:
            await self.server.stop()


class Consumer:
    """Subscribes to node ids; on_items(list[MonitoredItemNotification], t_recv_ns) per PublishResult."""

    def __init__(self, url: str, on_items: Callable[[list, int], None]) -> None:
        self.url = url
        self.on_items = on_items
        self.client: Client | None = None
        self.publish_responses = 0
        self.keepalives = 0
        self.notifications = 0

    async def connect(self) -> None:
        self.client = Client(self.url, timeout=10, watchdog_intervall=3600.0)
        await self.client.connect()

    async def subscribe(self, nodeids: list[ua.NodeId], handles: list[int], *, publishing_interval: float,
                        queue_size: int, keepalive_count: int = 1000, lifetime_count: int = 10000) -> dict:
        session = self.client.uaclient.session  # type: ignore[union-attr]
        params = ua.CreateSubscriptionParameters(
            RequestedPublishingInterval=publishing_interval, RequestedLifetimeCount=lifetime_count,
            RequestedMaxKeepAliveCount=keepalive_count, MaxNotificationsPerPublish=0,
            PublishingEnabled=True, Priority=0)
        sres = await session.create_subscription(params, self._on_publish)
        items = [ua.MonitoredItemCreateRequest(
            ItemToMonitor=ua.ReadValueId(NodeId=nid, AttributeId=ua.AttributeIds.Value),
            MonitoringMode=ua.MonitoringMode.Reporting,
            RequestedParameters=ua.MonitoringParameters(ClientHandle=h, SamplingInterval=0.0, Filter=None,
                                                        QueueSize=queue_size, DiscardOldest=True))
            for nid, h in zip(nodeids, handles)]
        results = await session.create_monitored_items(ua.CreateMonitoredItemsParameters(
            SubscriptionId=sres.SubscriptionId, TimestampsToReturn=ua.TimestampsToReturn.Both,
            ItemsToCreate=items))
        bad = [r.StatusCode for r in results if not r.StatusCode.is_good()]
        if bad:
            raise RuntimeError(f"CreateMonitoredItems failed: {bad[:3]}")
        return {
            "revised_publishing_interval": sres.RevisedPublishingInterval,
            "revised_queue_size": sorted({r.RevisedQueueSize for r in results}),
            "revised_sampling_interval": sorted({r.RevisedSamplingInterval for r in results}),
        }

    def _on_publish(self, result: ua.PublishResult) -> None:
        t = time.monotonic_ns()
        self.publish_responses += 1
        data = result.NotificationMessage.NotificationData
        if not data:
            self.keepalives += 1
            return
        for notif in data:
            if isinstance(notif, ua.DataChangeNotification):
                self.notifications += len(notif.MonitoredItems)
                self.on_items(notif.MonitoredItems, t)

    async def disconnect(self) -> None:
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass


class TagEventAssembler:
    """Re-assembles events from per-variable notifications (handle = variable index)."""

    def __init__(self, record: Callable[[int, int, int], None]) -> None:
        self.record = record
        self.values_seen = 0
        self.mismatched_batches = 0

    def on_items(self, items: list, t_recv: int) -> None:
        self.values_seen += len(items)
        seqs, times = [], []
        for it in items:
            if it.ClientHandle == 0:
                seqs.append(it.Value.Value.Value)
            elif it.ClientHandle == 1:
                times.append(it.Value.Value.Value)
        if len(seqs) != len(times):
            self.mismatched_batches += 1
        for seq, t_created in zip(seqs, times):
            if seq != SEQ_SENTINEL:
                self.record(seq, t_created, t_recv)
