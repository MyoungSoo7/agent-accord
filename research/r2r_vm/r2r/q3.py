"""Q3 — 계측 표본화(1/m) + VM: 계측만 / VM 전부 / T² 게이트 / T²+사후검증(PM 인지) 게이트."""
from __future__ import annotations

import numpy as np

from . import metrics as M, sim, vm as V
from .common import (BURN, CAL_LAM, DELAY, EQ_TOL, EVAL_SEEDS, K_BOOST, N_WAFERS, P, Q3_LAM, Q3_M, Q3_STRATS,
                     RES_K, T2_Q, TUNE_SEEDS, boost_mask, sampling_mask, steady_mask)
from .controllers import CtrlSpec
from .process import make_disturbance

PROFILE_M = 10      # PM 정렬 그림에 쓰는 m
PROFILE_WIN = (10, 150)
POST_WIN = 100      # 'PM 뒤 100장' 규격 이탈


def ctrl(lam):
    return CtrlSpec("A", lam, pm_reset=True)


# 전략 → (시뮬레이터 전략, PM 뒤 전수 계측 여부)
STRAT_DEF = {"meas": ("meas", False), "meas_boost": ("meas", True), "vm_all": ("vm_all", False),
             "vm_t2": ("vm_t2", False), "vm_t2r": ("vm_t2r", False), "vm_refresh": ("vm_refresh", True)}


def mask_for(strat, seeds, m, pm_wafers):
    meas = sampling_mask(seeds, N_WAFERS, m)
    return boost_mask(meas, pm_wafers, K_BOOST) if STRAT_DEF[strat][1] else meas


def run_one(p, dist, X, strat, meas, lam, thr=None):
    base = STRAT_DEF[strat][0]
    if base == "meas":
        return sim.run(p, dist, ctrl(lam), DELAY, measured=meas)
    ref, sc = V.burnin_scaling(X, BURN)
    t2_thr, res_thr = thr if thr is not None else (np.inf, np.inf)
    vmc = sim.VMConfig(base, X, V.OnlineRidgeVM(dist.n_seeds, ref, sc), t2_thr=t2_thr, res_thr=res_thr)
    return sim.run(p, dist, ctrl(lam), DELAY, measured=meas, vmc=vmc)


