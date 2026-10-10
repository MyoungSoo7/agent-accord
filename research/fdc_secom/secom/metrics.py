"""Ranking metrics with bootstrap CIs, written for speed (vectorised over replicates).

All metrics are computed from one ROC sweep over the distinct score values
(ties grouped), with optional per-row multiplicity weights so that a stratified
bootstrap is just a weight matrix. Matches sklearn's roc_auc_score and
average_precision_score (tested).
"""

from __future__ import annotations

import numpy as np

FPR_TARGETS = (0.05, 0.10)
METRIC_KEYS = ("roc_auc", "pr_auc", "recall_at_fpr05", "recall_at_fpr10")


def _groups(s: np.ndarray):
    order = np.argsort(-s, kind="mergesort")
    s_sorted = s[order]
    ends = np.r_[np.flatnonzero(np.diff(s_sorted) != 0), len(s) - 1]
    return order, ends


def _sweep(y: np.ndarray, s: np.ndarray, W: np.ndarray):
    """Cumulative weighted TP/FP at each distinct threshold (scores descending)."""
    order, ends = _groups(s)
    ys = y[order].astype(bool)
    Ws = W[:, order]
    tp = np.cumsum(np.where(ys, Ws, 0.0), axis=1)[:, ends]
    fp = np.cumsum(np.where(ys, 0.0, Ws), axis=1)[:, ends]
    return tp, fp


def _from_counts(tp: np.ndarray, fp: np.ndarray, fpr_targets=FPR_TARGETS) -> dict[str, np.ndarray]:
    P = tp[:, -1:]
    N = fp[:, -1:]
    tpr = np.hstack([np.zeros_like(P), tp / P])
    fpr = np.hstack([np.zeros_like(N), fp / N])
    auc = np.sum(np.diff(fpr, axis=1) * (tpr[:, 1:] + tpr[:, :-1]) / 2.0, axis=1)
    denom = tp + fp
    prec = np.divide(tp, denom, out=np.ones_like(tp), where=denom > 0)
    ap = np.sum(np.diff(tpr, axis=1) * prec, axis=1)
    out = {"roc_auc": auc, "pr_auc": ap}
    for t in fpr_targets:
        out[f"recall_at_fpr{int(round(t * 100)):02d}"] = np.max(
            np.where(fpr <= t + 1e-12, tpr, 0.0), axis=1
        )
    return out


def _check(y, s):
    y = np.asarray(y).astype(bool)
    s = np.asarray(s, dtype=np.float64)
    if y.ndim != 1 or s.shape != y.shape:
        raise ValueError("y and s must be 1-d and equal length")
    if not np.all(np.isfinite(s)):
        raise ValueError("scores must be finite")
    if y.all() or (~y).all():
        raise ValueError("need both classes")
    return y, s


def curve_metrics(y, s, W=None, fpr_targets=FPR_TARGETS) -> dict[str, np.ndarray]:
    """ROC-AUC, average precision and recall@FPR for each weight row of W.

    recall@FPR(t) = the largest TPR over thresholds whose FPR <= t (no
    interpolation between ROC points, i.e. a threshold one could actually set).
    """
    y, s = _check(y, s)
    W = np.ones((1, len(y))) if W is None else np.atleast_2d(np.asarray(W, dtype=np.float64))
    tp, fp = _sweep(y, s, W)
    return _from_counts(tp, fp, fpr_targets)


def permutation_null(y, s, n_perm: int, seed: int) -> dict[str, np.ndarray]:
    """Metrics after shuffling the labels against the fixed scores (keeps score ties).
    The mean of the AP null is the expected AP of a random ranking; with few
    fails it is clearly above the prevalence."""
    y, s = _check(y, s)
    rng = np.random.default_rng(seed)
    order, ends = _groups(s)
    Y = rng.permuted(np.tile(y[order], (n_perm, 1)), axis=1)
    tp = np.cumsum(Y, axis=1, dtype=np.float64)[:, ends]
    fp = (np.arange(1, len(y) + 1, dtype=np.float64)[ends])[None, :] - tp
    return _from_counts(tp, fp)


