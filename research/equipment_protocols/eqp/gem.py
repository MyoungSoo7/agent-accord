"""Minimal GEM (SEMI E30-style) equipment simulator and host on top of :mod:`eqp.hsms`.

Implemented messages (formats follow the secsgem 0.3.0 data-item definitions):

    S1F13/S1F14  establish communication  (host -> equipment; COMMACK B)
    S2F33/S2F34  define report            (DATAID, [(RPTID, [VID])]; DRACK B)
    S2F35/S2F36  link event report        (DATAID, [(CEID, [RPTID])]; LRACK B)
    S2F37/S2F38  enable event report      (CEED BOOLEAN, [CEID]; ERACK B)
    S6F11/S6F12  event report send/ack    (DATAID, CEID, [(RPTID, [V])]; ACKC6 B)
    S6F23/S6F24  request spooled data     (RSDC U1: 0 transmit / 1 purge; RSDA B: 0 ok, 2 no data)
    S1F1/S1F2    are you there
    S1F3/S1F4    selected equipment status (current value of each requested VID)

Spooling model (simplified from E30): while the host is not COMMUNICATING the equipment appends
S6F11 events to a FIFO spool instead of discarding them. An S6F11 that was in flight when the link
dropped (no S6F12 received) is put back at the head of the spool, so delivery is at-least-once and
the host must de-duplicate by DATAID. After S1F13 the spooled data is held until the host sends
S6F23 RSDC=0; events created meanwhile are appended behind the spool, which preserves order.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import struct
from collections import deque
from typing import Awaitable, Callable

from . import hsms, secs2
from .secs2 import Fmt
from .workload import CEID, RPTID, VarSpec, var_specs

log = logging.getLogger("gem")

MDLN = "ETCH-SIM"
SOFTREV = "0.1"


def _hdr(fmt: Fmt, n: int) -> bytes:
    return secs2.item_header(fmt, n)


_U4 = _hdr(Fmt.U4, 4)
_U8 = _hdr(Fmt.U8, 8)
_F8 = _hdr(Fmt.F8, 8)


def value_item(spec: VarSpec, v) -> secs2.Item:
    if spec.kind == "U4":
        return secs2.U4(v)
    if spec.kind == "U8":
        return secs2.U8(v)
    if spec.kind == "F8":
        return secs2.F8(v)
    return secs2.A(v)


class S6F11Encoder:
    """Fast byte-level S6F11 body encoder for one fixed report; equal to secs2.encode of the tree."""

    def __init__(self, specs: list[VarSpec], ceid: int = CEID, rptid: int = RPTID) -> None:
        self.specs = specs
        self.prefix_mid = _U4 + struct.pack(">I", ceid) + _hdr(Fmt.L, 1) + _hdr(Fmt.L, 2) + _U4 + struct.pack(">I", rptid) + _hdr(Fmt.L, len(specs))
        self.kinds = [s.kind for s in specs]

    def encode(self, dataid: int, values: list) -> bytes:
        out = [_hdr(Fmt.L, 3), _U4, struct.pack(">I", dataid), self.prefix_mid]
        for kind, v in zip(self.kinds, values):
            if kind == "F8":
                out.append(_F8)
                out.append(struct.pack(">d", v))
            elif kind == "U4":
                out.append(_U4)
                out.append(struct.pack(">I", v))
            elif kind == "U8":
                out.append(_U8)
                out.append(struct.pack(">Q", v))
            else:
                b = v.encode("ascii")
                out.append(_hdr(Fmt.A, len(b)))
                out.append(b)
        return b"".join(out)

    def tree(self, dataid: int, values: list) -> secs2.Item:
        return secs2.L(
            secs2.U4(dataid),
            secs2.U4(CEID),
            secs2.L(secs2.L(secs2.U4(RPTID), secs2.L(*(value_item(s, v) for s, v in zip(self.specs, values))))),
        )


ACK_OK = secs2.encode(secs2.B([0]))


class Equipment:
    """Passive HSMS entity + GEM event reporting with optional spooling."""

    def __init__(self, n_vars: int, *, spool: bool = False, window: int = 1, t3: float = 45.0,
                 spool_max: int = 1_000_000, linktest_interval: float | None = None) -> None:
        self.specs = var_specs(n_vars)
        self.known_vids = {s.vid for s in self.specs}
        self.encoder = S6F11Encoder(self.specs)
        self.reports: dict[int, list[int]] = {}
        self.links: dict[int, list[int]] = {}
        self.enabled: set[int] = set()
        self.last_values: list = [{"U4": 0, "U8": 0, "F8": 0.0, "A": ""}[s.kind] for s in self.specs]
        self.vid_index = {s.vid: s.index for s in self.specs}
        self.spool_enabled = spool
        self.spool_max = spool_max
        self.window = window
        self.t3 = t3
        self.linktest_interval = linktest_interval
        self.conn: hsms.HsmsConnection | None = None
        self.communicating = False
        self.queue: deque[list] = deque()
        self.spool_active = False  # queue holds spooled data (sent only after S6F23)
        self.unload_granted = False
        self._wake = asyncio.Event()
        self._inflight = 0
        self.server: asyncio.base_events.Server | None = None
        self.stats = dict(generated=0, acked=0, discarded=0, spooled=0, respooled=0, inflight_lost=0,
                          t3_timeouts=0, max_queue=0, connections=0, spool_overflow=0)
        self._sender_tasks: list[asyncio.Task] = []

    # -- network ---------------------------------------------------------------
    async def listen(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self.server = await asyncio.start_server(self._on_connect, host, port)
        for _ in range(self.window):
            self._sender_tasks.append(asyncio.create_task(self._sender()))
        return self.server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        for t in self._sender_tasks:
            t.cancel()
        if self.conn:
            self.conn.close()
        if self.server:
            self.server.close()

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.conn is not None and not self.conn.closed.is_set():
            writer.close()  # single session (HSMS-SS)
            return
        self.stats["connections"] += 1
        conn = hsms.HsmsConnection(reader, writer, active=False, on_primary=self._on_primary, t3=self.t3,
                                   linktest_interval=self.linktest_interval, name="equipment")
        self.conn = conn
        conn.start()
        await conn.wait_closed()
        self._on_disconnect()

    def _on_disconnect(self) -> None:
        self.communicating = False
        self.unload_granted = False
        if self.spool_enabled:
            if self.queue:
                self.spool_active = True
        else:
            self.stats["discarded"] += len(self.queue)
            self.queue.clear()
        self._wake.set()

    # -- event intake ----------------------------------------------------------
    def emit(self, values: list) -> None:
        self.stats["generated"] += 1
        self.last_values = values
        if self.communicating and not self.spool_active:
            self.queue.append(values)
        elif self.spool_enabled:
            if len(self.queue) >= self.spool_max:
                self.queue.popleft()
                self.stats["spool_overflow"] += 1
            self.queue.append(values)
            self.spool_active = True
            self.stats["spooled"] += 1
        else:
            self.stats["discarded"] += 1
            return
        if len(self.queue) > self.stats["max_queue"]:
            self.stats["max_queue"] = len(self.queue)
        self._wake.set()

    def _may_send(self) -> bool:
        return (self.communicating and bool(self.queue) and self.conn is not None
                and (not self.spool_active or self.unload_granted))

    async def _sender(self) -> None:
        while True:
            while not self._may_send():
                self._wake.clear()
                await self._wake.wait()
            conn = self.conn
            values = self.queue.popleft()
            body = self.encoder.encode(values[0], values)
            try:
                reply = await conn.request(6, 11, body)  # type: ignore[union-attr]
                if reply.function == 12:
                    self.stats["acked"] += 1
                if self.spool_active and not self.queue:
                    self.spool_active = False
                    self.unload_granted = False
            except (hsms.ConnectionClosed, hsms.T3Timeout, hsms.HsmsError) as exc:
                if isinstance(exc, hsms.T3Timeout):
                    self.stats["t3_timeouts"] += 1
                if self.spool_enabled:
                    self.queue.appendleft(values)
                    self.spool_active = True
                    self.stats["respooled"] += 1
                else:
                    self.stats["inflight_lost"] += 1
                if isinstance(exc, hsms.T3Timeout) and conn is not None:
                    conn.close()

    # -- host requests -----------------------------------------------------------
    async def _on_primary(self, conn: hsms.HsmsConnection, msg: hsms.Message) -> None:
        sf = (msg.stream, msg.function)
        item = msg.item()
        if sf == (1, 13):
            conn.reply(msg, 14, secs2.encode(secs2.L(secs2.B([0]), secs2.L(secs2.A(MDLN), secs2.A(SOFTREV)))))
            self.communicating = True
            self._wake.set()
        elif sf == (1, 1):
            conn.reply(msg, 2, secs2.encode(secs2.L(secs2.A(MDLN), secs2.A(SOFTREV))))
        elif sf == (1, 3):
            out = []
            for v in item.items:  # type: ignore[union-attr]
                idx = self.vid_index.get(v.scalar)
                out.append(secs2.L() if idx is None else value_item(self.specs[idx], self.last_values[idx]))
            conn.reply(msg, 4, secs2.encode(secs2.L(*out)))
        elif sf == (2, 33):
            conn.reply(msg, 34, secs2.encode(secs2.B([self._define_report(item)])))
        elif sf == (2, 35):
            conn.reply(msg, 36, secs2.encode(secs2.B([self._link_report(item)])))
        elif sf == (2, 37):
            conn.reply(msg, 38, secs2.encode(secs2.B([self._enable_report(item)])))
        elif sf == (6, 23):
            rsdc = item.scalar if item is not None else 0
            if not (self.spool_active and self.queue):
                conn.reply(msg, 24, secs2.encode(secs2.B([2])))  # no spooled data
            elif rsdc == 1:
                self.stats["discarded"] += len(self.queue)
                self.queue.clear()
                self.spool_active = False
                conn.reply(msg, 24, secs2.encode(secs2.B([0])))
            else:
                self.unload_granted = True
                conn.reply(msg, 24, secs2.encode(secs2.B([0])))
                self._wake.set()
        elif msg.wbit:
            conn.reply(msg, 0)  # abort: not supported here

    def _define_report(self, item: secs2.Item | None) -> int:
        _dataid, defs = item.items  # type: ignore[union-attr]
        if not defs.items:  # zero-length report list: delete all reports and their links
            self.reports.clear()
            self.links.clear()
            return 0
        parsed = []
        for d in defs.items:
            rptid_i, vids_i = d.items
            rptid = rptid_i.scalar
            vids = [v.scalar for v in vids_i.items]
            if vids and rptid in self.reports:
                return 3
            if any(v not in self.known_vids for v in vids):
                return 4
            parsed.append((rptid, vids))
        for rptid, vids in parsed:
            if vids:
                self.reports[rptid] = vids
            else:
                self.reports.pop(rptid, None)
                for ceid in list(self.links):
                    self.links[ceid] = [r for r in self.links[ceid] if r != rptid]
        return 0

    def _link_report(self, item: secs2.Item | None) -> int:
        _dataid, links = item.items  # type: ignore[union-attr]
        parsed = []
        for d in links.items:
            ceid_i, rpts_i = d.items
            ceid = ceid_i.scalar
            rptids = [r.scalar for r in rpts_i.items]
            if ceid != CEID:
                return 4
            if rptids and ceid in self.links:
                return 3
            if any(r not in self.reports for r in rptids):
                return 5
            parsed.append((ceid, rptids))
        for ceid, rptids in parsed:
            if rptids:
                self.links[ceid] = rptids
            else:
                self.links.pop(ceid, None)
        return 0

    def _enable_report(self, item: secs2.Item | None) -> int:
        ceed_i, ceids_i = item.items  # type: ignore[union-attr]
        ceids = [c.scalar for c in ceids_i.items] or [CEID]
        if any(c != CEID for c in ceids):
            return 1
        for c in ceids:
            (self.enabled.add if ceed_i.scalar else self.enabled.discard)(c)
        return 0


EventCallback = Callable[[list], "Awaitable[None] | None"]


class Host:
    """Active HSMS entity + GEM host: sets up the dynamic event report and consumes S6F11."""

    def __init__(self, n_vars: int, on_event: EventCallback, *, t3: float = 45.0) -> None:
        self.specs = var_specs(n_vars)
        self.on_event = on_event
        self.t3 = t3
        self.conn: hsms.HsmsConnection | None = None
        self.report_vids: dict[int, list[int]] = {}
        self.vid_pos = {s.vid: s.index for s in self.specs}
        self.received = 0
        self._async_cb = inspect.iscoroutinefunction(on_event)

    async def connect(self, host: str, port: int, *, define_reports: bool = True,
                      request_spool: bool = False) -> dict:
        await self.open(host, port)
        return await self.setup(define_reports=define_reports, request_spool=request_spool)

    async def open(self, host: str, port: int) -> None:
        """TCP connect + HSMS Select."""
        self.conn = await hsms.connect(host, port, on_primary=self._on_primary, t3=self.t3, name="host")

    async def setup(self, *, define_reports: bool = True, request_spool: bool = False) -> dict:
        """GEM: S1F13, S2F33 (delete all, define), S2F35, S2F37 and optionally S6F23."""
        assert self.conn is not None
        out = {}
        r = await self.conn.request(1, 13, secs2.encode(secs2.L()))
        out["commack"] = r.item().items[0].scalar
        self.report_vids = {RPTID: [s.vid for s in self.specs]}
        if define_reports:
            dataid = 1
            r = await self.conn.request(2, 33, secs2.encode(secs2.L(secs2.U4(dataid), secs2.L())))  # delete all
            r = await self.conn.request(2, 33, secs2.encode(secs2.L(
                secs2.U4(dataid + 1),
                secs2.L(secs2.L(secs2.U4(RPTID), secs2.L(*(secs2.U4(s.vid) for s in self.specs)))))))
            out["drack"] = r.item().scalar
            r = await self.conn.request(2, 35, secs2.encode(secs2.L(
                secs2.U4(dataid + 2), secs2.L(secs2.L(secs2.U4(CEID), secs2.L(secs2.U4(RPTID))))))
            )
            out["lrack"] = r.item().scalar
            r = await self.conn.request(2, 37, secs2.encode(secs2.L(secs2.BOOLEAN(True), secs2.L(secs2.U4(CEID)))))
            out["erack"] = r.item().scalar
        if request_spool:
            r = await self.conn.request(6, 23, secs2.encode(secs2.U1(0)))
            out["rsda"] = r.item().scalar
        return out

    def decode_event(self, item: secs2.Item) -> list:
        _dataid, _ceid, reports = item.items
        values: list = [None] * len(self.specs)
        for rpt in reports.items:
            rptid_i, vals_i = rpt.items
            for vid, v in zip(self.report_vids[rptid_i.scalar], vals_i.items):
                values[self.vid_pos[vid]] = v.scalar
        return values

    async def _on_primary(self, conn: hsms.HsmsConnection, msg: hsms.Message) -> None:
        if msg.stream == 6 and msg.function == 11:
            values = self.decode_event(msg.item())
            self.received += 1
            r = self.on_event(values)
            if self._async_cb:
                await r
            if msg.wbit:
                conn.reply(msg, 12, ACK_OK)
        elif msg.wbit:
            conn.reply(msg, 0)

    async def close(self) -> None:
        if self.conn:
            await self.conn.separate()
