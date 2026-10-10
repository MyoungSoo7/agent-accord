"""지표와 신뢰구간. 모든 CI 는 seed(=독립 반복) 단위 값의 평균에 대한 t 구간이다."""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy import stats


def t_ci(x, conf=0.95):
    """(평균, 하한, 상한, n). 유한값만 쓴다."""
    x = np.asarray(x, float).ravel()
    x = x[np.isfinite(x)]
    n = x.size
    if n == 0:
        return (np.nan, np.nan, np.nan, 0)
    m = float(x.mean())
    if n < 2:
        return (m, np.nan, np.nan, n)
    half = float(stats.t.ppf(0.5 + conf / 2, n - 1) * x.std(ddof=1) / np.sqrt(n))
    return (m, m - half, m + half, n)


def boot_ci(x, conf=0.95, n_boot=10000, seed=12345):
    """백분위 부트스트랩 (대부분 0 인 사건 수처럼 치우친 지표용). 고정 난수."""
    x = np.asarray(x, float).ravel()
    x = x[np.isfinite(x)]
    n = x.size
    if n == 0:
        return (np.nan, np.nan, np.nan, 0)
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, n, (n_boot, n))].mean(axis=1)
    lo, hi = np.quantile(means, [(1 - conf) / 2, (1 + conf) / 2])
    return (float(x.mean()), float(lo), float(hi), n)


def ci_dict(x, conf=0.95, method="t"):
    m, lo, hi, n = (t_ci if method == "t" else boot_ci)(x, conf)
    return {"mean": m, "lo": lo, "hi": hi, "n": n, "method": method}


def mse(Y, T, start=0):
    e = Y[:, start:] - T
    return (e * e).mean(axis=1)


def oos_pct(Y, T, spec, start=0):
    return 100.0 * (np.abs(Y[:, start:] - T) > spec).mean(axis=1)


def first_run(mask, K=1):
    """행마다 K 개 연속 True 가 처음 시작하는 인덱스, 없으면 -1."""
    n, N = mask.shape
    if K == 1:
        ok = mask
    else:
        if N < K:
            return np.full(n, -1)
        ok = sliding_window_view(mask, K, axis=1).all(axis=-1)
    idx = np.argmax(ok, axis=1)
    idx[~ok.any(axis=1)] = -1
    return idx


def recovery_after_pm(Y, T, spec, pm_wafers, run_len=5, horizon=100):
    """PM 웨이퍼부터 '연속 run_len 장이 모두 규격 안'이 처음 시작될 때까지의 웨이퍼 수. (n, n_pm)."""
    inspec = np.abs(Y - T) <= spec
    n, N = Y.shape
    out = np.full((n, len(pm_wafers)), np.nan)
    for j, k0 in enumerate(pm_wafers):
        seg = inspec[:, k0 : min(N, k0 + horizon + run_len)]
        if seg.shape[1] < run_len:
            continue
        f = first_run(seg, run_len).astype(float)
        f[f < 0] = horizon
        out[:, j] = f
    return out


def post_pm_count(flag, pm_wafers, window=30):
    """PM 뒤 window 장 안에서 flag(예: 규격 이탈)가 켜진 웨이퍼 수. (n, n_pm)."""
    n, N = flag.shape
    out = np.full((n, len(pm_wafers)), np.nan)
    for j, k0 in enumerate(pm_wafers):
        if k0 + window <= N:
            out[:, j] = flag[:, k0 : k0 + window].sum(axis=1)
    return out


def aligned(arr, pm_wafers, before=10, after=150):
    """PM 기준으로 정렬한 구간을 모은다. (n·n_pm, before+after)."""
    n, N = arr.shape
    segs = [arr[:, k0 - before : k0 + after] for k0 in pm_wafers if k0 - before >= 0 and k0 + after <= N]
    return np.concatenate(segs, axis=0) if segs else np.empty((0, before + after))
