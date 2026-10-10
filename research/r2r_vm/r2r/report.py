"""results.json · results.md · README.md 생성.

README.md 는 report_template.tmpl 의 이중 중괄호 자리표시를 이 파일이 만든 값·표로 채운 결과다.
보고서의 숫자는 전부 여기서 나온다 — 손으로 옮겨 적지 않는다.
본문의 정성적 주장(크다/작다, 안정/불안정)은 claims() 가 결과로 다시 확인한다. 하나라도 틀리면 실패한다.
"""
from __future__ import annotations

import json
import math
import re

import numpy as np

from . import common as C
from .process import CMP

FAM_KO = {"EWMA-A": "EWMA (A) 절편형", "EWMA-B": "EWMA (B) 속도형", "d-EWMA": "d-EWMA",
          "EWMA-A+PMreset": "EWMA (A) + PM 리셋", "d-EWMA+PMreset": "d-EWMA + PM 리셋"}
STRAT_KO = {"meas": "(i) 계측만", "meas_boost": "(i') 계측만 + PM 뒤 전수", "vm_all": "(ii) VM 전부",
            "vm_t2": "(iii) VM + T² 게이트", "vm_t2r": "(iv) VM + T² + PM 뒤 잔차 검증",
            "vm_refresh": "(v) PM 뒤 전수 + VM 재학습 + T²"}
SCEN_KO = {"normal": "정상 (PM 뒤에도 관계 유지)", "offset": "모터 기저선 이동 (T² 로 보임)",
           "silent": "같은 마찰에서 제거율 이동 (T² 로 안 보임)"}


def _pm_wafers():
    from .process import pad_schedule
    pad_idx, age = pad_schedule(C.N_WAFERS, C.P.pm_interval)
    return [int(k) for k in np.flatnonzero((age == 0) & (np.arange(C.N_WAFERS) > 0))]


# ── 서식 ───────────────────────────────────────────────────────────────────
def num(x, nd=0, sep=True):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    s = f"{x:,.{nd}f}" if sep else f"{x:.{nd}f}"
    if s.startswith("-") and float(s.replace(",", "")) == 0.0:
        s = s[1:]
    return s


def ci(c, nd=0, sep=True):
    if c is None or c.get("n", 0) == 0:
        return "—"
    if c["n"] < 2 or c["lo"] is None or not math.isfinite(c["lo"]):
        return num(c["mean"], nd, sep)
    return f"{num(c['mean'], nd, sep)} [{num(c['lo'], nd, sep)}, {num(c['hi'], nd, sep)}]"


def sci(x):
    return f"{x:.1e}"