def point_metrics(y, s) -> dict[str, float]:
    m = {k: float(v[0]) for k, v in curve_metrics(y, s).items()}
    m["prevalence"] = float(np.mean(np.asarray(y).astype(bool)))
    m["n"] = int(len(y))
    m["n_fail"] = int(np.sum(np.asarray(y).astype(bool)))
    return m


def recall_at_fpr(y, s, max_fpr: float) -> float:
    key = f"recall_at_fpr{int(round(max_fpr * 100)):02d}"
    return float(curve_metrics(y, s, fpr_targets=(max_fpr,))[key][0])


def stratified_bootstrap_weights(y, n_boot: int, rng: np.random.Generator) -> np.ndarray:
    """Multiplicity weights for a bootstrap that resamples fails and passes separately."""
    y = np.asarray(y).astype(bool)
    W = np.zeros((n_boot, len(y)))
    offsets = np.arange(n_boot)[:, None]
    for mask in (y, ~y):
        idx = np.flatnonzero(mask)
        m = len(idx)
        draws = rng.integers(0, m, size=(n_boot, m))
        counts = np.bincount((draws + offsets * m).ravel(), minlength=n_boot * m)
        W[:, idx] = counts.reshape(n_boot, m)
    return W


def bootstrap_replicates(y, s, n_boot: int, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return curve_metrics(y, s, stratified_bootstrap_weights(y, n_boot, rng))


def percentile_ci(replicates: np.ndarray, level: float = 0.95) -> tuple[float, float]:
    lo, hi = np.percentile(replicates, [(1 - level) / 2 * 100, (1 + level) / 2 * 100])
    return float(lo), float(hi)


def perm_p_greater(null: np.ndarray, observed: float) -> float:
    return float((1 + np.sum(null >= observed - 1e-12)) / (1 + len(null)))


def summarize_folds(fold_data, n_boot: int = 2000, seed: int = 0, n_null: int = 0) -> dict:
    """Per-fold point metrics with 95% percentile bootstrap CIs, and the fold mean.

    `fold_data` is a list of (y_test, scores). Bootstrap = stratified resampling
    within each test fold; the fold-mean interval averages the per-fold
    replicates. With n_null > 0, labels are also shuffled against the scores
    within each fold: `ap_random` is the expected AP of a random ranking and
    p_* are one-sided permutation p-values (fold mean: shuffles averaged across folds).
    """
    per_fold = []
    reps = {k: [] for k in METRIC_KEYS}
    nulls = {"roc_auc": [], "pr_auc": []}
    for f, (y, s) in enumerate(fold_data):
        pm = point_metrics(y, s)
        r = bootstrap_replicates(y, s, n_boot, seed + 1000 * f)
        pm["ci"] = {k: percentile_ci(r[k]) for k in METRIC_KEYS}
        if n_null:
            nl = permutation_null(y, s, n_null, seed + 1000 * f + 500)
            pm["ap_random"] = float(np.mean(nl["pr_auc"]))
            pm["p_auc"] = perm_p_greater(nl["roc_auc"], pm["roc_auc"])
            pm["p_ap"] = perm_p_greater(nl["pr_auc"], pm["pr_auc"])
            for k in nulls:
                nulls[k].append(nl[k])
        per_fold.append(pm)
        for k in METRIC_KEYS:
            reps[k].append(r[k])
    mean = {k: float(np.mean([pf[k] for pf in per_fold])) for k in METRIC_KEYS}
    mean["prevalence"] = float(np.mean([pf["prevalence"] for pf in per_fold]))
    mean_ci = {k: percentile_ci(np.mean(np.vstack(reps[k]), axis=0)) for k in METRIC_KEYS}
    if n_null:
        mean["ap_random"] = float(np.mean([pf["ap_random"] for pf in per_fold]))
        mean["p_auc"] = perm_p_greater(np.mean(np.vstack(nulls["roc_auc"]), axis=0), mean["roc_auc"])
        mean["p_ap"] = perm_p_greater(np.mean(np.vstack(nulls["pr_auc"]), axis=0), mean["pr_auc"])
    return {"per_fold": per_fold, "mean": mean, "mean_ci": mean_ci, "_replicates": reps}


def make_chunks(block_ids, chunk: int = 100) -> np.ndarray:
    """Chunk id per row: consecutive runs of `chunk` rows, never crossing a block."""
    block_ids = np.asarray(block_ids)
    out = np.empty(len(block_ids), dtype=np.int64)
    cid = -1
    pos = 0
    for i in range(len(block_ids)):
        if i == 0 or block_ids[i] != block_ids[i - 1] or pos == chunk:
            cid += 1
            pos = 0
        out[i] = cid
        pos += 1
    return out


def _chunk_slices(chunk_ids):
    chunk_ids = np.asarray(chunk_ids)
    starts = np.r_[0, np.flatnonzero(np.diff(chunk_ids)) + 1]
    ends = np.r_[starts[1:], len(chunk_ids)]
    return [slice(a, b) for a, b in zip(starts, ends)]


def budget_k(n_c: int, budget_per_100: float) -> int:
    return int(np.floor(budget_per_100 * n_c / 100.0 + 0.5))


def top_k_per_chunk(scores, budget_per_100: float, chunk_ids, seed: int = 0) -> np.ndarray:
    """Boolean alarm mask: within each chunk (consecutive entities in time order)
    flag the budget_k highest scores. Ties at the cut are broken by a seeded
    random key. Uses no labels."""
    s = np.asarray(scores, dtype=np.float64)
    rng = np.random.default_rng(seed)
    tiebreak = rng.random(len(s))
    alarm = np.zeros(len(s), dtype=bool)
    for sl in _chunk_slices(chunk_ids):
        k = budget_k(sl.stop - sl.start, budget_per_100)
        if k <= 0:
            continue
        idx = np.lexsort((tiebreak[sl], -s[sl]))[:k]
        alarm[sl.start + idx] = True
    return alarm


def random_inspection(y, budget_per_100: float, chunk_ids, n_sim: int = 20000, seed: int = 0) -> dict:
    """Fails caught by inspecting budget_k random entities per chunk:
    exact expectation and a simulated 95% interval (hypergeometric per chunk)."""
    y = np.asarray(y).astype(bool)
    rng = np.random.default_rng(seed)
    expected = 0.0
    n_alarm = 0
    sims = np.zeros(n_sim)
    for sl in _chunk_slices(chunk_ids):
        yc = y[sl]
        n_c, f_c = len(yc), int(yc.sum())
        k = budget_k(n_c, budget_per_100)
        if k <= 0:
            continue
        n_alarm += k
        expected += k * f_c / n_c
        if f_c > 0:
            sims += rng.hypergeometric(f_c, n_c - f_c, k, size=n_sim)
    lo, hi = np.percentile(sims, [2.5, 97.5])
    return {"expected": float(expected), "lo": float(lo), "hi": float(hi), "n_alarm": int(n_alarm)}


def confusion_at(y, alarm) -> dict:
    y = np.asarray(y).astype(bool)
    a = np.asarray(alarm).astype(bool)
    tp = int(np.sum(a & y))
    fp = int(np.sum(a & ~y))
    fn = int(np.sum(~a & y))
    tn = int(np.sum(~a & ~y))
    n = len(y)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "accuracy": (tp + tn) / n,
        "balanced_accuracy": 0.5 * (tp / max(tp + fn, 1) + tn / max(tn + fp, 1)),
        "recall": tp / max(tp + fn, 1),
        "precision": tp / (tp + fp) if (tp + fp) > 0 else float("nan"),
        "false_alarm_rate": fp / max(fp + tn, 1),
        "alarm_rate": (tp + fp) / n,
    }
