"""Q4: which signals matter, and do they stay the same over time?

Permutation importance = drop in ROC-AUC on a chronological *test* fold when
one raw feature is shuffled. It is computed after the (column-wise)
preprocessing: dropping, median imputation, scaling and quantile binning all act
on each column separately, so shuffling raw column j and then transforming is
the same as transforming and then shuffling column j (tested). That lets the
final model score all shuffles in large batches.
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
from scipy import stats

from .characterize import bh_fdr, univariate_auc, univariate_mw_p
from .models import final_score

IMPORTANCE_KEYS = ("mspc", "l1lr", "rf", "hgb")
N_REPEATS = 5
TOP_K = (10, 20)
BATCH_ROWS = 40_000


def rank_auc_rows(S: np.ndarray, y: np.ndarray) -> np.ndarray:
    """ROC-AUC of every row of S against y (average ranks for ties)."""
    yb = np.asarray(y).astype(bool)
    n1 = int(yb.sum())
    n0 = len(yb) - n1
    R = stats.rankdata(np.atleast_2d(S), axis=1)
    return (R[:, yb].sum(axis=1) - n1 * (n1 + 1) / 2) / (n1 * n0)


def permutation_importance(pipe, X: np.ndarray, y: np.ndarray, n_repeats: int = N_REPEATS, seed: int = 0) -> dict:
    pre, model = pipe[:-1], pipe[-1]
    Z = pre.transform(X)
    n, p = Z.shape
    base = float(rank_auc_rows(final_score(model, Z), y)[0])
    rng = np.random.default_rng(seed)
    perms = np.array([[rng.permutation(n) for _ in range(n_repeats)] for _ in range(p)])
    per_batch = max(1, BATCH_ROWS // (n * n_repeats))
    drops = np.empty((p, n_repeats))
    for start in range(0, p, per_batch):
        feats = list(range(start, min(p, start + per_batch)))
        stacked = np.tile(Z, (len(feats) * n_repeats, 1))
        for bi, j in enumerate(feats):
            for r in range(n_repeats):
                row0 = (bi * n_repeats + r) * n
                stacked[row0 : row0 + n, j] = Z[perms[j, r], j]
        S = final_score(model, stacked).reshape(len(feats) * n_repeats, n)
        drops[start : start + len(feats)] = base - rank_auc_rows(S, y).reshape(len(feats), n_repeats)
    keep = pipe["drop"].keep_
    raw = np.flatnonzero(keep)
    mean = np.zeros(keep.size)
    sd = np.zeros(keep.size)
    mean[raw] = drops.mean(axis=1)
    sd[raw] = drops.std(axis=1, ddof=1)
    return {"base_auc": base, "mean": mean, "std": sd, "kept": raw}


def top_k(values: np.ndarray, k: int, candidates: np.ndarray | None = None) -> list[int]:
    """Indices of the k largest values (ties -> lower index), restricted to candidates."""
    idx = np.arange(len(values)) if candidates is None else np.asarray(candidates)
    order = np.lexsort((idx, -values[idx]))
    return [int(i) for i in idx[order[:k]]]


def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if (a | b) else float("nan")


def pairwise_jaccard(sets: list) -> dict:
    vals = [jaccard(a, b) for a, b in combinations(sets, 2)]
    return {"mean": float(np.mean(vals)), "min": float(np.min(vals)), "max": float(np.max(vals))}


def null_jaccard(m: int, k: int, n_sim: int = 20000, seed: int = 0) -> dict:
    """Jaccard of two independent uniformly random k-subsets of m features."""
    rng = np.random.default_rng(seed)
    vals = np.empty(n_sim)
    for i in range(n_sim):
        a = rng.choice(m, k, replace=False)
        b = rng.choice(m, k, replace=False)
        inter = np.intersect1d(a, b).size
        vals[i] = inter / (2 * k - inter)
    return {"mean": float(vals.mean()), "p95": float(np.percentile(vals, 95))}


def recurring(sets: list, min_folds: int) -> list[int]:
    counts = {}
    for s in sets:
        for j in s:
            counts[j] = counts.get(j, 0) + 1
    return sorted(j for j, c in counts.items() if c >= min_folds)


def model_stability(imps: list[dict], cluster_ids: np.ndarray, feature_names) -> dict:
    """Top-k stability of permutation importance across chronological folds."""
    out = {"per_fold": [], "top_k": {}}
    for f, imp in enumerate(imps):
        m = imp["mean"]
        top = top_k(m, 10, imp["kept"])
        out["per_fold"].append(
            {
                "fold": f + 1,
                "base_auc": imp["base_auc"],
                "max_drop": float(m.max()),
                "n_drop_gt_001": int(np.sum(m > 0.01)),
                "n_drop_gt_0": int(np.sum(m > 0)),
                "top10": [feature_names[j] for j in top],
                "top10_drop": [float(m[j]) for j in top],
                "top10_drop_sd": [float(imp["std"][j]) for j in top],
            }
        )
    for k in TOP_K:
        sets = [top_k(imp["mean"], k, imp["kept"]) for imp in imps]
        csets = [{int(cluster_ids[j]) for j in s} for s in sets]
        m_common = int(len(set.intersection(*[set(imp["kept"].tolist()) for imp in imps])))
        out["top_k"][str(k)] = {
            "jaccard": pairwise_jaccard(sets),
            "cluster_jaccard": pairwise_jaccard(csets),
            "null": null_jaccard(m_common, k),
            "in_ge3_folds": [feature_names[j] for j in recurring(sets, 3)],
            "in_all_folds": [feature_names[j] for j in recurring(sets, len(sets))],
        }
    return out


def univariate_filter_stability(X, y, folds, feature_names, k: int = 10) -> dict:
    """The secom.names-style univariate screen, run per chronological fold:
    rank features by |AUC - 0.5| on the training rows; do the top-k agree across
    folds, and do they still separate fails in the following test block?"""
    sets, oriented_test, per_fold = [], [], []
    for f, (tr, te) in enumerate(folds):
        a_tr = univariate_auc(X[tr], y[tr])
        strength = np.where(np.isfinite(a_tr), np.abs(a_tr - 0.5), -1.0)
        top = top_k(strength, k)
        a_te = univariate_auc(X[te], y[te], min_per_class=3)
        ori = []
        for j in top:
            if np.isfinite(a_te[j]):
                ori.append(a_te[j] if a_tr[j] >= 0.5 else 1 - a_te[j])
        sets.append(top)
        oriented_test.append(float(np.mean(ori)) if ori else float("nan"))
        per_fold.append(
            {
                "fold": f + 1,
                "top": [feature_names[j] for j in top],
                "train_auc_strength_mean": float(np.mean(strength[top]) + 0.5),
                "test_oriented_auc_mean": oriented_test[-1],
            }
        )
    return {"k": k, "per_fold": per_fold, "jaccard": pairwise_jaccard(sets)}


def halves(X, y) -> dict:
    """Univariate signal in the first vs second half of time (signed AUC - 0.5)."""
    n = len(y)
    h = n // 2
    a1 = univariate_auc(X[:h], y[:h])
    a2 = univariate_auc(X[h:], y[h:])
    ok = np.isfinite(a1) & np.isfinite(a2)
    rho = stats.spearmanr(a1[ok] - 0.5, a2[ok] - 0.5)
    q_all = bh_fdr(univariate_mw_p(X, y))
    q1 = bh_fdr(univariate_mw_p(X[:h], y[:h]))
    q2 = bh_fdr(univariate_mw_p(X[h:], y[h:]))
    s1 = set(np.flatnonzero(q1 < 0.05).tolist())
    s2 = set(np.flatnonzero(q2 < 0.05).tolist())
    a_all = univariate_auc(X, y)
    return {
        "n_features_compared": int(ok.sum()),
        "spearman_rho": float(rho.statistic),
        "spearman_p": float(rho.pvalue),
        "bh_q005_full": int(np.nansum(q_all < 0.05)),
        "bh_q005_first_half": len(s1),
        "bh_q005_second_half": len(s2),
        "bh_q005_both_halves": len(s1 & s2),
        "max_abs_auc_dev_full": float(np.nanmax(np.abs(a_all - 0.5))),
        "_auc_first": a1,
        "_auc_second": a2,
    }
