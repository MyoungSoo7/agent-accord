#!/usr/bin/env python3
"""Fetch and verify the UCI SECOM files into data/raw/.

    python -I fetch_data.py                 # verify files already in data/raw/, else download
    python -I fetch_data.py --raw-dir DIR   # use another directory

Order: (1) files already present -> verify sha256 + shape + label counts;
(2) official UCI zip; (3) two public GitHub mirrors (raw.githubusercontent.com).
Every candidate byte string is checked against the pinned sha256 before it is
written, and the final directory is checked again (hash + 1567x590 shape +
1463/104 label counts). Any mismatch exits non-zero. Downloaded bytes are only
hashed and parsed as text; nothing from the sources is executed, and zip
members are read by name (never extracted by path).

Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# `python -I` does not put the script directory on sys.path. Add back only this
# study's own code directory (never the data directory).
sys.path.insert(0, str(HERE))

from secom.integrity import (  # noqa: E402
    EXPECTED_SHA256,
    FILES,
    IntegrityError,
    sha256_file,
    verify,
)

UCI_ZIP_URL = "https://archive.ics.uci.edu/static/public/179/secom.zip"
# `HEAD` resolves to each repository's default branch.
MIRRORS = (
    (
        "github.com/MarwanAhmedHelmy/secom-defect-prediction",
        "https://raw.githubusercontent.com/MarwanAhmedHelmy/secom-defect-prediction/HEAD/{name}",
    ),
    (
        "github.com/LakshmiNakshatra/Predictive-Models-for-Equipment-Fault-Detection---Secom-Dataset",
        "https://raw.githubusercontent.com/LakshmiNakshatra/"
        "Predictive-Models-for-Equipment-Fault-Detection---Secom-Dataset/HEAD/data/{name}",
    ),
)
MAX_BYTES = 64 * 1024 * 1024
TIMEOUT_S = 30


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def download(url: str, max_bytes: int = MAX_BYTES) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "fdc-secom-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise IOError(f"response from {url} exceeds {max_bytes} bytes")
    return data


def _accept(name: str, data: bytes, source: str) -> bool:
    digest = hashlib.sha256(data).hexdigest()
    if digest != EXPECTED_SHA256[name]:
        _log(
            f"  REJECTED {name} from {source}: sha256 {digest} != expected "
            f"{EXPECTED_SHA256[name]}"
        )
        return False
    return True


def from_uci_zip(needed: list[str]) -> dict[str, tuple[bytes, str]]:
    got: dict[str, tuple[bytes, str]] = {}
    try:
        blob = download(UCI_ZIP_URL)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _log(f"  UCI zip unavailable: {exc}")
        return got
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        _log(f"  UCI zip unreadable: {exc}")
        return got
    with zf:
        for info in zf.infolist():
            base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
            if base not in needed or info.is_dir() or base in got:
                continue
            if info.file_size > MAX_BYTES:
                _log(f"  REJECTED {base} in UCI zip: declared size {info.file_size}")
                continue
            data = zf.read(info)
            if _accept(base, data, "UCI zip"):
                got[base] = (data, UCI_ZIP_URL)
    return got


def from_mirrors(needed: list[str]) -> dict[str, tuple[bytes, str]]:
    got: dict[str, tuple[bytes, str]] = {}
    for name in needed:
        for label, pattern in MIRRORS:
            url = pattern.format(name=name)
            try:
                data = download(url)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                _log(f"  {name} not available from {label}: {exc}")
                continue
            if _accept(name, data, label):
                got[name] = (data, url)
                break
    return got


def fetch(raw_dir: Path) -> int:
    raw_dir.mkdir(parents=True, exist_ok=True)
    present = [n for n in FILES if (raw_dir / n).is_file()]
    for name in present:
        digest = sha256_file(raw_dir / name)
        if digest != EXPECTED_SHA256[name]:
            _log(
                f"FAIL: existing {raw_dir / name} has sha256 {digest}, expected "
                f"{EXPECTED_SHA256[name]}. Not overwriting; inspect or delete it."
            )
            return 1
    needed = [n for n in FILES if n not in present]
    if needed:
        _log(f"missing {needed}; trying the official UCI zip")
        got = from_uci_zip(needed)
        rest = [n for n in needed if n not in got]
        if rest:
            _log(f"still missing {rest}; trying GitHub mirrors")
            got.update(from_mirrors(rest))
        absent = [n for n in needed if n not in got]
        if absent:
            _log(f"FAIL: no source produced verified bytes for {absent}")
            return 1
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=raw_dir.parent))
        try:
            for name, (data, url) in got.items():
                (staging / name).write_bytes(data)
                os.replace(staging / name, raw_dir / name)
                _log(f"  wrote {raw_dir / name}  <- {url}")
        finally:
            for leftover in staging.iterdir():
                leftover.unlink()
            staging.rmdir()
    try:
        summary = verify(raw_dir)
    except IntegrityError as exc:
        _log(f"FAIL: {exc}")
        return 1
    print(f"OK {raw_dir}")
    for name, digest in summary["sha256"].items():
        print(f"  sha256 {digest}  {name}")
    print(
        f"  shape {summary['n_rows']}x{summary['n_features']}, "
        f"labels {summary['label_counts']}, missing cells {summary['n_missing_cells']}, "
        f"{summary['first_timestamp']} .. {summary['last_timestamp']}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw-dir", type=Path, default=HERE / "data" / "raw")
    args = ap.parse_args(argv)
    return fetch(args.raw_dir.resolve())


if __name__ == "__main__":
    sys.exit(main())
