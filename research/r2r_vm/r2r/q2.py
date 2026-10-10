"""Q2 — 마모 드리프트 + IMA(1,1) + PM 스텝에서 EWMA vs d-EWMA, 경계 포화."""
from __future__ import annotations

import numpy as np

from . import metrics as M, sim, stability as S
from .common import (BURN, D_L1, D_L2, DELAY, EVAL_SEEDS, LAM_GRID, N_WAFERS, P, POST_PM_WIN, REC_RUN,
                     SAT_K, SAT_N, SAT_WEAR, SENS_DELAY, SENS_WEAR, TUNE_SEEDS)
from .controllers import CtrlSpec
from .process import make_disturbance

FAMILIES = {
    "EWMA-A": [CtrlSpec("A", l) for l in LAM_GRID],
    "EWMA-B": [CtrlSpec("B", l) for l in LAM_GRID],
    "d-EWMA": [CtrlSpec("D", a, b) for a in D_L1 for b in D_L2],
    "EWMA-A+PMreset": [CtrlSpec("A", l, pm_reset=True) for l in LAM_GRID],
    "d-EWMA+PMreset": [CtrlSpec("D", a, b, pm_reset=True) for a in D_L1 for b in D_L2],
}
ORDER = tuple(FAMILIES)


def spec_params(spec: CtrlSpec):
    return {"kind": spec.kind, "lam": spec.lam, "lam2": spec.lam2 if spec.kind == "D" else None,
            "pm_reset": spec.pm_reset}


def tune(p, dist, delay=DELAY):
    best, grids = {}, {}
    for fam, specs in FAMILIES.items():
        rows = []
        for sp in specs:
            r = sim.run(p, dist, sp, delay)
            rows.append((sp, float(M.mse(r.Y, p.target, BURN).mean())))
        sp_best, _ = min(rows, key=lambda t: t[1])
        best[fam] = sp_best
        grids[fam] = [{**spec_params(sp), "mse": v} for sp, v in rows]
    return best, grids


def evaluate(p, dist, sp, delay=DELAY):
    r = sim.run(p, dist, sp, delay)
    T = p.target
    mse = M.mse(r.Y, T, BURN)
    oos = M.oos_pct(r.Y, T, p.spec, BURN)
    rec = M.recovery_after_pm(r.Y, T, p.spec, dist.pm_wafers, REC_RUN)
    post = M.post_pm_count(np.abs(r.Y - T) > p.spec, dist.pm_wafers, POST_PM_WIN)
    pin = r.pinned[:, BURN:].sum(axis=1).astype(float)
    rl = r.rate_limited[:, BURN:].sum(axis=1).astype(float)
    res = {
        "mse": M.ci_dict(mse), "rmse_of_mean_mse": float(np.sqrt(mse.mean())), "oos_pct": M.ci_dict(oos),
        "recovery": M.ci_dict(np.nanmean(rec, axis=1)) if rec.size else None,
        "post_pm_oos": M.ci_dict(np.nanmean(post, axis=1)) if post.size else None,
        "pinned_per_run": M.ci_dict(pin, method="boot"), "runs_pinned_pct": float(100 * (pin > 0).mean()),
        "rate_limited_per_run": M.ci_dict(rl, method="boot"),
    }
    return res, r, mse


def stability_margins(best, delay=DELAY):
    """튜닝된 제어기들의 안정 ξ 범위 (δ = delay)."""
    out = {}
    for fam, sp in best.items():
        if sp.kind == "A":
            lo, hi = S.xi_min_A(sp.lam, delay), S.xi_max_A(sp.lam, delay)
        elif sp.kind == "B":
            lo, hi = 0.0, S.critical_gain_B(delay) / sp.lam
        else:
            lo, hi = S.xi_min_D(sp.lam, sp.lam2, delay), S.xi_max_D(sp.lam, sp.lam2, delay)
        out[fam] = {"xi_lo": float(lo), "xi_hi": float(hi)}
    return out


def saturation_study(p, best, seeds=EVAL_SEEDS):
    """PM 없이 한 패드를 SAT_N 장 쓴다. 마모율별 첫 포화 시점, 지속 포화, 포화 후 품질 손실."""
    rows, profiles = [], {}
    for wear in SAT_WEAR:
        pw = p.with_(wear=wear, pm_interval=0)
        dist = make_disturbance(pw, SAT_N, seeds)
        b_pad0 = dist.b_pad[:, 0]
        n_star = (1.0 - p.target / (p.u_max * b_pad0)) / wear if wear > 0 else np.full(len(seeds), np.inf)
        for fam in ("EWMA-A", "d-EWMA"):
            sp = best[fam]
            r = sim.run(pw, dist, sp, DELAY)
            e = r.Y - p.target
            oos = np.abs(e) > p.spec
            touch = M.first_run(r.pinned, 1)
            sust = M.first_run(r.pinned, SAT_K)
            lead, after, before = [], [], []
            for i in range(len(seeds)):
                s = sust[i]
                if s < 0:
                    continue
                nxt = np.flatnonzero(oos[i, s:])
                if nxt.size:
                    lead.append(float(nxt[0]))
                if s + 100 <= SAT_N:
                    after.append(100.0 * oos[i, s : s + 100].mean())
                if s - 100 >= 0:
                    before.append(100.0 * oos[i, s - 100 : s].mean())
            row = {
                "wear": wear, "drift_A_per_wafer": p.b0 * wear * p.u_nom, "family": fam,
                "runs_touch_pct": float(100 * (touch >= 0).mean()),
                "runs_sustained_pct": float(100 * (sust >= 0).mean()),
                "first_touch": M.ci_dict(touch[touch >= 0]),
                "first_sustained": M.ci_dict(sust[sust >= 0]),
                "pred_n_star": M.ci_dict(n_star) if wear > 0 else None,
                "lead_to_first_oos": M.ci_dict(lead, method="boot"),
                "oos_pct_100_after": M.ci_dict(after, method="boot"),
                "oos_pct_100_before": M.ci_dict(before, method="boot"),
                "margin_wafers_det": float(p.spec / (p.b0 * wear * p.u_max)) if wear > 0 else None,
                "pinned_per_run": M.ci_dict(r.pinned.sum(axis=1).astype(float), method="boot"),
            }
            rows.append(row)
            if fam == "EWMA-A" and wear > 0:
                segs = [e[i, s - 50 : s + 200] for i, s in enumerate(sust) if s >= 50 and s + 200 <= SAT_N]
                if segs:
                    profiles[wear] = np.mean(segs, axis=0)
    return rows, profiles


