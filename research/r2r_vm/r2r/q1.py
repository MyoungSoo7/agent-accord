"""Q1 — 모델 이득 불일치 ξ 와 계측 지연 δ 에 대한 EWMA 안정성."""
from __future__ import annotations

import numpy as np

from . import sim, stability as S
from .common import P
from .controllers import CtrlSpec, EWMAIntercept
from .process import make_disturbance

DELAYS_FIG = (0, 1, 2, 3)
DELAYS_TAB = (0, 1, 2, 3, 5, 10, 25)
LAMS_TAB = (0.1, 0.2, 0.3, 0.5, 1.0)
LAM_FINE = np.linspace(0.005, 1.0, 200)
XI_FINE = np.linspace(0.01, 5.0, 250)
LAM_SIM = np.linspace(0.05, 1.0, 20)
XI_SIM = np.linspace(0.1, 5.0, 25)
SIM_STEPS = 1000
CLEAR = 0.01  # |ρ−1| 가 이보다 큰 점만 '명확'으로 본다 (경계 바로 옆은 유한 길이 시뮬레이션으로 판정 불가)


def run():
    out = {"grids": {}, "sim_check": {}, "B_boundary": [], "A_ximax": {}, "A_robust": {}, "equivalence": {}}
    arrays = {"rho": {}, "sim": {}}

    # 1) 스펙트럼 반경 격자 + 시뮬레이션 확인 (그림·일치율)
    for form in ("A", "B"):
        for d in DELAYS_FIG:
            arrays["rho"][(form, d)] = S.rho_grid(form, LAM_FINE, XI_FINE, d)
            Ls, Xs = np.meshgrid(LAM_SIM, XI_SIM)
            rho = S.rho_grid(form, LAM_SIM, XI_SIM, d)
            rho_hat = S.simulate_rho(form, Ls, Xs, d, n_steps=SIM_STEPS)
            arrays["sim"][(form, d)] = (Ls, Xs, rho, rho_hat)
            clear = np.abs(rho - 1.0) > CLEAR
            agree = (rho < 1.0) == (rho_hat < 1.0)
            fin = np.isfinite(rho_hat) & (rho_hat > 0) & clear
            out["sim_check"][f"{form}{d}"] = {
                "n_points": int(rho.size),
                "n_clear": int(clear.sum()),
                "agree_clear": float(agree[clear].mean()),
                "agree_all": float(agree.mean()),
                "max_abs_rho_err": float(np.max(np.abs(rho_hat - rho)[fin])) if fin.any() else None,
                "stable_frac": float((rho < 1.0).mean()),
            }

    # 2) (B) 경계: 수치 이분법 vs 2 sin(π/(4δ+2))
    for d in DELAYS_TAB:
        kn, kf = S.critical_gain_B(d), S.critical_gain_B_formula(d)
        out["B_boundary"].append({"delay": d, "numeric": kn, "formula": kf, "abs_diff": abs(kn - kf)})

    # 3) (A) ξ_max(λ, δ), ξ_min, δ=1 닫힌꼴 1+1/λ, δ=0 닫힌꼴 2/λ
    for lam in LAMS_TAB:
        row = {str(d): S.xi_max_A(lam, d) for d in DELAYS_TAB}
        row["xi_min_max_over_delays"] = max(abs(S.xi_min_A(lam, d)) for d in (0, 1, 2, 3, 5))
        row["closed_d0"] = 2.0 / lam
        row["closed_d1"] = 1.0 + 1.0 / lam
        out["A_ximax"][f"{lam:g}"] = row
    # 0 < ξ < 2 는 모든 δ 에서 안정인가 (0 < λ ≤ 1): λ 10점 × ξ ∈ [0.01, 1.99] 60점 × δ 25개의 최대 ρ
    lam_chk = np.linspace(0.1, 1.0, 10)
    xi_chk = np.linspace(0.01, 1.99, 60)
    d_chk = tuple(range(0, 21)) + (25, 30, 40, 50)
    L, X = np.meshgrid(lam_chk, xi_chk)
    worst = max(float(S.rho_batch("A", L, X, d).max()) for d in d_chk)
    # 반대로 ξ 가 2 를 넘으면 δ 를 키울 때 결국 불안정해지나: λ 별 ξ_max(δ=50)
    xm50 = [first for first in (S.first_crossing(lambda x, l=l: S.rho_batch("A", l, x, 50), 1.0, 10.0, n_scan=600)
                                for l in (0.1, 0.3, 0.5, 1.0))]
    out["A_robust"] = {"max_rho_xi_0_2": worst, "n_lam": int(lam_chk.size), "n_xi": int(xi_chk.size),
                       "delays": list(d_chk), "ximax_delay50": dict(zip(("0.1", "0.3", "0.5", "1"), xm50))}

    # 그림용 지연 곡선 (δ = 0..25)
    dd = np.arange(0, 26)
    arrays["B_curve"] = (dd, np.array([S.critical_gain_B_formula(d) for d in dd]),
                         np.array([S.critical_gain_B(d) for d in dd]))
    arrays["A_curve"] = {lam: np.array([S.xi_max_A(lam, d, hi=25.0, n_scan=500) for d in dd])
                         for lam in (0.1, 0.3, 0.5, 1.0)}

    # 4) (B) ≡ (A, 최신 레시피로 갱신) — 잡음 있는 공정에서 레시피 궤적이 같은가
    dist = make_disturbance(P, 400, (11, 12, 13))
    rB = sim.run(P, dist, CtrlSpec("B", 0.3), 2, limits=False)
    n = dist.n_seeds
    c = EWMAIntercept(0.3, P.b_hat, P.target, n)
    U = np.empty((n, 400))
    Y = np.empty((n, 400))
    up = np.full(n, P.u_nom)
    for k in range(400):
        u = c.command(up)
        U[:, k], Y[:, k] = u, dist.rr[:, k] * u + dist.e[:, k]
        if k - 2 >= 0:
            c.update(Y[:, k - 2] + dist.v[:, k - 2], u)  # 측정 웨이퍼의 레시피가 아닌 '최신' 레시피
        up = u
    rA = sim.run(P, dist, CtrlSpec("A", 0.3), 2, limits=False)
    out["equivalence"] = {"max_abs_dU_B_vs_Alatest": float(np.abs(rB.U - U).max()),
                          "max_abs_dU_B_vs_Amatched": float(np.abs(rB.U - rA.U).max())}
    return out, arrays
