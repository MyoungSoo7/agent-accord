"""Minimal HSMS (SEMI E37, single-session style) on stdlib asyncio.

Frame on the wire::

    4-byte message length (big-endian, = 10 + len(data))
    10-byte header: SessionID(2) | byte2 | byte3 | PType(1) | SType(1) | SystemBytes(4)
    data (SECS-II encoded body, data messages only)

For data messages byte2 = W-bit (0x80) | stream and byte3 = function.
For control messages byte2/byte3 carry control information
(Select.rsp: byte3 = SelectStatus; Reject.req: byte2 = rejected SType/PType, byte3 = reason).
Control messages use SessionID 0xFFFF.

Timers implemented: T3 (reply), T6 (control transaction), T7 (not-selected), T8 (inter-character,
applied to the read of one message body).  Not implemented: T5 connect-separation pacing is
left to the caller, multi-block/interleave rules of SECS-I, HSMS-GS multi-session.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import secs2

log = logging.getLogger("hsms")

# SType values
DATA = 0
SELECT_REQ = 1
SELECT_RSP = 2
DESELECT_REQ = 3
DESELECT_RSP = 4
LINKTEST_REQ = 5
LINKTEST_RSP = 6
REJECT_REQ = 7
SEPARATE_REQ = 9

STYPE_NAMES = {
    DATA: "data",
    SELECT_REQ: "select.req",
    SELECT_RSP: "select.rsp",
    DESELECT_REQ: "deselect.req",
    DESELECT_RSP: "deselect.rsp",
    LINKTEST_REQ: "linktest.req",
    LINKTEST_RSP: "linktest.rsp",
    REJECT_REQ: "reject.req",
    SEPARATE_REQ: "separate.req",
}

SELECT_OK = 0
SELECT_ALREADY_ACTIVE = 1

REJECT_STYPE_NOT_SUPPORTED = 1
REJECT_PTYPE_NOT_SUPPORTED = 2
REJECT_TRANSACTION_NOT_OPEN = 3
REJECT_NOT_SELECTED = 4

CONTROL_SESSION = 0xFFFF
HEADER_LEN = 10
MAX_MESSAGE = 16 * 1024 * 1024

_HDR = struct.Struct(">HBBBBI")
_LEN = struct.Struct(">I")


@dataclass(frozen=True, slots=True)
class Header:
    session_id: int
    byte2: int
    byte3: int
    ptype: int
    stype: int
    system: int

    def encode(self) -> bytes:
        return _HDR.pack(self.session_id, self.byte2, self.byte3, self.ptype, self.stype, self.system)

    @classmethod
    def decode(cls, data: bytes) -> "Header":
        if len(data) != HEADER_LEN:
            raise ValueError("HSMS header must be 10 bytes")
        return cls(*_HDR.unpack(data))

    @property
    def stream(self) -> int:
        return self.byte2 & 0x7F

    @property
    def function(self) -> int:
        return self.byte3

    @property
    def wbit(self) -> bool:
        return bool(self.byte2 & 0x80)


def data_header(stream: int, function: int, system: int, *, wbit: bool, session_id: int = 0) -> Header:
    if not 0 <= stream <= 127 or not 0 <= function <= 255:
        raise ValueError("stream 0..127, function 0..255")
    return Header(session_id, (0x80 if wbit else 0) | stream, function, 0, DATA, system)


def control_header(stype: int, system: int, *, byte2: int = 0, byte3: int = 0,
                   session_id: int = CONTROL_SESSION) -> Header:
    return Header(session_id, byte2, byte3, 0, stype, system)


def encode_frame(header: Header, body: bytes = b"") -> bytes:
    return _LEN.pack(HEADER_LEN + len(body)) + header.encode() + body


@dataclass(slots=True)
class Message:
    header: Header
    body: bytes

    @property
    def stream(self) -> int:
        return self.header.stream

    @property
    def function(self) -> int:
        return self.header.function

    @property
    def wbit(self) -> bool:
        return self.header.wbit

    @property
    def system(self) -> int:
        return self.header.system

    def item(self) -> secs2.Item | None:
        return secs2.decode(self.body) if self.body else None

    def __repr__(self) -> str:
        return f"S{self.stream}F{self.function}{' W' if self.wbit else ''} sys={self.system} len={len(self.body)}"


class HsmsError(Exception):
    pass


class T3Timeout(HsmsError):
    pass


class ConnectionClosed(HsmsError):
    pass


class Rejected(HsmsError):
    pass


PrimaryHandler = Callable[["HsmsConnection", Message], Awaitable[None]]


class HsmsConnection:
    """One HSMS TCP connection (one session).

    The reader task resolves replies by system bytes and answers control messages.
    Primary data messages are processed one at a time, in arrival order, by a worker task
    that calls ``on_primary``; the handler replies with :meth:`reply`.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        active: bool,
        session_id: int = 0,
        t3: float = 45.0,
        t6: float = 5.0,
        t7: float = 10.0,
        t8: float = 5.0,
        linktest_interval: float | None = None,
        on_primary: PrimaryHandler | None = None,
        name: str = "hsms",
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.active = active
        self.session_id = session_id
        self.t3, self.t6, self.t7, self.t8 = t3, t6, t7, t8
        self.linktest_interval = linktest_interval
        self.on_primary = on_primary
        self.name = name
        self.selected = asyncio.Event()
        self.closed = asyncio.Event()
        self._pending: dict[int, asyncio.Future] = {}
        self._system = 0
        self._primary_q: asyncio.Queue[Message | None] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self.unexpected_replies = 0
        self.rejects_sent = 0

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._reader_loop(), name=f"{self.name}-reader"))
        self._tasks.append(asyncio.create_task(self._worker(), name=f"{self.name}-worker"))
        if not self.active:
            self._tasks.append(asyncio.create_task(self._t7_guard(), name=f"{self.name}-t7"))
        if self.linktest_interval:
            self._tasks.append(asyncio.create_task(self._linktest_loop(), name=f"{self.name}-linktest"))

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        self.selected.clear()
        try:
            self.writer.close()
        except Exception:  # pragma: no cover - transport already gone
            pass
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionClosed(self.name))
        self._pending.clear()
        self._primary_q.put_nowait(None)
        current = asyncio.current_task()
        for t in self._tasks:
            if t is not current and t.get_name() != f"{self.name}-worker":
                t.cancel()

    async def wait_closed(self) -> None:
        await self.closed.wait()

    # -- sending -------------------------------------------------------------
    def next_system(self) -> int:
        self._system = self._system % 0xFFFFFFFF + 1
        return self._system

    def _write(self, header: Header, body: bytes = b"") -> None:
        if self.closed.is_set():
            raise ConnectionClosed(self.name)
        self.writer.write(encode_frame(header, body))

    async def select(self) -> None:
        """Active side: Select.req / Select.rsp under T6."""
        system = self.next_system()
        fut = asyncio.get_running_loop().create_future()
        self._pending[system] = fut
        self._write(control_header(SELECT_REQ, system))
        try:
            status = await asyncio.wait_for(fut, self.t6)
        except asyncio.TimeoutError:
            self.close()
            raise HsmsError("T6 timeout waiting for select.rsp")
        finally:
            self._pending.pop(system, None)
        if status != SELECT_OK:
            raise HsmsError(f"select rejected, status {status}")
        self.selected.set()

    async def linktest(self) -> None:
        system = self.next_system()
        fut = asyncio.get_running_loop().create_future()
        self._pending[system] = fut
        self._write(control_header(LINKTEST_REQ, system))
        try:
            await asyncio.wait_for(fut, self.t6)
        except asyncio.TimeoutError:
            self.close()
            raise HsmsError("T6 timeout waiting for linktest.rsp")
        finally:
            self._pending.pop(system, None)

    async def separate(self) -> None:
        if not self.closed.is_set():
            self._write(control_header(SEPARATE_REQ, self.next_system()))
            try:
                await self.writer.drain()
            except Exception:
                pass
        self.close()

    async def request(self, stream: int, function: int, body: bytes = b"", *,
                      timeout: float | None = None) -> Message:
        """Send a primary with W-bit and wait for the reply (T3)."""
        if not self.selected.is_set():
            raise HsmsError("not selected")
        system = self.next_system()
        fut = asyncio.get_running_loop().create_future()
        self._pending[system] = fut
        self._write(data_header(stream, function, system, wbit=True, session_id=self.session_id), body)
        try:
            return await asyncio.wait_for(fut, self.t3 if timeout is None else timeout)
        except asyncio.TimeoutError:
            raise T3Timeout(f"T3 expired for S{stream}F{function} sys={system}") from None
        finally:
            self._pending.pop(system, None)

    def send(self, stream: int, function: int, body: bytes = b"") -> None:
        """Primary without W-bit (no reply expected)."""
        self._write(data_header(stream, function, self.next_system(), wbit=False, session_id=self.session_id), body)

    def reply(self, primary: Message, function: int, body: bytes = b"") -> None:
        self._write(data_header(primary.stream, function, primary.system, wbit=False,
                                session_id=self.session_id), body)

    def _reject(self, hdr: Header, reason: int) -> None:
        self.rejects_sent += 1
        byte2 = hdr.ptype if reason == REJECT_PTYPE_NOT_SUPPORTED else hdr.stype
        self._write(control_header(REJECT_REQ, hdr.system, byte2=byte2, byte3=reason))

    # -- receiving -----------------------------------------------------------
    async def _reader_loop(self) -> None:
        try:
            while True:
                lb = await self.reader.readexactly(4)
                n = _LEN.unpack(lb)[0]
                if n < HEADER_LEN or n > MAX_MESSAGE:
                    log.warning("%s: bad message length %d", self.name, n)
                    break
                data = await asyncio.wait_for(self.reader.readexactly(n), self.t8)
                self._dispatch(Header.decode(data[:HEADER_LEN]), data[HEADER_LEN:])
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError, OSError):
            pass
        except asyncio.CancelledError:
            pass
        finally:
            self.close()

    def _resolve(self, system: int, value=None, exc: Exception | None = None) -> bool:
        fut = self._pending.pop(system, None)
        if fut is None or fut.done():
            return False
        if exc is not None:
            fut.set_exception(exc)
        else:
            fut.set_result(value)
        return True

    def _dispatch(self, hdr: Header, body: bytes) -> None:
        if hdr.ptype != 0:
            self._reject(hdr, REJECT_PTYPE_NOT_SUPPORTED)
            return
        st = hdr.stype
        if st == DATA:
            if not self.selected.is_set():
                self._reject(hdr, REJECT_NOT_SELECTED)
                return
            msg = Message(hdr, body)
            if hdr.function % 2 == 0:  # reply (or SxF0 abort)
                if not self._resolve(hdr.system, msg):
                    self.unexpected_replies += 1
            else:
                self._primary_q.put_nowait(msg)
        elif st == SELECT_REQ:
            if self.active:
                self._reject(hdr, REJECT_STYPE_NOT_SUPPORTED)
            elif self.selected.is_set():
                self._write(control_header(SELECT_RSP, hdr.system, byte3=SELECT_ALREADY_ACTIVE))
            else:
                self._write(control_header(SELECT_RSP, hdr.system, byte3=SELECT_OK))
                self.selected.set()
        elif st == SELECT_RSP:
            if not self._resolve(hdr.system, hdr.byte3):
                self._reject(hdr, REJECT_TRANSACTION_NOT_OPEN)
        elif st == LINKTEST_REQ:
            self._write(control_header(LINKTEST_RSP, hdr.system))
        elif st in (LINKTEST_RSP, DESELECT_RSP):
            if not self._resolve(hdr.system, hdr.byte3):
                self._reject(hdr, REJECT_TRANSACTION_NOT_OPEN)
        elif st == DESELECT_REQ:
            self._write(control_header(DESELECT_RSP, hdr.system, byte3=0))
            self.selected.clear()
        elif st == SEPARATE_REQ:
            self.close()
        elif st == REJECT_REQ:
            self._resolve(hdr.system, exc=Rejected(f"reject reason {hdr.byte3}"))
        else:
            self._reject(hdr, REJECT_STYPE_NOT_SUPPORTED)

    async def _worker(self) -> None:
        while True:
            msg = await self._primary_q.get()
            if msg is None:
                return
            if self.on_primary is None:
                if msg.wbit and not self.closed.is_set():
                    self.reply(msg, 0)  # SxF0: abort transaction
                continue
            try:
                await self.on_primary(self, msg)
            except ConnectionClosed:
                return
            except Exception:  # keep serving; log for diagnosis
                log.exception("%s: primary handler failed for %r", self.name, msg)

    async def _t7_guard(self) -> None:
        try:
            await asyncio.wait_for(self.selected.wait(), self.t7)
        except asyncio.TimeoutError:
            log.warning("%s: T7 expired (not selected)", self.name)
            self.close()

    async def _linktest_loop(self) -> None:
        await self.selected.wait()
        while not self.closed.is_set():
            await asyncio.sleep(self.linktest_interval)  # type: ignore[arg-type]
            try:
                await self.linktest()
            except HsmsError:
                return


async def connect(host: str, port: int, **kw) -> HsmsConnection:
    """Active entity: TCP connect + Select."""
    reader, writer = await asyncio.open_connection(host, port)
    conn = HsmsConnection(reader, writer, active=True, **kw)
    conn.start()
    await conn.select()
    return conn
