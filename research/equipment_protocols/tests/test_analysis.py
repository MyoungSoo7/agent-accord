"""Sequence-gap analysis and percentile helpers."""

import random

import numpy as np

from eqp.analysis import latency_summary, percentile, runs, seq_stats, spread


def test_clean_stream():
    s = seq_stats(range(100), 0, 99)
    assert (s["lost"], s["duplicates"], s["out_of_order"], s["unique"]) == (0, 0, 0, 100)


def test_gap_dup_and_reorder():
    received = [0, 1, 2, 5, 6, 6, 4, 7, 9, 9, 9]  # 3 and 8 lost; 6 dup once, 9 dup twice; 4 late
    s = seq_stats(received, 0, 9)
    assert s["lost"] == 2 and s["lost_ranges"] == [[3, 3], [8, 8]]
    assert s["duplicates"] == 3
    assert s["out_of_order"] == 1
    assert s["receipts"] == len(received) and s["unique"] == 8


def test_late_duplicate_is_not_counted_as_out_of_order():
    s = seq_stats([0, 1, 2, 3, 1, 4], 0, 4)
    assert s["duplicates"] == 1 and s["out_of_order"] == 0


def test_tail_loss_and_unexpected():
    s = seq_stats([0, 1, 2, 50], 0, 9)
    assert s["lost"] == 7 and s["lost_ranges"] == [[3, 9]] and s["unexpected"] == 1


def test_runs():
    assert runs([1, 2, 3, 7, 9, 10]) == [[1, 3], [7, 7], [9, 10]]
    assert runs([]) == []


def test_percentile_matches_numpy_linear():
    rng = random.Random(0)
    vals = sorted(rng.expovariate(1.0) for _ in range(1001))
    for p in (0, 50, 95, 99, 100):
        assert abs(percentile(vals, p) - float(np.percentile(vals, p))) < 1e-12
    summ = latency_summary(vals)
    assert summ["n"] == 1001 and summ["max"] == vals[-1]


def test_spread():
    assert spread([3, 1, 2]) == {"median": 2, "min": 1, "max": 3, "n": 3}
    assert spread([])["n"] == 0
