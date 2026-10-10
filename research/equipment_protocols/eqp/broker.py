"""Mosquitto (reference MQTT broker) on an ephemeral loopback port, one instance per experiment."""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path

MOSQUITTO = shutil.which("mosquitto") or "/usr/sbin/mosquitto"

# Stated explicitly so the report can quote them (believed to equal the Mosquitto 2.0 defaults,
# not checked against its documentation). set_tcp_nodelay is appended: true unless nodelay=False.
DEFAULT_CONF = """\
allow_anonymous true
persistence false
max_inflight_messages 20
max_queued_messages 1000
queue_qos0_messages false
"""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def version() -> str:
    try:
        out = subprocess.run([MOSQUITTO, "-h"], capture_output=True, text=True, timeout=5).stdout
        return out.splitlines()[0].strip()
    except Exception:
        return "unknown"


class Mosquitto:
    def __init__(self, workdir: Path, extra_conf: str = "", nodelay: bool = True) -> None:
        self.workdir = Path(workdir)
        self.extra_conf = extra_conf + f"set_tcp_nodelay {'true' if nodelay else 'false'}\n"
        self.port = 0
        self.proc: subprocess.Popen | None = None
        self.log_path: Path | None = None

    def start(self, attempts: int = 5) -> int:
        self.workdir.mkdir(parents=True, exist_ok=True)
        for _ in range(attempts):
            port = free_port()
            conf = self.workdir / f"mosquitto-{port}.conf"
            # log to stderr captured here: as root, mosquitto drops to user "mosquitto", which may
            # not be able to write a log file into our work directory
            conf.write_text(f"listener {port} 127.0.0.1\n{DEFAULT_CONF}{self.extra_conf}log_dest stderr\n")
            self.log_path = self.workdir / f"mosquitto-{port}.log"
            with open(self.log_path, "wb") as log:
                self.proc = subprocess.Popen([MOSQUITTO, "-c", str(conf)], stdout=subprocess.DEVNULL, stderr=log)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and self.proc.poll() is None:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        self.port = port
                        return port
                except OSError:
                    time.sleep(0.02)
            self.stop()
        raise RuntimeError("could not start mosquitto")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(3)
        self.proc = None

    def __enter__(self) -> "Mosquitto":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def dropped_warnings(log_path: Path | None) -> int:
    """Number of 'Outgoing messages are being dropped' notices in a mosquitto log."""
    if not log_path or not Path(log_path).exists():
        return 0
    return sum("being dropped" in line for line in Path(log_path).read_text(errors="replace").splitlines())