def table(header, rows, align=None):
    align = align or ["---"] + ["---:"] * (len(header) - 1)
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join(align) + " |"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def sanitize(o):
    """JSON 으로 쓸 수 있게: numpy → 파이썬, 비유한 실수 → None, 튜플 키 → 문자열."""
    if isinstance(o, dict):
        return {str(k): sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [sanitize(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return sanitize(o.tolist())
    return o


# ── 표 ─────────────────────────────────────────────────────────────────────
def tables(r1, r2, r3):
    T = {}
    p = C.P
    # 공정·제어 가정
    T["params"] = table(
        ["항목", "값", "뜻"],
        [["목표 제거량 T", f"{p.target:,.0f} Å", "산화막 CMP, 웨이퍼 한 장 = 한 run"],
         ["명목 제거율 b0 = 모델 이득 b̂", f"{p.b0:g} Å/s ({p.b0 * 60:,.0f} Å/min)", "y = RR·u + e"],
         ["명목 연마시간 T/b̂", f"{p.u_nom:g} s", "레시피 u = 연마시간"],
         ["규격", f"T ± {p.spec:g} Å (±{100 * p.spec / p.target:g} %)", "품질은 참값 y 로 잰다"],
         ["사전 승인 레시피 범위", f"[{p.u_min:g}, {p.u_max:g}] s", f"RR {p.target / p.u_max:g}~{p.target / p.u_min:g} Å/s 까지 보상"],
         ["웨이퍼당 최대 변경 Δmax", f"{p.du_max:g} s", "R2R 한 번에 움직일 수 있는 양"],
         ["패드 마모 (선형)", f"RR 상대 −{p.wear * 1e4:g}e-4 / wafer", f"PM 주기 {p.pm_interval} 장에 −{100 * p.wear * p.pm_interval:g} %"],
         ["PM (패드 교체)", f"{p.pm_interval} 장마다", f"새 패드 RR = b0·(1+s), s ~ N(0, {p.pad_sd:g}²)"],
         ["IMA(1,1) (제거율)", f"θ = {p.ima_theta:g}, σ_a = {p.ima_sd:g} Å/s", f"u=100 s 에서 백색 {p.ima_sd * p.u_nom:g} Å, 랜덤워크 {(1 - p.ima_theta) * p.ima_sd * p.u_nom:g} Å/wafer"],
         ["하강압·정반속도 요동", f"{100 * p.p_sd:g} %, {100 * p.v_sd:g} %", "Preston RR ∝ P·V, FDC 로 보인다"],
         ["관측 불가 백색 잡음 e", f"σ = {p.e_sd:g} Å", "입고 막두께 등, VM 도 못 본다"],
         ["계측 잡음", f"σ = {p.meas_sd:g} Å", "제어기·VM 학습에만 들어간다"],
         ["계측 지연 δ (기준)", f"{C.DELAY} wafer", "웨이퍼 k−δ 의 계측이 웨이퍼 k+1 레시피 전에 도착"],
         ["run 길이", f"{num(C.N_WAFERS)} wafer",
          f"PM {len(_pm_wafers())}회 ({'·'.join(str(k) for k in _pm_wafers())}), 처음 {C.BURN} 장은 지표에서 제외"],
         ["Monte Carlo", f"평가 {len(C.EVAL_SEEDS)} seed, 튜닝 {len(C.TUNE_SEEDS)} seed (겹치지 않음)", "seed = 독립 반복 한 번"]],
        ["---", "---", "---"])

    # Q1
    T["q1_B"] = table(["δ", "수치 이분법 (λξ)*", "2·sin(π/(4δ+2))", "|차이|"],
                      [[r["delay"], f"{r['numeric']:.10f}", f"{r['formula']:.10f}", sci(r["abs_diff"])]
                       for r in r1["B_boundary"]])
    dl = ("0", "1", "2", "3", "5", "10", "25")
    from .stability import critical_gain_B_formula
    rows = []
    for lam, row in r1["A_ximax"].items():
        rows.append([f"(A) λ={lam}"] + [num(row[d], 3) for d in dl])
        rows.append([f"(B) λ={lam}"] + [num(critical_gain_B_formula(int(d)) / float(lam), 3) for d in dl])
    T["q1_ximax"] = table(["형태, λ"] + [f"δ={d}" for d in dl], rows)
    rows = []
    for key, s in r1["sim_check"].items():
        rows.append([f"({key[0]})", key[1:], s["n_points"], s["n_clear"], f"{100 * s['agree_clear']:.1f} %",
                     f"{100 * s['agree_all']:.1f} %", sci(s["max_abs_rho_err"])])
    T["q1_sim"] = table(["형태", "δ", "격자 점", "|ρ−1|>0.01 점", "일치 (명확한 점)", "일치 (전체)",
                         "max |ρ̂−ρ|"], rows)

    # Q2
    rows = []
    for fam in ("EWMA-A", "EWMA-B", "d-EWMA", "EWMA-A+PMreset", "d-EWMA+PMreset"):
        t = r2["tuned"][fam]
        par = f"λ={t['lam']:g}" if t["kind"] != "D" else f"λ1={t['lam']:g}, λ2={t['lam2']:g}"
        m = r2["margins"][fam]
        rows.append([FAM_KO[fam], par, f"{num(m['xi_lo'], 2)} < ξ < {num(m['xi_hi'], 2)}"])
    T["q2_tuned"] = table(["제어기", "튜닝 결과 (튜닝 seed, MSE 최소)", f"안정 ξ 범위 (δ={C.DELAY})"], rows)
    rows = []
    for fam in ("EWMA-A", "EWMA-B", "d-EWMA", "EWMA-A+PMreset", "d-EWMA+PMreset"):
        r = r2["main"][fam]
        rows.append([FAM_KO[fam], ci(r["mse"]), num(r["rmse_of_mean_mse"], 1), ci(r["oos_pct"], 2),
                     ci(r["recovery"], 1), ci(r["post_pm_oos"], 2),
                     ci(r["mse_diff_vs_A"]) if fam != "EWMA-A" else "기준"])
    T["q2_main"] = table(["제어기", "MSE (Å²)", "√MSE (Å)", "규격 이탈 %", "PM 회복 (wafer)",
                          f"PM 뒤 {C.POST_PM_WIN}장 이탈 수", "ΔMSE vs (A) (짝 비교)"], rows)
    rows = []
    for fam in ("EWMA-A", "EWMA-B", "d-EWMA", "EWMA-A+PMreset", "d-EWMA+PMreset"):
        r = r2["main"][fam]
        rows.append([FAM_KO[fam], ci(r["pinned_per_run"], 2), num(r["runs_pinned_pct"], 0) + " %",
                     ci(r["rate_limited_per_run"], 2)])
    T["q2_events"] = table(["제어기", "경계 포화 웨이퍼 / run", "포화가 한 번이라도 난 run", "Δmax 에 묶인 웨이퍼 / run"],
                           rows)
    rows = []
    for d in C.SENS_DELAY:
        cells = [d]
        for fam in ("EWMA-A", "EWMA-B", "d-EWMA"):
            r = [x for x in r2["sens_delay"] if x["delay"] == d and x["family"] == fam][0]
            cells += [ci(r["mse"]), ci(r["oos_pct"], 2)]
        rows.append(cells)
    T["q2_delay"] = table(["δ", "(A) MSE", "(A) 이탈 %", "(B) MSE", "(B) 이탈 %", "d-EWMA MSE", "d-EWMA 이탈 %"], rows)
    rows = []
    for w in C.SENS_WEAR:
        cells = [f"{w * 1e4:g}e-4", num(p.b0 * w * p.u_nom, 1), f"{100 * w * p.pm_interval:g} %"]
        for fam in ("EWMA-A", "d-EWMA", "EWMA-A+PMreset"):
            r = [x for x in r2["sens_wear"] if x["wear"] == w and x["family"] == fam][0]
            cells += [ci(r["mse"]), ci(r["oos_pct"], 2), num(r["runs_pinned_pct"], 0) + " %"]
        rows.append(cells)
    T["q2_wear"] = table(["마모율 w", "드리프트 Å/wafer", "패드 수명 중 RR 감소",
                          "(A) MSE", "(A) 이탈 %", "(A) 포화 run", "d-EWMA MSE", "d-EWMA 이탈 %", "d-EWMA 포화 run",
                          "(A)+리셋 MSE", "(A)+리셋 이탈 %", "(A)+리셋 포화 run"], rows)
    rows = []
    for w in C.SAT_WEAR:
        a = [x for x in r2["saturation"] if x["wear"] == w and x["family"] == "EWMA-A"][0]
        d = [x for x in r2["saturation"] if x["wear"] == w and x["family"] == "d-EWMA"][0]
        rows.append([f"{w * 1e4:g}e-4", num(a["drift_A_per_wafer"], 1),
                     ci(a["pred_n_star"]) if a["pred_n_star"] else "—",
                     num(a["runs_touch_pct"], 0) + " %", ci(a["first_touch"]), ci(a["first_sustained"]),
                     ci(d["first_touch"]), ci(a["lead_to_first_oos"], 1),
                     num(a["margin_wafers_det"], 0) if a["margin_wafers_det"] else "—",
                     ci(a["oos_pct_100_before"], 2), ci(a["oos_pct_100_after"], 1)])
    T["q2_sat"] = table(["마모율 w", "드리프트 Å/wafer", "예측 첫 포화 (결정론)", f"{num(C.SAT_N)}장 안에 포화한 run",
                         "(A) 첫 포화 웨이퍼", f"(A) 지속 포화 ({C.SAT_K}연속)", "d-EWMA 첫 포화",
                         "지속 포화 → 첫 이탈 (wafer)", "평균이 −150 Å 에 닿는 데 (결정론)",
                         "포화 전 100장 이탈 %", "포화 후 100장 이탈 %"], rows)

    # Q3
    rows = []
    for m, d in r3["calibration"].items():
        rows.append([m, num(d["t2_thr"], 1), num(d["res_rms"], 1), num(d["res_thr"], 1), num(d["vm_rmse_true"], 1),
                     num(d["y_sd_unmeasured"], 1)])
    T["q3_calib"] = table(["m", "T² 문턱 (99 %)", "VM 잔차 RMS (Å)", "잔차 검증 문턱 (Å)", "VM 오차 RMS vs 참값 (Å)",
                           "참값 y 표준편차 (Å)"], rows)
    strats = C.Q3_STRATS
    rows = []
    for s in strats:
        cells = [STRAT_KO[s]] + [num(r3["tuned_lam"].get(f"{s}|{m}"), 1) if f"{s}|{m}" in r3["tuned_lam"] else "—"
                                 for m in C.Q3_M]
        rows.append(cells)
    T["q3_lam"] = table(["전략"] + [f"m={m}" for m in C.Q3_M], rows)
    ev = r3["eval"]
    base = [r for r in ev if r["m"] == 1][0]
    for sc in ("normal", "offset", "silent"):
        rows = []
        for s in strats:
            cells = [STRAT_KO[s]]
            for m in C.Q3_M:
                rr = [r for r in ev if r["scenario"] == sc and r["m"] == m and (r["strategy"] == s or m == 1)]
                cells.append(ci(rr[0]["ratio_vs_full"], 2) if rr else "—")
            rows.append(cells)
        T[f"q3_ratio_{sc}"] = table(["전략"] + [f"m={m}" for m in C.Q3_M], rows)
        rows = []
        for s in strats:
            cells = [STRAT_KO[s]]
            for m in C.Q3_M:
                rr = [r for r in ev if r["scenario"] == sc and r["m"] == m and (r["strategy"] == s or m == 1)]
                cells.append(ci(rr[0]["oos_pct"], 2) if rr else "—")
            rows.append(cells)
        T[f"q3_oos_{sc}"] = table(["전략"] + [f"m={m}" for m in C.Q3_M], rows)
    rows = []
    for s in strats:
        rows.append([STRAT_KO[s]] + [num(100 * [r for r in ev if r["scenario"] == "normal" and r["m"] == m and
                                                 (r["strategy"] == s or m == 1)][0]["meas_frac"], 1) + " %"
                                     for m in C.Q3_M])
    T["q3_frac"] = table(["전략"] + [f"m={m}" for m in C.Q3_M], rows)
    rows = []
    for sc in ("normal", "offset", "silent"):
        for s in strats:
            e = r3["equal_quality"][f"{sc}|{s}"]
            rows.append([SCEN_KO[sc], STRAT_KO[s],
                         e["max_m_point"] if e["max_m_point"] else "—",
                         num(100 * e["meas_frac_point"], 1) + " %" if e["meas_frac_point"] else "—",
                         e["max_m_conservative"] if e["max_m_conservative"] else "—",
                         num(100 * e["meas_frac_conservative"], 1) + " %" if e["meas_frac_conservative"] else "—"])
    T["q3_eq"] = table(["시나리오", "전략", "최대 m (점추정)", "계측 비율", "최대 m (구간 상한)", "계측 비율"], rows,
                       ["---", "---", "---:", "---:", "---:", "---:"])
    rows = []
    for sc in ("normal", "offset", "silent"):
        for s in strats[1:]:
            cells = [SCEN_KO[sc], STRAT_KO[s]]
            for m in C.Q3_M[1:]:
                rr = [r for r in ev if r["scenario"] == sc and r["m"] == m and r["strategy"] == s][0]
                cells.append(num(100 * rr["vm_use_frac"], 1) + " %" if "vm_use_frac" in rr else "—")
            rows.append(cells)
    T["q3_use"] = table(["시나리오", "전략"] + [f"m={m}" for m in C.Q3_M[1:]], rows,
                        ["---", "---"] + ["---:"] * 4)
    rows = []
    for sc in ("normal", "offset", "silent"):
        for s in strats:
            cells = [SCEN_KO[sc], STRAT_KO[s]]
            for m in C.Q3_M:
                rr = [r for r in ev if r["scenario"] == sc and r["m"] == m and (r["strategy"] == s or m == 1)]
                cells.append(ci(rr[0]["post_pm_oos_pct"], 1) if rr else "—")
            rows.append(cells)
    T["q3_post"] = table(["시나리오", "전략"] + [f"m={m}" for m in C.Q3_M], rows, ["---", "---"] + ["---:"] * 5)
    rows = []
    for sc in ("normal", "offset", "silent"):
        for s in strats:
            cells = [SCEN_KO[sc], STRAT_KO[s]]
            for m in C.Q3_M:
                rr = [r for r in ev if r["scenario"] == sc and r["m"] == m and (r["strategy"] == s or m == 1)]
                cells.append(ci(rr[0]["mse"]) if rr else "—")
            rows.append(cells)
    T["q3_mse"] = table(["시나리오", "전략"] + [f"m={m}" for m in C.Q3_M], rows, ["---", "---"] + ["---:"] * 5)
    return T


# ── 본문 값 ─────────────────────────────────────────────────────────────────
def _q3(r3, sc, s, m):
    return [r for r in r3["eval"] if r["scenario"] == sc and r["m"] == m and (r["strategy"] == s or m == 1)][0]


def values(r1, r2, r3):
    V = {}
    p = C.P
    from . import q1 as Q1, vm as VMm
    # 설정에서 나오는 값 (본문에 손으로 숫자를 적지 않으려고)
    white = math.sqrt((p.ima_sd * p.u_nom) ** 2 + (p.b0 * p.u_nom) ** 2 * (p.p_sd ** 2 + p.v_sd ** 2) + p.e_sd ** 2)
    u_worn = p.target / (p.b0 * (1 - p.wear * p.pm_interval))
    V.update({
        "cfg_T": num(p.target), "cfg_b0": num(p.b0), "cfg_unom": num(p.u_nom), "cfg_spec": num(p.spec),
        "cfg_N": num(C.N_WAFERS), "cfg_pm": num(p.pm_interval), "cfg_delay": str(C.DELAY), "cfg_burn": num(C.BURN),
        "cfg_eval_n": str(len(C.EVAL_SEEDS)), "cfg_tune_n": str(len(C.TUNE_SEEDS)),
        "cfg_eval_range": f"{C.EVAL_SEEDS[0]}–{C.EVAL_SEEDS[-1]}", "cfg_tune_range": f"{C.TUNE_SEEDS[0]}–{C.TUNE_SEEDS[-1]}",
        "cfg_wear": f"{p.wear * 1e4:g}e-4", "cfg_white": num(white), "cfg_e": num(p.e_sd),
        "cfg_pm_step_pct": num(100 * p.wear * p.pm_interval), "cfg_u_worn": num(u_worn),
        "cfg_pm_step_A": num(p.b0 * p.wear * p.pm_interval * u_worn),
        "cfg_sat_n": num(C.SAT_N), "cfg_sat_k": str(C.SAT_K), "cfg_rec_run": str(C.REC_RUN),
        "cfg_post_win": str(C.POST_PM_WIN),
        "vm_forget": f"{VMm.VM_FORGET:g}", "vm_mem": num(1 / (1 - VMm.VM_FORGET)), "vm_alpha": f"{VMm.VM_ALPHA:g}",
        "vm_min": str(VMm.VM_MIN_COUNT), "vm_res_pct": num(100 * VMm.friction_rr_resolution(), 1),
        "vm_off_lo": num(100 * VMm.OFFSET_RANGE[0]), "vm_off_hi": num(100 * VMm.OFFSET_RANGE[1]),
        "vm_sil_lo": num(100 * VMm.SILENT_RANGE[0]), "vm_sil_hi": num(100 * VMm.SILENT_RANGE[1]),
        "q3_t2q": num(100 * C.T2_Q), "q3_resk": f"{C.RES_K:g}", "q3_eqtol": f"{C.EQ_TOL:.2f}",
        "q1_steps": num(Q1.SIM_STEPS), "q1_clear": f"{Q1.CLEAR:g}",
        "q1_nlam": str(r1["A_robust"]["n_lam"]), "q1_nxi": str(r1["A_robust"]["n_xi"]),
        "q1_maxd": str(max(r1["A_robust"]["delays"])),
        "cfg_npm": str(len(_pm_wafers())),
    })
    from . import q3 as Q3
    V["q3_profile_m"] = str(Q3.PROFILE_M)
    # Q1
    V["q1_agree_min"] = f"{100 * min(s['agree_clear'] for s in r1['sim_check'].values()):.0f} %"
    V["q1_n_clear"] = f"{sum(s['n_clear'] for s in r1['sim_check'].values()):,}"
    V["q1_n_points"] = f"{sum(s['n_points'] for s in r1['sim_check'].values()):,}"
    V["q1_rho_err_max"] = sci(max(s["max_abs_rho_err"] for s in r1["sim_check"].values()))
    V["q1_B_diff_max"] = sci(max(r["abs_diff"] for r in r1["B_boundary"]))
    for r in r1["B_boundary"]:
        V[f"q1_Bk_{r['delay']}"] = f"{r['numeric']:.3f}"
    for lam, row in r1["A_ximax"].items():
        for d in ("0", "1", "2", "3", "5", "10", "25"):
            V[f"q1_Ax_{lam}_{d}"] = f"{row[d]:.2f}"
    V["q1_robust_rho"] = f"{r1['A_robust']['max_rho_xi_0_2']:.5f}"
    V["q1_robust_nd"] = str(len(r1["A_robust"]["delays"]))
    V["q1_ximax50_01"] = f"{r1['A_robust']['ximax_delay50']['0.1']:.3f}"
    V["q1_ximax50_03"] = f"{r1['A_robust']['ximax_delay50']['0.3']:.3f}"
    V["q1_equiv"] = sci(r1["equivalence"]["max_abs_dU_B_vs_Alatest"])
    V["q1_equiv_diff"] = f"{r1['equivalence']['max_abs_dU_B_vs_Amatched']:.2f}"
    # Q2
    t = r2["tuned"]
    V["q2_lamA"] = f"{t['EWMA-A']['lam']:g}"
    V["q2_lamB"] = f"{t['EWMA-B']['lam']:g}"
    V["q2_l1D"] = f"{t['d-EWMA']['lam']:g}"
    V["q2_l2D"] = f"{t['d-EWMA']['lam2']:g}"
    V["q2_lamAR"] = f"{t['EWMA-A+PMreset']['lam']:g}"
    V["q2_l2grid_min"] = f"{min(C.D_L2):g}"
    V["q2_xi_lo"] = f"{r2['xi_operating']['q005']:.2f}"
    V["q2_xi_hi"] = f"{r2['xi_operating']['q995']:.2f}"
    for fam, key in (("EWMA-A", "A"), ("EWMA-B", "B"), ("d-EWMA", "D"), ("EWMA-A+PMreset", "AR"),
                     ("d-EWMA+PMreset", "DR")):
        r = r2["main"][fam]
        V[f"q2_mse_{key}"] = ci(r["mse"])
        V[f"q2_msem_{key}"] = num(r["mse"]["mean"])
        V[f"q2_rmse_{key}"] = num(r["rmse_of_mean_mse"], 1)
        V[f"q2_oos_{key}"] = ci(r["oos_pct"], 2)
        V[f"q2_rec_{key}"] = ci(r["recovery"], 1)
        V[f"q2_post_{key}"] = ci(r["post_pm_oos"], 2)
        V[f"q2_dA_{key}"] = ci(r["mse_diff_vs_A"])
        V[f"q2_pinrun_{key}"] = num(r["runs_pinned_pct"], 0) + " %"
        V[f"q2_pin_{key}"] = ci(r["pinned_per_run"], 2)
    V["q2_reset_gain_pct"] = num(100 * (1 - r2["main"]["EWMA-A+PMreset"]["mse"]["mean"] /
                                       r2["main"]["EWMA-A"]["mse"]["mean"]), 0) + " %"
    V["q2_dAR_DR"] = ci(r2["main"]["d-EWMA+PMreset"]["mse_diff_vs_AR"])
    V["q2_pmstep_A"] = ci(r2["pm_step_err"]["EWMA-A"])
    V["q2_pin_start_age"] = num(r2["main_pinned_detail"]["min_start_age"])
    V["q2_pin_runs"] = str(r2["main_pinned_detail"]["n_runs"])
    V["q2_pmstep_AR"] = ci(r2["pm_step_err"]["EWMA-A+PMreset"])
    sd = {(x["delay"], x["family"]): x for x in r2["sens_delay"]}
    for d in C.SENS_DELAY:
        for fam, key in (("EWMA-A", "A"), ("EWMA-B", "B"), ("d-EWMA", "D")):
            V[f"q2_sd_{key}_{d}"] = ci(sd[(d, fam)]["mse"])
            V[f"q2_sdo_{key}_{d}"] = ci(sd[(d, fam)]["oos_pct"], 2)
    from .stability import critical_gain_B_formula
    lamB = t["EWMA-B"]["lam"]
    V["q2_B_K5"] = f"{critical_gain_B_formula(5):.3f}"
    V["q2_B_K10"] = f"{critical_gain_B_formula(10):.3f}"
    V["q2_B_lxi_lo"] = f"{lamB * r2['xi_operating']['q005']:.2f}"
    V["q2_B_lxi_hi"] = f"{lamB * r2['xi_operating']['q995']:.2f}"
    sw = {(x["wear"], x["family"]): x for x in r2["sens_wear"]}
    for w in C.SENS_WEAR:
        k = f"{w * 1e4:g}"
        V[f"q2_sw_pin_A_{k}"] = num(sw[(w, "EWMA-A")]["runs_pinned_pct"], 0) + " %"
        V[f"q2_sw_oos_A_{k}"] = ci(sw[(w, "EWMA-A")]["oos_pct"], 2)
        V[f"q2_sw_mse_A_{k}"] = ci(sw[(w, "EWMA-A")]["mse"])
        V[f"q2_sw_mse_D_{k}"] = ci(sw[(w, "d-EWMA")]["mse"])
    sat = {(x["wear"], x["family"]): x for x in r2["saturation"]}
    for w in C.SAT_WEAR:
        k = f"{w * 1e4:g}"
        a = sat[(w, "EWMA-A")]
        V[f"q2_sat_touchpct_{k}"] = num(a["runs_touch_pct"], 0) + " %"
        V[f"q2_sat_touch_{k}"] = ci(a["first_touch"])
        V[f"q2_sat_pred_{k}"] = ci(a["pred_n_star"]) if a["pred_n_star"] else "—"
        V[f"q2_sat_lead_{k}"] = ci(a["lead_to_first_oos"], 1)
        V[f"q2_sat_after_{k}"] = ci(a["oos_pct_100_after"], 1)
        V[f"q2_sat_before_{k}"] = ci(a["oos_pct_100_before"], 2)
        V[f"q2_sat_det_{k}"] = num(a["margin_wafers_det"], 0) if a["margin_wafers_det"] else "—"
        V[f"q2_sat_touchD_{k}"] = ci(sat[(w, "d-EWMA")]["first_touch"])
    V["q2_offset_formula_2e4"] = num(p.b0 * 2e-4 * p.u_nom * (C.DELAY + 1 / t["EWMA-A"]["lam"]), 1)
    V["q2_drift_2e4"] = num(p.b0 * 2e-4 * p.u_nom, 1)
    # Q3
    cal = r3["calibration"]
    V["q3_vm_rmse_lo"] = num(min(d["vm_rmse_true"] for d in cal.values()), 1)
    V["q3_vm_rmse_hi"] = num(max(d["vm_rmse_true"] for d in cal.values()), 1)
    V["q3_res_thr_lo"] = num(min(d["res_thr"] for d in cal.values()), 0)
    V["q3_res_thr_hi"] = num(max(d["res_thr"] for d in cal.values()), 0)
    V["q3_base_mse"] = ci(_q3(r3, "normal", "meas", 1)["mse"])
    for sc in ("normal", "offset", "silent"):
        for s in C.Q3_STRATS:
            for m in C.Q3_M[1:]:
                r = _q3(r3, sc, s, m)
                V[f"q3_r_{sc}_{s}_{m}"] = ci(r["ratio_vs_full"], 2)
                V[f"q3_rm_{sc}_{s}_{m}"] = num(r["ratio_vs_full"]["mean"], 2)
                V[f"q3_oos_{sc}_{s}_{m}"] = ci(r["oos_pct"], 2)
                V[f"q3_frac_{sc}_{s}_{m}"] = num(100 * r["meas_frac"], 1) + " %"
                if "vm_use_frac" in r:
                    V[f"q3_use_{sc}_{s}_{m}"] = num(100 * r["vm_use_frac"], 0) + " %"
            e = r3["equal_quality"][f"{sc}|{s}"]
            V[f"q3_eq_{sc}_{s}"] = str(e["max_m_point"]) if e["max_m_point"] else "—"
            V[f"q3_eqf_{sc}_{s}"] = num(100 * e["meas_frac_point"], 1) + " %" if e["meas_frac_point"] else "—"
            V[f"q3_eqc_{sc}_{s}"] = str(e["max_m_conservative"]) if e["max_m_conservative"] else "—"
            V[f"q3_eqcf_{sc}_{s}"] = (num(100 * e["meas_frac_conservative"], 1) + " %"
                                      if e["meas_frac_conservative"] else "—")
    V["q3_boost"] = str(C.K_BOOST)
    V["q3_oos_base"] = ci(_q3(r3, "normal", "meas", 1)["oos_pct"], 2)
    return V


# ── 본문 주장 확인 ──────────────────────────────────────────────────────────
def claims(r1, r2, r3):
    """README 본문이 말하는 방향·순서가 결과와 맞는지. 틀리면 AssertionError."""
    sc = r1["sim_check"]
    out = []

    def check(cond, msg):
        out.append((bool(cond), msg))

    check(all(s["agree_clear"] == 1.0 for s in sc.values()), "Q1: 명확한 점 전부 일치")
    check(max(r["abs_diff"] for r in r1["B_boundary"]) < 1e-9, "Q1: (B) 경계식 일치")
    check(r1["A_robust"]["max_rho_xi_0_2"] < 1.0, "Q1: (A) 0<ξ<2 에서 모든 δ 안정")
    check(r1["equivalence"]["max_abs_dU_B_vs_Alatest"] < 1e-9, "Q1: (B) ≡ (A, 최신 레시피)")
    m = r2["main"]
    check(m["d-EWMA"]["mse_diff_vs_A"]["lo"] > 0, "Q2: 리셋 없이 d-EWMA MSE 가 (A) 보다 (짝 비교로) 크다")
    check(m["d-EWMA+PMreset"]["mse_diff_vs_AR"]["hi"] < 0, "Q2: PM 리셋과 함께면 d-EWMA MSE 가 (A) 보다 작다")
    check(abs(m["d-EWMA"]["mse_diff_vs_A"]["mean"]) < 0.02 * m["EWMA-A"]["mse"]["mean"]
          and abs(m["d-EWMA+PMreset"]["mse_diff_vs_AR"]["mean"]) < 0.02 * m["EWMA-A+PMreset"]["mse"]["mean"],
          "Q2: d-EWMA 득실은 MSE 의 2 % 미만")
    check(m["d-EWMA"]["recovery"]["mean"] >= m["EWMA-A"]["recovery"]["lo"] - 0.5,
          "Q2: d-EWMA 가 PM 회복을 빠르게 하지 않는다")
    check(m["EWMA-B"]["mse_diff_vs_A"]["lo"] > 0, "Q2: (B) MSE 가 (A) 보다 크다")
    check(m["EWMA-A+PMreset"]["mse_diff_vs_A"]["hi"] < 0, "Q2: PM 리셋이 MSE 를 줄인다")
    check(r2["tuned"]["d-EWMA"]["lam2"] == min(C.D_L2), "Q2: d-EWMA 튜닝이 λ2 격자 최솟값으로 간다")
    check(m["EWMA-B"]["recovery"]["mean"] > m["EWMA-A"]["recovery"]["mean"], "Q2: (B) 가 PM 회복이 더 느리다")
    sd = {(x["delay"], x["family"]): x for x in r2["sens_delay"]}
    check(sd[(10, "EWMA-B")]["mse"]["mean"] > 20 * sd[(10, "EWMA-A")]["mse"]["mean"], "Q2: δ=10 에서 (B) 발산")
    check(sd[(5, "EWMA-B")]["mse"]["mean"] > 2 * sd[(5, "EWMA-A")]["mse"]["mean"], "Q2: δ=5 에서 (B) 크게 나빠짐")
    lamB = r2["tuned"]["EWMA-B"]["lam"]
    from .stability import critical_gain_B_formula
    check(lamB * r2["xi_operating"]["q995"] < critical_gain_B_formula(5), "Q2: δ=5 에서 (B) 는 아직 안정 범위")
    check(lamB * r2["xi_operating"]["q005"] > critical_gain_B_formula(10), "Q2: δ=10 에서 (B) 는 불안정 범위")
    sat = {(x["wear"], x["family"]): x for x in r2["saturation"]}
    check(sat[(0.0, "EWMA-A")]["runs_touch_pct"] == 0.0, "Q2: 마모 0 이면 1500장 안에 포화 없음")
    for w in (2e-4, 3e-4, 4e-4, 6e-4, 8e-4):
        a = sat[(w, "EWMA-A")]
        check(a["runs_touch_pct"] == 100.0, f"Q2: w={w} 전 run 포화")
        check(a["first_touch"]["lo"] <= a["pred_n_star"]["hi"] and a["pred_n_star"]["lo"] <= a["first_touch"]["hi"],
              f"Q2: w={w} 첫 포화가 결정론 예측과 구간이 겹친다")
        check(a["lead_to_first_oos"]["mean"] < a["margin_wafers_det"], f"Q2: w={w} 첫 이탈이 평균 교차보다 빠르다")
    q = lambda s, st, mm: _q3(r3, s, st, mm)  # noqa: E731
    check(q("normal", "vm_all", 25)["ratio_vs_full"]["hi"] <= C.EQ_TOL, "Q3: 정상에서 VM 전부 m=25 동등")
    check(q("normal", "meas", 5)["ratio_vs_full"]["lo"] > C.EQ_TOL, "Q3: 계측만은 m=5 에서 이미 동등 아님")
    check(q("silent", "vm_all", 25)["ratio_vs_full"]["lo"] > q("silent", "meas", 25)["ratio_vs_full"]["hi"],
          "Q3: silent 에서 VM 전부가 계측만보다 나쁘다 (m=25)")
    check(abs(q("silent", "vm_t2", 25)["ratio_vs_full"]["mean"] - q("silent", "vm_all", 25)["ratio_vs_full"]["mean"])
          < 0.1, "Q3: silent 에서 T² 게이트는 효과가 거의 없다")
    check(q("silent", "vm_t2r", 25)["ratio_vs_full"]["hi"] < q("silent", "vm_all", 25)["ratio_vs_full"]["lo"],
          "Q3: silent 에서 잔차 검증이 피해를 줄인다")
    check(q("silent", "vm_t2r", 25)["ratio_vs_full"]["lo"] > q("silent", "meas", 25)["ratio_vs_full"]["hi"],
          "Q3: 그래도 잔차 검증은 계측만보다 나쁘다 (m=25)")
    check(q("normal", "vm_t2", 25)["ratio_vs_full"]["lo"] > q("normal", "vm_all", 25)["ratio_vs_full"]["hi"],
          "Q3: 정상에서 T² 게이트는 손해")
    for s_ in ("normal", "offset", "silent"):
        check(abs(q(s_, "vm_refresh", 10)["ratio_vs_full"]["mean"] - q("normal", "vm_refresh", 10)["ratio_vs_full"]["mean"])
              < 1e-9, "Q3: 재학습 전략은 세 시나리오에서 같다")
    e = r3["equal_quality"]
    check(e["silent|vm_refresh"]["max_m_point"] == 10 and e["silent|meas"]["max_m_point"] == 2,
          "Q3: silent 동등 품질 m — 재학습 10, 계측만 2")
    check(q("normal", "vm_refresh", 10)["ratio_vs_full"]["mean"] > q("normal", "vm_all", 10)["ratio_vs_full"]["mean"],
          "Q3: 정상에서는 재학습이 VM 전부보다 약간 손해")
    pd = r2["main_pinned_detail"]
    check(pd["min_start_age"] is not None and pd["min_start_age"] >= 0.6 * C.P.pm_interval,
          "Q2: 기준 시나리오에서 run 별 첫 포화는 패드 수명 60 % 이후")
    check(pd["b_pad_at_start_mean"] is not None and pd["b_pad_at_start_mean"] < pd["b_pad_all_mean"],
          "Q2: 포화한 패드는 평균보다 제거율이 낮게 시작했다")
    check(pd["ima_at_start_max"] is not None and pd["ima_at_start_max"] < 0, "Q2: 포화 시작 때 IMA 는 모두 하강 쪽")
    check(sd[(10, "EWMA-A")]["mse"]["mean"] < 2.5 * sd[(2, "EWMA-A")]["mse"]["mean"]
          and sd[(10, "d-EWMA")]["mse"]["mean"] < 2.5 * sd[(2, "d-EWMA")]["mse"]["mean"],
          "Q2: (A)·d-EWMA 는 δ=10 에서도 완만하게 나빠진다")
    check(all(sd[(d2, f)]["mse"]["mean"] < sd[(d1, f)]["mse"]["mean"] for f in ("EWMA-A", "d-EWMA")
              for d1, d2 in zip(C.SENS_DELAY[1:], C.SENS_DELAY[:-1])), "Q2: 지연이 늘면 MSE 가 는다")
    check(abs(m["d-EWMA"]["recovery"]["mean"] - m["EWMA-A"]["recovery"]["mean"]) < 0.5,
          "Q2: d-EWMA 는 PM 회복에서도 이득이 없다")
    for w in (2e-4, 3e-4, 4e-4, 6e-4, 8e-4):
        check(abs(sat[(w, "d-EWMA")]["first_touch"]["mean"] - sat[(w, "EWMA-A")]["first_touch"]["mean"]) < 20,
              f"Q2: w={w} d-EWMA 도 거의 같은 때 포화")
        check(sat[(w, "EWMA-A")]["oos_pct_100_after"]["lo"] > sat[(w, "EWMA-A")]["oos_pct_100_before"]["hi"],
              f"Q2: w={w} 포화 뒤 이탈이 는다")
    check(sat[(1e-4, "EWMA-A")]["pred_n_star"]["lo"] > C.SAT_N, "Q2: w=1e-4 예측 첫 포화는 run 밖")
    check(abs(q("offset", "vm_t2", 25)["ratio_vs_full"]["mean"] - q("offset", "vm_all", 25)["ratio_vs_full"]["mean"])
          < 0.1, "Q3: offset 에서 T² 게이트의 이득이 작다")
    check(len({e[f"{s_}|vm_refresh"]["max_m_point"] for s_ in ("normal", "offset", "silent")}) == 1,
          "Q3: 재학습 전략의 동등 품질 m 은 세 시나리오가 같다")
    check(max(d["vm_rmse_true"] for d in r3["calibration"].values()) < 2 * C.P.e_sd,
          "Q3: VM 오차 RMS 는 관측 불가 잡음의 2배 미만")
    bad = [msg for ok, msg in out if not ok]
    if bad:
        raise AssertionError("README 주장과 결과가 어긋난다:\n  " + "\n  ".join(bad))
    return [msg for _, msg in out]


def render(template: str, V: dict, T: dict) -> str:
    def rep(mo):
        key = mo.group(1).strip()
        if key.startswith("table:"):
            name = key[6:]
            if name not in T:
                raise KeyError(f"표 없음: {name}")
            return T[name]
        if key not in V:
            raise KeyError(f"값 없음: {key}")
        return V[key]
    return re.sub(r"\{\{([^{}]+)\}\}", rep, template)


def results_md(T, meta, checks):
    parts = ["# 결과 (run_all.py 가 생성)", "",
             f"생성: run_all.py · 전체 실행 {meta['runtime_s']:.0f} s (이 줄만 실행마다 다르다)", "",
             "## 공정·제어 가정", "", T["params"], "",
             "## Q1 안정성", "", "### (B) 경계: 수치 vs 닫힌꼴", "", T["q1_B"], "",
             "### 안정 ξ 상한 (A: 수치, B: 2sin(π/(4δ+2))/λ)", "", T["q1_ximax"], "",
             "### 특성다항식 vs 폐루프 시뮬레이션", "", T["q1_sim"], "",
             "## Q2 외란 성능·포화", "", "### 튜닝", "", T["q2_tuned"], "",
             "### 기준 시나리오 (평가 seed)", "", T["q2_main"], "", T["q2_events"], "",
             "### 지연 민감도 (δ=2 튜닝값 고정)", "", T["q2_delay"], "",
             "### 마모율 민감도 (PM 500장)", "", T["q2_wear"], "",
             "### 포화 연구 (PM 없음, 1500장)", "", T["q2_sat"], "",
             "## Q3 표본 계측 + VM", "", "### 보정 (튜닝 seed, 정상 시나리오)", "", T["q3_calib"], "",
             "### 튜닝된 λ", "", T["q3_lam"], "",
             "### 계측 비율 (번인 제외)", "", T["q3_frac"], ""]
    for sc in ("normal", "offset", "silent"):
        parts += [f"### MSE 비 (전수 계측 m=1 대비) — {SCEN_KO[sc]}", "", T[f"q3_ratio_{sc}"], "",
                  f"### 규격 이탈 % — {SCEN_KO[sc]}", "", T[f"q3_oos_{sc}"], ""]
    parts += ["### 동등 품질 (MSE 비 ≤ 1.10)", "", T["q3_eq"], "",
              "### 비계측 웨이퍼 중 VM 이 쓰인 비율", "", T["q3_use"], "",
              "### PM 뒤 100장 규격 이탈 %", "", T["q3_post"], "",
              "### MSE (Å²)", "", T["q3_mse"], "",
              "## 본문 주장 자동 확인", ""] + [f"- [x] {c}" for c in checks] + [""]
    return "\n".join(parts)


def write_all(root, r1, r2, r3, meta, template_path):
    import os
    T = tables(r1, r2, r3)
    V = values(r1, r2, r3)
    checks = claims(r1, r2, r3)
    with open(os.path.join(root, "results.json"), "w", encoding="utf-8") as f:
        json.dump(sanitize({"meta": meta, "q1": r1, "q2": r2, "q3": r3}), f, ensure_ascii=False, indent=1)
    with open(os.path.join(root, "results.md"), "w", encoding="utf-8") as f:
        f.write(results_md(T, meta, checks))
    with open(template_path, encoding="utf-8") as f:
        tpl = f.read()
    with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as f:
        f.write(render(tpl, V, T))
    return checks
