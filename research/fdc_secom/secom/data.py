"""Load the verified SECOM files into numpy arrays (time-sorted)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .integrity import (
    LABEL_COUNTS,
    N_FEATURES,
    N_ROWS,
    IntegrityError,
    check_hashes,
    parse_label_line,
)

DEFAULT_RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


@dataclass(frozen=True)
class SecomData:
    X: np.ndarray  # (n, 590) float64, NaN = missing
    y: np.ndarray  # (n,) int8, 1 = fail, 0 = pass
    ts: np.ndarray  # (n,) datetime64[s], time of the in-house line test
    row_id: np.ndarray  # (n,) original 0-based line number in the files
    feature_names: tuple[str, ...]  # "f000".."f589" = 0-based column in secom.data

    @property
    def n(self) -> int:
        return self.X.shape[0]


def feature_names(p: int = N_FEATURES) -> tuple[str, ...]:
    return tuple(f"f{j:03d}" for j in range(p))


def load_secom(raw_dir: Path | str = DEFAULT_RAW_DIR, verify_hashes: bool = True) -> SecomData:
    """Verify sha256 (by default), parse, check shape/labels and sort by timestamp."""
    raw_dir = Path(raw_dir)
    if verify_hashes:
        check_hashes(raw_dir)
    X = pd.read_csv(
        raw_dir / "secom.data",
        sep=r"\s+",
        header=None,
        na_values=["NaN"],
        keep_default_na=False,
        dtype=np.float64,
        engine="c",
    ).to_numpy(dtype=np.float64, copy=True)
    if X.shape != (N_ROWS, N_FEATURES):
        raise IntegrityError(f"secom.data parsed to shape {X.shape}")
    if not np.all(np.isfinite(X) | np.isnan(X)):
        raise IntegrityError("secom.data contains infinite values")

    labels, stamps = [], []
    with open(raw_dir / "secom_labels.data", "r", encoding="ascii") as fh:
        for line in fh:
            lab, ts = parse_label_line(line)
            labels.append(lab)
            stamps.append(ts)
    labels = np.asarray(labels, dtype=np.int64)
    if labels.shape[0] != N_ROWS:
        raise IntegrityError(f"secom_labels.data has {labels.shape[0]} rows")
    counts = {int(v): int((labels == v).sum()) for v in np.unique(labels)}
    if counts != LABEL_COUNTS:
        raise IntegrityError(f"label counts {counts}, expected {LABEL_COUNTS}")

    ts = np.asarray(stamps, dtype="datetime64[s]")
    order = np.argsort(ts, kind="stable")
    y = (labels == 1).astype(np.int8)
    return SecomData(
        X=X[order],
        y=y[order],
        ts=ts[order],
        row_id=order.astype(np.int64),
        feature_names=feature_names(X.shape[1]),
    )
