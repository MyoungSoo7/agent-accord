"""Sequence-gap and latency statistics."""

from __future__ import annotations

import math
import statistics
from typing import Iterable, Sequence


def seq_stats(received: Iterable[int], first: int, last: int) -> dict:
    """Classify receipts of sequence numbers expected in [first, last].

    received: sequence numbers in arrival order (may repeat, may be out of order).
    lost:          expected numbers never received
    duplicates:    receipts of a number already received before
    out_of_order:  first-time receipts of a number lower than the highest number received earlier
    unexpected:    distinct received numbers outside [first, last]
    """
    seen: set[int] = set()
    dup = ooo = total = 0
    highest = None
    for s in received:
        total += 1
        if s in seen:
            dup += 1
            continue
        seen.add(s)
        if highest is not None and s < highest:
            ooo += 1
        else:
            highest = s
    expected = last - first + 1 if last >= first else 0
    in_range = {s for s in seen if first <= s <= last}
    missing = sorted(set(range(first, last + 1)) - in_range) if expected else []
    return {
        "expected": expected,
        "receipts": total,
        "unique": len(in_range),
        "lost": len(missing),
        "duplicates": dup,
        "out_of_order": ooo,
        "unexpected": len(seen) - len(in_range),
        "lost_ranges": runs(missing),
    }


def runs(sorted_values: Sequence[int]) -> list[list[int]]:
    """[1,2,3,7,9,10] -> [[1,3],[7,7],[9,10]]"""
    out: list[list[int]] = []
    for v in sorted_values:
        if out and v == out[-1][1] + 1:
            out[-1][1] = v
        else:
            out.append([v, v])
    return out


def percentile(sorted_vals: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile of pre-sorted values (numpy's default method)."""
    if not sorted_vals:
        return math.nan
    k = (len(sorted_vals) - 1) * p / 100.0
    lo = math.floor(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def latency_summary(lat_ms: Iterable[float]) -> dict:
    vals = sorted(lat_ms)
    return {
        "n": len(vals),
        "p50": percentile(vals, 50),
        "p95": percentile(vals, 95),
        "p99": percentile(vals, 99),
        "max": vals[-1] if vals else math.nan,
    }


def spread(values: Iterable[float]) -> dict:
    """Median and min/max across repetitions."""
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return {"median": math.nan, "min": math.nan, "max": math.nan, "n": 0}
    return {"median": statistics.median(vals), "min": min(vals), "max": max(vals), "n": len(vals)}
