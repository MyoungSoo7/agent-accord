"""Recording of received events, paho-mqtt helpers, PUBACK -> asyncio bridge."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import struct
import threading
from pathlib import Path

import paho.mqtt.client as mqtt

REC = struct.Struct("<IQQ")  # seq, t_created_ns, t_recv_ns


class Recorder:
    """Event log. durable=True writes every record with os.write (survives SIGKILL of the
    process, because the bytes are already in the kernel); otherwise records are buffered in
    memory and written at close()."""

    def __init__(self, path: str | Path, durable: bool = False) -> None:
        self.path = str(path)
        self.durable = durable
        self.count = 0
        if durable:
            self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        else:
            self.buf = bytearray()
        self._state_fd: int | None = None

    def event(self, seq: int, t_created: int, t_recv: int) -> None:
        self.count += 1
        rec = REC.pack(seq & 0xFFFFFFFF, t_created, t_recv)
        if self.durable:
            os.write(self.fd, rec)
        else:
            self.buf += rec

    def state(self, **fields) -> None:
        """Durable JSON-lines side log (Sparkplug state messages etc.)."""
        if self._state_fd is None:
            self._state_fd = os.open(self.path + ".state", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.write(self._state_fd, (json.dumps(fields) + "\n").encode())

    def close(self) -> None:
        if self.durable:
            os.close(self.fd)
        else:
            with open(self.path, "ab") as f:
                f.write(self.buf)
        if self._state_fd is not None:
            os.close(self._state_fd)


def read_events(path: str | Path) -> list[tuple[int, int, int]]:
    p = Path(path)
    if not p.exists():
        return []
    data = p.read_bytes()
    usable = len(data) - len(data) % REC.size
    return list(REC.iter_unpack(data[:usable]))


def read_states(path: str | Path) -> list[dict]:
    p = Path(str(path) + ".state")
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


# --- paho-mqtt (1.6.x API) -----------------------------------------------------

def mqtt_client(client_id: str, *, clean_session: bool = True, nodelay: bool = True) -> mqtt.Client:
    """paho 1.6 does not set TCP_NODELAY itself; nodelay=True sets it on every new socket."""
    c = mqtt.Client(client_id=client_id, clean_session=clean_session, protocol=mqtt.MQTTv311)
    if nodelay:
        c.on_socket_open = lambda cl, userdata, sock: sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return c


def connect_blocking(client: mqtt.Client, port: int, *, keepalive: int = 30, timeout: float = 5.0,
                     host: str = "127.0.0.1") -> dict:
    """connect + loop_start; returns CONNACK flags once on_connect fired with rc=0."""
    done = threading.Event()
    box: dict = {}
    user_cb = client.on_connect

    def on_connect(c, userdata, flags, rc):
        box["rc"], box["flags"] = rc, dict(flags)
        if user_cb:
            user_cb(c, userdata, flags, rc)
        done.set()

    client.on_connect = on_connect
    client.connect(host, port, keepalive)
    client.loop_start()
    if not done.wait(timeout) or box.get("rc") != 0:
        client.loop_stop()
        raise ConnectionError(f"MQTT connect failed: {box}")
    client.on_connect = user_cb
    return box["flags"]


def subscribe_blocking(client: mqtt.Client, topic: str, qos: int, timeout: float = 5.0) -> tuple:
    """SUBSCRIBE and wait for its SUBACK (callback registered before sending: no race)."""
    cond = threading.Condition()
    acked: dict[int, tuple] = {}

    def on_subscribe(c, userdata, m, granted):
        with cond:
            acked[m] = tuple(granted)
            cond.notify_all()

    client.on_subscribe = on_subscribe
    _rc, mid = client.subscribe(topic, qos)
    with cond:
        if not cond.wait_for(lambda: mid in acked, timeout):
            raise TimeoutError("SUBACK not received")
        return acked[mid]


class AckWaiter:
    """Turns paho's on_publish(mid) (network thread) into awaitable futures."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.lock = threading.Lock()
        self.futs: dict[int, asyncio.Future] = {}
        self.early: set[int] = set()

    def on_publish(self, client, userdata, mid) -> None:
        with self.lock:
            fut = self.futs.pop(mid, None)
            if fut is None:
                self.early.add(mid)
                return
        self.loop.call_soon_threadsafe(_resolve, fut)

    def wait(self, mid: int) -> asyncio.Future:
        fut = self.loop.create_future()
        with self.lock:
            if mid in self.early:
                self.early.discard(mid)
                fut.set_result(None)
            else:
                self.futs[mid] = fut
        return fut


def _resolve(fut: asyncio.Future) -> None:
    if not fut.done():
        fut.set_result(None)

