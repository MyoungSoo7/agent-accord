"""Q1: data characterisation (missingness, constant features, imbalance, time, clustering, drift).

Label-free statistics may use the whole data set; nothing here feeds a model
that is evaluated later.
"""

from __future__ import annotations

import numpy as np
from scipy import stats
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from sklearn.neighbors import NearestNeighbors

from .data import SecomData

N_PERM = 10000
ACF_LAGS = 20
LJUNG_BOX_LAGS = 10


def bh_fdr(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg q-values (NaN-aware)."""
    p = np.asarray(p, dtype=np.float64)
    q = np.full_like(p, np.nan)
    ok = np.isfinite(p)
    pv = p[ok]
    m = pv.size
    if m == 0:
        return q
    order = np.argsort(pv)
    ranked = pv[order] * m / np.arange(1, m + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(ranked, 1.0)
    q[ok] = out
    return q


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / den
    return float(centre - half), float(centre + half)


def perm_p(null: np.ndarray, observed: float, tail: str) -> float:
    """(b + 1) / (N + 1) permutation p-value."""
    if tail == "greater":
        b = np.sum(null >= observed - 1e-12)
    elif tail == "less":
        b = np.sum(null <= observed + 1e-12)
    else:
        raise ValueError(tail)
    return float((b + 1) / (len(null) + 1))


# ---------------------------------------------------------------- missingness
def missingness(X: np.ndarray, y: np.ndarray, ts: np.ndarray) -> dict:
    nan = np.isnan(X)
    n, p = X.shape
    mf = nan.mean(axis=0)
    mr = nan.mean(axis=1)
    thresholds = (0.0, 0.05, 0.5, 0.9)
    high = np.flatnonzero(mf > 0.05)
    # features that go missing together: identical missingness columns
    patterns = {}
    for j in high:
        patterns.setdefault(nan[:, j].tobytes(), []).append(int(j))
    groups = sorted(patterns.values(), key=len, reverse=True)
    # time structure: missing fraction in the first vs second half of time
    half = n // 2
    m1, m2 = nan[:half].mean(axis=0), nan[half:].mean(axis=0)
    regime = int(np.sum(((m1 > 0.5) & (m2 < 0.05)) | ((m2 > 0.5) & (m1 < 0.05))))
    mw = stats.mannwhitneyu(mr[y == 1], mr[y == 0], alternative="two-sided")
    return {
        "missing_cells": int(nan.sum()),
        "missing_cell_frac": float(nan.mean()),
        "features_with_missing_gt": {f"{t:g}": int(np.sum(mf > t)) for t in thresholds},
        "feature_missing_max": float(mf.max()),
        "feature_missing_median": float(np.median(mf)),
        "row_missing_min": float(mr.min()),
        "row_missing_median": float(np.median(mr)),
        "row_missing_max": float(mr.max()),
        "rows_complete": int(np.sum(mr == 0)),
        "n_patterns_among_gt05": len(groups),
        "largest_pattern_group": len(groups[0]) if groups else 0,
        "pattern_group_sizes": [len(g) for g in groups],
        "features_regime_missing_half_vs_half": regime,
        "row_missing_median_fail": float(np.median(mr[y == 1])),
        "row_missing_median_pass": float(np.median(mr[y == 0])),
        "row_missing_mannwhitney_p": float(mw.pvalue),
        "_feature_missing_frac": mf,  # for figures (dropped from json)
    }


def constant_features(X: np.ndarray) -> dict:
    nan = np.isnan(X)
    n_distinct = np.array([np.unique(X[~nan[:, j], j]).size for j in range(X.shape[1])])
    modal_frac = np.zeros(X.shape[1])
    for j in range(X.shape[1]):
        col = X[~nan[:, j], j]
        if col.size:
            _, counts = np.unique(col, return_counts=True)
            modal_frac[j] = counts.max() / col.size
    const = n_distinct <= 1
    near = (~const) & (modal_frac >= 0.95)
    return {
        "constant": int(const.sum()),
        "two_valued": int(np.sum(n_distinct == 2)),
        "near_constant_modal_ge_095": int(near.sum()),
        "near_constant_modal_ge_099": int(np.sum((~const) & (modal_frac >= 0.99))),
        "dropped_full_data_rule": int(np.sum(const | (nan.mean(axis=0) > 0.5))),
        "kept_full_data_rule": int(np.sum(~const & (nan.mean(axis=0) <= 0.5))),
    }


# --------------------------------------------------------------- time & labels
def time_summary(ts: np.ndarray, y: np.ndarray) -> dict:
    t = ts.astype("datetime64[s]")
    gaps_min = np.diff(t).astype(np.int64) / 60.0
    days = (t - t[0].astype("datetime64[D]")).astype("timedelta64[D]").astype(int)
    per_day = np.bincount(days)
    n = len(y)
    return {
        "first": str(t[0]).replace("T", " "),
        "last": str(t[-1]).replace("T", " "),
        "span_days": float((t[-1] - t[0]).astype(np.int64) / 86400.0),
        "calendar_days": int(per_day.size),
        "days_without_entities": int(np.sum(per_day == 0)),
        "entities_per_active_day_median": float(np.median(per_day[per_day > 0])),
        "duplicate_timestamps": int(n - np.unique(t).size),
        "median_gap_min": float(np.median(gaps_min)),
        "max_gap_hours": float(gaps_min.max() / 60.0),
        "sorted_in_file": True,
        "n": int(n),
        "n_fail": int(y.sum()),
        "n_pass": int(n - y.sum()),
        "prevalence": float(y.mean()),
        "majority_accuracy": float(1 - y.mean()),
    }


def week_index(ts: np.ndarray) -> np.ndarray:
    t = ts.astype("datetime64[s]")
    d0 = t[0].astype("datetime64[D]")
    return ((t - d0).astype("timedelta64[D]").astype(int) // 7).astype(int)


def weekly_table(y: np.ndarray, ts: np.ndarray) -> list[dict]:
    w = week_index(ts)
    d0 = ts[0].astype("datetime64[D]")
    rows = []
    for k in range(w.max() + 1):
        m = w == k
        n_k, f_k = int(m.sum()), int(y[m].sum())
        lo, hi = wilson(f_k, n_k)
        start = d0 + np.timedelta64(7 * k, "D")
        rows.append(
            {
                "week": k + 1,
                "start": str(start),
                "n": n_k,
                "fails": f_k,
                "rate": f_k / n_k if n_k else float("nan"),
                "ci": [lo, hi],
            }
        )
    return rows


def _acf_matrix(Y: np.ndarray, lags: int) -> np.ndarray:
    """Autocorrelation of each row of Y (0/1 sequences) at lags 1..lags."""
    Yc = Y - Y.mean(axis=1, keepdims=True)
    den = np.sum(Yc**2, axis=1)
    out = np.empty((Y.shape[0], lags))
    for k in range(1, lags + 1):
        out[:, k - 1] = np.sum(Yc[:, :-k] * Yc[:, k:], axis=1) / den
    return out


def fail_clustering(y: np.ndarray, ts: np.ndarray, n_perm: int = N_PERM, seed: int = 0) -> dict:
    """Do fails cluster in time? Runs test, ACF / Ljung-Box and weekly homogeneity,
    each against a permutation null that shuffles labels over time positions."""
    y = np.asarray(y, dtype=np.float64)
    n = len(y)
    n1 = y.sum()
    n0 = n - n1
    rng = np.random.default_rng(seed)
    perms = rng.permuted(np.tile(y, (n_perm, 1)), axis=1)

    def runs(Y):
        return 1 + np.sum(Y[:, 1:] != Y[:, :-1], axis=1)

    r_obs = float(runs(y[None])[0])
    r_null = runs(perms)
    e_r = 1 + 2 * n1 * n0 / n
    v_r = 2 * n1 * n0 * (2 * n1 * n0 - n) / (n**2 * (n - 1))
    z = (r_obs - e_r) / np.sqrt(v_r)

    acf_obs = _acf_matrix(y[None], ACF_LAGS)[0]
    acf_null = _acf_matrix(perms, ACF_LAGS)
    band = np.percentile(acf_null, [2.5, 97.5], axis=0)

    def ljung_box(acf):
        k = np.arange(1, LJUNG_BOX_LAGS + 1)
        return n * (n + 2) * np.sum(acf[..., :LJUNG_BOX_LAGS] ** 2 / (n - k), axis=-1)

    lb_obs = float(ljung_box(acf_obs))
    lb_null = ljung_box(acf_null)

    w = week_index(ts)
    onehot = np.zeros((n, w.max() + 1))
    onehot[np.arange(n), w] = 1.0
    n_w = onehot.sum(axis=0)
    p_bar = n1 / n

    def chi2_weeks(Y):
        f = Y @ onehot
        e1 = n_w * p_bar
        e0 = n_w * (1 - p_bar)
        return np.sum((f - e1) ** 2 / e1 + ((n_w - f) - e0) ** 2 / e0, axis=-1)

    chi_obs = float(chi2_weeks(y[None])[0])
    chi_null = chi2_weeks(perms)

    # fail rate by halves of time
    half = n // 2
    return {
        "n_perm": n_perm,
        "runs_observed": int(r_obs),
        "runs_expected": float(e_r),
        "runs_z": float(z),
        "runs_p_normal_one_sided": float(stats.norm.cdf(z)),
        "runs_p_perm_one_sided": perm_p(r_null, r_obs, "less"),
        "acf": [float(a) for a in acf_obs],
        "acf_band_lo": [float(a) for a in band[0]],
        "acf_band_hi": [float(a) for a in band[1]],
        "acf_lag1": float(acf_obs[0]),
        "acf_lags_above_band": [int(k + 1) for k in np.flatnonzero(acf_obs > band[1])],
        "ljung_box_lags": LJUNG_BOX_LAGS,
        "ljung_box_q": lb_obs,
        "ljung_box_p_chi2": float(stats.chi2.sf(lb_obs, LJUNG_BOX_LAGS)),
        "ljung_box_p_perm": perm_p(lb_null, lb_obs, "greater"),
        "weekly_chi2": chi_obs,
        "weekly_chi2_df": int(onehot.shape[1] - 1),
        "weekly_chi2_p_perm": perm_p(chi_null, chi_obs, "greater"),
        "fail_rate_first_half": float(y[:half].mean()),
        "fail_rate_second_half": float(y[half:].mean()),
    }


# ----------------------------------------------------------------------- drift
def _kept_full(X: np.ndarray) -> np.ndarray:
    nan = np.isnan(X)
    n_distinct = np.array([np.unique(X[~nan[:, j], j]).size for j in range(X.shape[1])])
    return (nan.mean(axis=0) <= 0.5) & (n_distinct >= 2)


def _median_impute_standardize(X: np.ndarray) -> np.ndarray:
    med = np.nanmedian(X, axis=0)
    Z = np.where(np.isnan(X), med, X)
    sd = Z.std(axis=0)
    sd[sd == 0] = 1.0
    return (Z - Z.mean(axis=0)) / sd


def drift(X: np.ndarray) -> dict:
    """KS test, first vs second half of time, per kept feature (label-free)."""
    keep = np.flatnonzero(_kept_full(X))
    n = X.shape[0]
    half = n // 2
    pvals = np.full(X.shape[1], np.nan)
    for j in keep:
        a = X[:half, j]
        b = X[half:, j]
        a, b = a[~np.isnan(a)], b[~np.isnan(b)]
        if a.size >= 10 and b.size >= 10:
            pvals[j] = stats.ks_2samp(a, b).pvalue
    q = bh_fdr(pvals)
    tested = int(np.isfinite(pvals).sum())
    return {
        "features_tested": tested,
        "ks_bh_q_lt_005": int(np.nansum(q < 0.05)),
        "ks_bh_q_lt_005_frac": float(np.nansum(q < 0.05) / tested),
    }


def temporal_neighbours(X: np.ndarray) -> dict:
    """How far apart in time is each entity from its nearest neighbour in feature space?"""
    keep = _kept_full(X)
    Z = _median_impute_standardize(X[:, keep])
    nn = NearestNeighbors(n_neighbors=2).fit(Z)
    _, idx = nn.kneighbors(Z)
    gap = np.abs(idx[:, 1] - np.arange(len(Z)))
    n = len(Z)
    out = {"median_index_gap": float(np.median(gap))}
    for g in (1, 5, 25):
        # chance that a uniformly random other entity lies within g positions
        i = np.arange(n)
        within = np.minimum(i, g) + np.minimum(n - 1 - i, g)
        out[f"frac_within_{g}"] = float(np.mean(gap <= g))
        out[f"frac_within_{g}_random"] = float(np.mean(within / (n - 1)))
    return out


def univariate_auc(X: np.ndarray, y: np.ndarray, min_per_class: int = 5) -> np.ndarray:
    """Per-feature ROC-AUC of the raw value (NaN rows excluded), 0.5 = no signal."""
    auc = np.full(X.shape[1], np.nan)
    y = np.asarray(y).astype(bool)
    for j in range(X.shape[1]):
        ok = ~np.isnan(X[:, j])
        yy, xx = y[ok], X[ok, j]
        n1, n0 = int(yy.sum()), int((~yy).sum())
        if n1 < min_per_class or n0 < min_per_class or np.unique(xx).size < 2:
            continue
        r = stats.rankdata(xx)
        auc[j] = (r[yy].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
    return auc


def univariate_mw_p(X: np.ndarray, y: np.ndarray, min_per_class: int = 5) -> np.ndarray:
    p = np.full(X.shape[1], np.nan)
    y = np.asarray(y).astype(bool)
    for j in range(X.shape[1]):
        ok = ~np.isnan(X[:, j])
        a, b = X[ok & y, j], X[ok & ~y, j]
        if a.size < min_per_class or b.size < min_per_class or np.unique(X[ok, j]).size < 2:
            continue
        p[j] = stats.mannwhitneyu(a, b, alternative="two-sided").pvalue
    return p


def correlation_clusters(X: np.ndarray, max_dist: float = 0.3) -> np.ndarray:
    """Cluster ids from |Spearman rho| (average linkage, cut at 1-|rho| = max_dist).

    Label-free, full data; used only to ask whether unstable top features are
    interchangeable correlated sensors. Dropped features get singleton ids.
    """
    keep = np.flatnonzero(_kept_full(X))
    med = np.nanmedian(X[:, keep], axis=0)
    Z = np.where(np.isnan(X[:, keep]), med, X[:, keep])
    R = stats.rankdata(Z, axis=0)
    C = np.corrcoef(R, rowvar=False)
    D = 1.0 - np.abs(np.nan_to_num(C))
    np.fill_diagonal(D, 0.0)
    D = (D + D.T) / 2
    lab = fcluster(linkage(squareform(D, checks=False), method="average"), max_dist, "distance")
    ids = np.arange(X.shape[1]) + 10_000  # singletons for dropped features
    ids[keep] = lab
    return ids


def run(d: SecomData, n_perm: int = N_PERM, seed: int = 0) -> dict:
    return {
        "time": time_summary(d.ts, d.y),
        "missing": missingness(d.X, d.y, d.ts),
        "constant": constant_features(d.X),
        "weekly": weekly_table(d.y, d.ts),
        "clustering": fail_clustering(d.y, d.ts, n_perm=n_perm, seed=seed),
        "drift": drift(d.X),
        "neighbours": temporal_neighbours(d.X),
    }
