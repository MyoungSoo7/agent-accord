"""Train/test splits: chronological (holdout, expanding window) and random baselines.

All functions return lists of (train_idx, test_idx) integer arrays that index
the arrays as given. Chronological splits never put two entities with the same
timestamp on different sides, so every training timestamp is strictly earlier
than every test timestamp.
"""

from __future__ import annotations

import numpy as np
from sklearn.model_selection import StratifiedKFold, StratifiedShuffleSplit


def _order(ts: np.ndarray) -> np.ndarray:
    return np.argsort(np.asarray(ts), kind="stable")


def _tie_safe_cut(ts_sorted: np.ndarray, cut: int) -> int:
    """Move `cut` forward until it does not split a run of equal timestamps."""
    n = len(ts_sorted)
    cut = int(min(max(cut, 1), n - 1))
    while cut < n and ts_sorted[cut] == ts_sorted[cut - 1]:
        cut += 1
    if cut >= n:
        raise ValueError("cannot place a cut: all remaining timestamps are tied")
    return cut


def chronological_holdout(ts: np.ndarray, train_frac: float = 0.7):
    """Earliest `train_frac` of entities -> train, the rest -> test."""
    if not 0.0 < train_frac < 1.0:
        raise ValueError("train_frac must be in (0, 1)")
    order = _order(ts)
    ts_sorted = np.asarray(ts)[order]
    cut = _tie_safe_cut(ts_sorted, int(round(train_frac * len(order))))
    return [(order[:cut], order[cut:])]


def expanding_window_folds(ts: np.ndarray, n_blocks: int = 6):
    """Cut time into `n_blocks` contiguous blocks of (almost) equal size.

    Fold k (k = 1..n_blocks-1) trains on blocks 0..k-1 and tests on block k
    (rolling origin, expanding window).
    """
    if n_blocks < 2:
        raise ValueError("need at least two blocks")
    order = _order(ts)
    ts_sorted = np.asarray(ts)[order]
    n = len(order)
    cuts = []
    prev = 0
    for k in range(1, n_blocks):
        c = _tie_safe_cut(ts_sorted, int(round(k * n / n_blocks)))
        if c <= prev:
            raise ValueError("blocks collapsed; too many ties or blocks")
        cuts.append(c)
        prev = c
    bounds = [0, *cuts, n]
    folds = []
    for k in range(1, n_blocks):
        folds.append((order[: bounds[k]], order[bounds[k] : bounds[k + 1]]))
    return folds


def block_bounds(ts: np.ndarray, n_blocks: int = 6) -> list[int]:
    """Positions (in time order) where the expanding-window blocks start/end."""
    folds = expanding_window_folds(ts, n_blocks)
    return [0] + [len(tr) for tr, _ in folds] + [len(ts)]


def random_stratified_kfold(y: np.ndarray, n_splits: int = 5, n_repeats: int = 1, seed: int = 0):
    """Shuffled stratified k-fold (ignores time), repeated with seeds seed..seed+R-1."""
    y = np.asarray(y)
    folds = []
    for r in range(n_repeats):
        skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed + r)
        folds.extend((tr, te) for tr, te in skf.split(np.zeros(len(y)), y))
    return folds


def random_stratified_holdouts(y: np.ndarray, test_size: int, n_repeats: int = 10, seed: int = 0):
    """Random stratified train/test splits with a fixed test size (ignores time)."""
    y = np.asarray(y)
    sss = StratifiedShuffleSplit(n_splits=n_repeats, test_size=test_size, random_state=seed)
    return [(tr, te) for tr, te in sss.split(np.zeros(len(y)), y)]