def ratio_ci(a, b, n_boot=10000, seed=7):
    """평균 MSE 비 mean(a)/mean(b) 와 seed 짝 부트스트랩 95 % 구간."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, a.size, (n_boot, a.size))
    r = a[idx].mean(axis=1) / b[idx].mean(axis=1)
    lo, hi = np.quantile(r, [0.025, 0.975])
    return {"mean": float(a.mean() / b.mean()), "lo": float(lo), "hi": float(hi), "n": int(a.size), "method": "boot"}


def calibrate(p, dist, X):
    """정상 시나리오·튜닝 seed 에서 m 별 T² 문턱과 사후 검증 문턱을 정한다 (VM 전부 사용, λ=CAL_LAM)."""
    st = steady_mask(dist.n_wafers, dist.pm_wafers)[None, :]
    thr, diag = {}, {}
    for m in Q3_M[1:]:
        meas = sampling_mask(dist.seeds, dist.n_wafers, m)
        r = run_one(p, dist, X, "vm_all", meas, CAL_LAM)
        un = st & ~meas
        t2_thr = float(np.quantile(r.t2[un], T2_Q))
        res = (r.Y + dist.v - r.vm_pred)[st & meas]
        res_rms = float(np.sqrt(np.mean(res**2)))
        thr[m] = (t2_thr, RES_K * res_rms)
        err = (r.vm_pred - r.Y)[un]
        diag[m] = {"t2_thr": t2_thr, "res_rms": res_rms, "res_thr": RES_K * res_rms,
                   "vm_rmse_true": float(np.sqrt(np.mean(err**2))),
                   "y_sd_unmeasured": float(np.std(r.Y[un]))}
    return thr, diag


def tune(p, dist, X, thr):
    best, grid = {}, []
    for m in Q3_M:
        for strat in (("meas",) if m == 1 else Q3_STRATS):
            meas = mask_for(strat, dist.seeds, m, dist.pm_wafers)
            rows = []
            for lam in Q3_LAM:
                r = run_one(p, dist, X, strat, meas, lam, thr.get(m))
                v = float(M.mse(r.Y, p.target, BURN).mean())
                rows.append((lam, v))
                grid.append({"m": m, "strategy": strat, "lam": lam, "mse": v})
            best[(strat, m)] = min(rows, key=lambda t: t[1])[0]
    return best, grid


def shift_sign(seeds, scenario, pad_idx):
    """PM 뒤 오차를 '낡은 VM 이 미는 방향'으로 정렬하는 부호. (n, n_pm)."""
    off, cp = V.relation_shifts(seeds, scenario)
    if scenario == "silent":
        return np.sign(cp[:, pad_idx])
    if scenario == "offset":
        return -np.sign(off[:, pad_idx])
    return np.ones((len(seeds), len(pad_idx)))


def run():
    p = P
    out, arrays = {}, {"profiles": {}}
    dist_t = make_disturbance(p, N_WAFERS, TUNE_SEEDS)
    X_t = V.make_features(dist_t, p.b0, "normal")
    thr, diag = calibrate(p, dist_t, X_t)
    out["calibration"] = {str(m): d for m, d in diag.items()}
    best, grid = tune(p, dist_t, X_t, thr)
    out["tuned_lam"] = {f"{s}|{m}": lam for (s, m), lam in best.items()}
    out["tune_grid"] = grid

    dist = make_disturbance(p, N_WAFERS, EVAL_SEEDS)
    pm = dist.pm_wafers
    pads = dist.pad_idx[pm]
    T = p.target
    base_mse = None
    meas_cache = {}
    rows = []
    for sc in V.SCENARIOS:
        X = V.make_features(dist, p.b0, sc)
        sgn = shift_sign(EVAL_SEEDS, sc, pads)
        for m in Q3_M:
            for strat in (("meas",) if m == 1 else Q3_STRATS):
                meas = mask_for(strat, EVAL_SEEDS, m, pm)
                lam = best[(strat, m)]
                key = (strat, m)
                if STRAT_DEF[strat][0] == "meas" and key in meas_cache:  # 계측만 쓰는 제어는 시나리오와 무관
                    r = meas_cache[key]
                else:
                    r = run_one(p, dist, X, strat, meas, lam, thr.get(m))
                    if STRAT_DEF[strat][0] == "meas":
                        meas_cache[key] = r
                mse = M.mse(r.Y, T, BURN)
                if m == 1 and strat == "meas":
                    base_mse = mse
                oos = np.abs(r.Y - T) > p.spec
                post = M.post_pm_count(oos, pm, POST_WIN) / POST_WIN * 100.0
                row = {"scenario": sc, "m": m, "strategy": strat, "lam": lam,
                       "mse": M.ci_dict(mse), "oos_pct": M.ci_dict(M.oos_pct(r.Y, T, p.spec, BURN)),
                       "post_pm_oos_pct": M.ci_dict(np.nanmean(post, axis=1)),
                       "ratio_vs_full": ratio_ci(mse, base_mse),
                       "meas_frac": float(meas[:, BURN:].mean())}
                if r.vm_used is not None:
                    un = ~meas[:, BURN:]
                    row["vm_use_frac"] = float(r.vm_used[:, BURN:][un].mean())
                    err = (r.vm_pred - r.Y)[:, BURN:][un]
                    row["vm_rmse_true"] = float(np.sqrt(np.mean(err**2)))
                rows.append(row)
                if m == PROFILE_M:
                    b, a = PROFILE_WIN
                    segs = M.aligned(r.Y - T, pm, b, a).reshape(len(pm), len(EVAL_SEEDS), b + a)
                    prof = (segs * sgn.T[:, :, None]).reshape(-1, b + a).mean(axis=0)
                    use = None
                    if r.vm_used is not None:
                        un_mask = ~meas
                        u_seg = M.aligned(r.vm_used.astype(float), pm, b, a)
                        n_seg = M.aligned(un_mask.astype(float), pm, b, a)
                        with np.errstate(invalid="ignore"):
                            use = u_seg.sum(axis=0) / n_seg.sum(axis=0)
                    arrays["profiles"][(sc, strat)] = (prof, use)
    out["eval"] = rows

    # 동등 품질: MSE 비 ≤ EQ_TOL 인 가장 큰 m (점추정 / 보수적: 구간 상한)
    eq = {}
    for sc in V.SCENARIOS:
        for strat in Q3_STRATS:
            ms = [r for r in rows if r["scenario"] == sc and (r["strategy"] == strat or (r["m"] == 1))]
            ok_pt = [r for r in ms if r["ratio_vs_full"]["mean"] <= EQ_TOL]
            ok_cons = [r for r in ms if r["ratio_vs_full"]["hi"] <= EQ_TOL]
            bp = max(ok_pt, key=lambda r: r["m"]) if ok_pt else None
            bc = max(ok_cons, key=lambda r: r["m"]) if ok_cons else None
            eq[f"{sc}|{strat}"] = {"max_m_point": bp["m"] if bp else None,
                                   "meas_frac_point": bp["meas_frac"] if bp else None,
                                   "max_m_conservative": bc["m"] if bc else None,
                                   "meas_frac_conservative": bc["meas_frac"] if bc else None}
    out["equal_quality"] = eq
    return out, arrays
