"""Integrity checks for the UCI SECOM files.

Standard library only, so ``fetch_data.py`` can run before the venv exists.
The files are treated as untrusted data: they are hashed and parsed as text,
never executed or unpickled.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

FILES = ("secom.data", "secom_labels.data", "secom.names")

EXPECTED_SHA256 = {
    "secom.data": "20f0e7ee434f7dcbae0eea9ffff009a2b57f42d6b0dc9a5bd4f00782c0a3374c",
    "secom_labels.data": "126884cf453705c9e61a903fe906f0665a3b45ce3639e621edc5c93c89627e03",
    "secom.names": "6d91b0b46cdee03064ee3e3112f937c1b3f7fcd9933575794ec07974e6f1ea59",
}

N_ROWS = 1567
N_FEATURES = 590
# secom_labels.data: -1 = pass, 1 = fail (secom.names prints "-1" as ".1").
LABEL_COUNTS = {-1: 1463, 1: 104}
TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M:%S"

_LABEL_RE = re.compile(r'^(-1|1) "(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})"$')


class IntegrityError(RuntimeError):
    """Raised when a SECOM file does not match the expected hash or structure."""


def sha256_file(path: Path | str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def check_hashes(raw_dir: Path | str, expected: dict | None = None) -> dict:
    """Return {file: sha256}; raise IntegrityError on a missing file or mismatch."""
    expected = EXPECTED_SHA256 if expected is None else expected
    raw_dir = Path(raw_dir)
    got = {}
    for name, want in expected.items():
        path = raw_dir / name
        if not path.is_file():
            raise IntegrityError(f"missing file: {path}")
        digest = sha256_file(path)
        if digest != want:
            raise IntegrityError(
                f"sha256 mismatch for {name}: got {digest}, expected {want}. "
                "Refusing to use it; inspect the file before changing EXPECTED_SHA256."
            )
        got[name] = digest
    return got


def parse_label_line(line: str) -> tuple[int, datetime]:
    """Parse one secom_labels.data line: ``-1 "19/07/2008 11:55:00"``."""
    m = _LABEL_RE.match(line.strip())
    if not m:
        raise IntegrityError(f"unparseable label line: {line[:80]!r}")
    return int(m.group(1)), datetime.strptime(m.group(2), TIMESTAMP_FORMAT)


def _parse_feature_token(tok: str) -> float:
    if tok == "NaN":
        return math.nan
    try:
        value = float(tok)
    except ValueError as exc:
        raise IntegrityError(f"non-numeric token in secom.data: {tok[:40]!r}") from exc
    if not math.isfinite(value):
        raise IntegrityError(f"non-finite token in secom.data: {tok[:40]!r}")
    return value


def check_structure(
    raw_dir: Path | str,
    n_rows: int = N_ROWS,
    n_features: int = N_FEATURES,
    label_counts: dict | None = None,
) -> dict:
    """Parse both data files as text and check shape, tokens and label counts."""
    label_counts = LABEL_COUNTS if label_counts is None else label_counts
    raw_dir = Path(raw_dir)
    rows = 0
    n_nan = 0
    with open(raw_dir / "secom.data", "r", encoding="ascii") as fh:
        for i, line in enumerate(fh):
            toks = line.split()
            if len(toks) != n_features:
                raise IntegrityError(
                    f"secom.data line {i + 1}: {len(toks)} fields, expected {n_features}"
                )
            for tok in toks:
                if math.isnan(_parse_feature_token(tok)):
                    n_nan += 1
            rows += 1
    if rows != n_rows:
        raise IntegrityError(f"secom.data has {rows} rows, expected {n_rows}")

    labels = []
    stamps = []
    with open(raw_dir / "secom_labels.data", "r", encoding="ascii") as fh:
        for line in fh:
            lab, ts = parse_label_line(line)
            labels.append(lab)
            stamps.append(ts)
    if len(labels) != n_rows:
        raise IntegrityError(f"secom_labels.data has {len(labels)} rows, expected {n_rows}")
    counts = dict(Counter(labels))
    if counts != dict(label_counts):
        raise IntegrityError(f"label counts {counts}, expected {dict(label_counts)}")
    return {
        "n_rows": rows,
        "n_features": n_features,
        "n_missing_cells": n_nan,
        "label_counts": {str(k): v for k, v in sorted(counts.items())},
        "first_timestamp": min(stamps).isoformat(sep=" "),
        "last_timestamp": max(stamps).isoformat(sep=" "),
    }


def verify(raw_dir: Path | str) -> dict:
    """Hash check, then structural check. Raises IntegrityError on any mismatch."""
    hashes = check_hashes(raw_dir)
    summary = check_structure(raw_dir)
    summary["sha256"] = hashes
    return summary