def run():
    p = P
    out, arrays = {}, {}
    dist_t = make_disturbance(p, N_WAFERS, TUNE_SEEDS)
    best, grids = tune(p, dist_t)
    out["tuned"] = {fam: spec_params(sp) for fam, sp in best.items()}
    out["tune_grid"] = grids

    dist = make_disturbance(p, N_WAFERS, EVAL_SEEDS)
    xi = dist.rr[:, BURN:] / p.b_hat
    out["xi_operating"] = {"q005": float(np.quantile(xi, 0.005)), "q995": float(np.quantile(xi, 0.995)),
                           "min": float(xi.min()), "max": float(xi.max())}
    out["margins"] = stability_margins(best)

    main, mses, runs = {}, {}, {}
    for fam in ORDER:
        res, r, mse = evaluate(p, dist, best[fam])
        main[fam], mses[fam], runs[fam] = res, mse, r
    for fam in ORDER:
        main[fam]["mse_diff_vs_A"] = M.ci_dict(mses[fam] - mses["EWMA-A"])
        main[fam]["oos_diff_vs_A"] = M.ci_dict(
            M.oos_pct(runs[fam].Y, p.target, p.spec, BURN) - M.oos_pct(runs["EWMA-A"].Y, p.target, p.spec, BURN))
    main["d-EWMA+PMreset"]["mse_diff_vs_AR"] = M.ci_dict(mses["d-EWMA+PMreset"] - mses["EWMA-A+PMreset"])
    out["main"] = main
    # run 별 첫 포화 웨이퍼: 그때의 패드 나이·패드 초기 제거율·IMA 수준 (경계 근처에서는 포화 표시가 깜빡인다)
    pinA = runs["EWMA-A"].pinned
    starts = [(i, int(np.flatnonzero(pinA[i])[0])) for i in range(dist.n_seeds) if pinA[i].any()]
    out["main_pinned_detail"] = {
        "n_runs": len(starts),
        "min_start_age": int(min(dist.age[k] for _, k in starts)) if starts else None,
        "b_pad_at_start_mean": float(np.mean([dist.b_pad[i, dist.pad_idx[k]] for i, k in starts])) if starts else None,
        "ima_at_start_max": float(max(dist.ima[i, k] for i, k in starts)) if starts else None,
        "b_pad_all_mean": float(dist.b_pad[:, : dist.pad_idx[-1] + 1].mean()),
    }
    arrays["pm_profile"] = {fam: M.aligned(runs[fam].Y - p.target, dist.pm_wafers, 10, 60).mean(axis=0)
                            for fam in ORDER}
    # PM 직후 δ+1 장(아직 새 패드 계측이 반영되지 않은 웨이퍼)의 평균 오차
    out["pm_step_err"] = {fam: M.ci_dict(np.mean([(runs[fam].Y[:, k : k + DELAY + 1] - p.target).mean(axis=1)
                                                  for k in dist.pm_wafers], axis=0)) for fam in ORDER}
    arrays["example"] = {fam: (runs[fam].Y[0] - p.target, runs[fam].U[0]) for fam in ("EWMA-A", "EWMA-A+PMreset")}
    arrays["pm_wafers"] = dist.pm_wafers

    sens_d = []
    for d in SENS_DELAY:
        for fam in ("EWMA-A", "EWMA-B", "d-EWMA"):
            res, _, _ = evaluate(p, dist, best[fam], delay=d)
            sens_d.append({"delay": d, "family": fam, "mse": res["mse"], "oos_pct": res["oos_pct"]})
    out["sens_delay"] = sens_d

    sens_w = []
    for wear in SENS_WEAR:
        pw = p.with_(wear=wear)
        dw = make_disturbance(pw, N_WAFERS, EVAL_SEEDS)
        for fam in ("EWMA-A", "d-EWMA", "EWMA-A+PMreset"):
            res, _, _ = evaluate(pw, dw, best[fam])
            sens_w.append({"wear": wear, "family": fam, "mse": res["mse"], "oos_pct": res["oos_pct"],
                           "pinned_per_run": res["pinned_per_run"], "runs_pinned_pct": res["runs_pinned_pct"]})
    out["sens_wear"] = sens_w

    sat_rows, sat_prof = saturation_study(p, best)
    out["saturation"] = sat_rows
    arrays["sat_profile"] = sat_prof
    return out, arrays, best
