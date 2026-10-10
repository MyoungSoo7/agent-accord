#!/usr/bin/env python3
"""CMP 장비 한 대의 패드 수명을 웨이퍼 단위로 돌린다 — R2R 제어, 상한 고정 감지, 레시피 변경 제안.

연구 1 (ACCORD × 팹 레시피 변경 승인) 의 시뮬레이터다. accord.py 와 같은 원칙으로 표준
라이브러리만 쓴다. 수치는 전부 가정이고 README 의 '모델·가정' 표에 그대로 옮겼다.

에이전트가 하는 일은 셋뿐이다: 감지, 제안서 작성, 제안서가 계약을 통과하는지 스스로 확인.
변경이 *적용되는* 시점은 사람 승인이 끝난 뒤다 (자율 정책은 비교용 기준선일 뿐이다).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import accord  # noqa: E402

# ---------------------------------------------------------------- 가정
TARGET, LSL, USL = 5000.0, 4850.0, 5150.0   # 제거량 목표·규격 (Å)
SIGMA_PROC, SIGMA_MET = 30.0, 15.0          # 웨이퍼마다의 공정 잡음, 계측 잡음 (Å)
RATE0_MEAN, RATE0_SD = 50.0, 1.0            # 새 패드의 제거율 (Å/s)
WEAR_MEDIAN, WEAR_SIGMA = 0.005, 0.35       # 웨이퍼당 제거율 감소 (Å/s) — 패드마다 로그정규
PAD_LIFE = 1400                             # 패드 교체(PM) 주기 (웨이퍼)
WPH = 30                                    # 시간당 웨이퍼
T_MIN, T_MAX0 = 90.0, 115.0                 # 판본 r41 이 승인한 R2R 연마 시간 범위 (s)
MAX_STEP = 10.0                             # 한 번의 변경으로 움직일 수 있는 폭 (s)
LAMBDA = 0.3                                # R2R EWMA 가중치
PIN_RUN = 5                                 # 반응형: 상한 고정이 이만큼 이어지면 제안
FIT_WINDOW = 150                            # 예측형: 추세 적합에 쓰는 최근 웨이퍼 수
SPC_LAMBDA, SPC_L = 0.2, 3.0                # 사람 SPC: EWMA 관리도
LAT_SIGMA = 0.5                             # 승인 지연의 로그정규 σ
DRAFT_MEDIAN_H = 2.0                        # 사람이 직접 변경안을 쓰는 시간 중앙값 (h)
BUFFER_WAFERS = WPH                         # 예측형 지평에 더하는 여유 (1시간)

TOOL, RECIPE_ID, BASE_REV = "CMP-03", "OX_CMP_STD", "r41"
WINDOW_PATH = os.path.join(HERE, "windows", "CMP-03_OX_CMP_STD.json")
WINDOW_REF = "research/fab_recipe/windows/CMP-03_OX_CMP_STD.json"
PARAM = "polish_time.max"

POLICIES = ("none", "spc", "reactive", "predictive", "autonomous")
POLICY_KO = {
    "none": "아무도 안 바꿈 (PM 까지 대기)",
    "spc": "사람이 SPC 로 발견 → 직접 작성 → 승인",
    "reactive": "에이전트 반응형 (상한 고정 5매) → 승인",
    "predictive": "에이전트 예측형 (지평 = 승인 지연 P90 + 1h) → 승인",
    "autonomous": "자율 (승인 없이 즉시 적용, 비교용)",
}


def load_window():
    with open(WINDOW_PATH, "rb") as fh:
        raw = fh.read()
    doc = json.loads(raw.decode("utf-8"))
    return doc, hashlib.sha256(raw).hexdigest()


WINDOW_DOC, WINDOW_SHA = load_window()
WIN_LO = float(WINDOW_DOC["params"][PARAM]["lo"])
WIN_HI = float(WINDOW_DOC["params"][PARAM]["hi"])


# ---------------------------------------------------------------- 무작위 요소
def pad_draws(seed, i, rw_sigma=0.0):
    """패드 i 의 무작위 요소. 정책이 달라도 같은 패드는 같은 값을 쓴다(공통 난수).

    rw_sigma: 제거율에 섞는 랜덤워크의 웨이퍼당 표준편차 (Å/s). 0 이면 마모가 정확히 직선이다.
    """
    r = random.Random(f"{seed}:pad:{i}")
    rate0 = r.gauss(RATE0_MEAN, RATE0_SD)
    wear = WEAR_MEDIAN * math.exp(r.gauss(0.0, WEAR_SIGMA))
    proc = [r.gauss(0.0, SIGMA_PROC) for _ in range(PAD_LIFE)]
    met = [r.gauss(0.0, SIGMA_MET) for _ in range(PAD_LIFE)]
    # PM 뒤 qual 웨이퍼 3매(100 s)로 잡은 첫 제거율 추정
    qual = sum(rate0 * 100 + r.gauss(0.0, SIGMA_PROC) + r.gauss(0.0, SIGMA_MET)
               for _ in range(3)) / 3 / 100
    w = random.Random(f"{seed}:rw:{i}")
    rw, acc = [], 0.0
    for _ in range(PAD_LIFE):
        rw.append(acc)
        acc += w.gauss(0.0, rw_sigma) if rw_sigma else 0.0
    h = random.Random(f"{seed}:human:{i}")
    lat = [h.gauss(0.0, 1.0) for _ in range(32)]     # 승인 지연용 표준정규
    draft = [h.gauss(0.0, 1.0) for _ in range(32)]   # 사람 작성 시간용 표준정규
    return {"rate0": rate0, "wear": wear, "proc": proc, "met": met, "qual": qual, "rw": rw,
            "lat": lat, "draft": draft}


def lognormal_wafers(median_h, z):
    return max(1, int(round(median_h * math.exp(LAT_SIGMA * z) * WPH)))


def latency_q90_wafers(median_h):
    return median_h * math.exp(LAT_SIGMA * 1.2815515655446004) * WPH


# ---------------------------------------------------------------- 제안서
# 폐루프(R2R 이 도는 상태)에서 계측값의 표준편차. EWMA 가 잡음을 되먹이므로 개루프보다 크다:
# σ_cl² = (σ_p² + σ_m²)(1 + λ/(2−λ)). closed_loop_capability() 의 시뮬레이션과 맞는지 시험한다.
SIGMA_CL = math.sqrt((SIGMA_PROC ** 2 + SIGMA_MET ** 2) * (1 + LAMBDA / (2 - LAMBDA)))
QUAL_SD_MAX = 2 * SIGMA_CL     # qual 산포 상한: 폐루프 σ 의 2배
CPK_MONITOR_N = 200            # 검토 체크리스트: Cpk 를 이보다 적은 매수로 판정하는 규칙은 쟁점


def draft_payload(base_rev, t_from, t_to, evidence, applies_to=None, wiwnu=False,
                  stat_fix=False):
    """에이전트가 쓰는 recipe_change 본문. 공정 창 값은 창 파일에서 읽어 그대로 싣는다.

    stat_fix=False 는 첫 초안이다: qual 5매로 평균과 Cpk 를 같이 판정한다.
    stat_fix=True 는 쟁점을 받은 뒤다: qual 은 평균·산포로, Cpk 는 누적 감시(rollback 조건)로.
    """
    p = {
        "tool": TOOL,
        "recipe": {"id": RECIPE_ID, "base_rev": base_rev},
        "kind": "r2r_bounds",
        "summary": f"패드 마모로 R2R 연마 시간이 승인된 상한 {fmt(t_from)} s 에 닿았다 — "
                   f"상한을 {fmt(t_to)} s 로 넓힌다",
        "trigger": {"source": "r2r", "signal": f"연마 시간 상한 {fmt(t_from)} s 고정 또는 고정 예측",
                    "evidence": list(evidence)},
        "changes": [{"param": PARAM, "unit": "s", "from": t_from, "to": t_to,
                     "window": {"lo": WIN_LO, "hi": WIN_HI}, "max_step": MAX_STEP}],
        "window_ref": {"kind": "path", "value": WINDOW_REF, "sha256": WINDOW_SHA},
        "risk": "medium",
        "rollback": {"to_rev": base_rev, "when": "검증 미달, 또는 적용 뒤 SPC 이탈 1회"},
        "verification": [{"metric": "removal_A", "rule": "평균 5000±50 Å, Cpk ≥ 1.33",
                          "n_wafers": 5}],
    }
    if stat_fix:
        p["verification"] = [
            {"metric": "removal_A", "rule": "qual 평균 5000±50 Å", "n_wafers": 5},
            {"metric": "removal_A", "rule": f"qual 표준편차 ≤ {QUAL_SD_MAX:.0f} Å (기준 σ 의 2배)",
             "n_wafers": 5}]
        p["rollback"]["when"] = "qual 미달, 또는 적용 뒤 SPC(EWMA 관리도) 이탈 1회"
    if applies_to:
        p["applies_to"] = applies_to
    if wiwnu:
        p["verification"].append({"metric": "wiwnu_pct", "rule": "웨이퍼 내 불균일도 ≤ 4%",
                                  "n_wafers": 5})
    return p


def fmt(x):
    return f"{x:g}"


def next_max(t_max):
    """창 안에서, 한 번에 움직일 수 있는 폭만큼만 올린다. 창을 다 썼으면 None."""
    if t_max >= WIN_HI:
        return None
    return min(WIN_HI, t_max + MAX_STEP)


# ---------------------------------------------------------------- 추세 예측
def forecast_saturation(hist, t_max):
    """최근 (n, b̂) 로 직선을 맞춰 b̂ 가 TARGET/t_max 아래로 내려갈 웨이퍼 번호를 낸다."""
    return forecast_from_sums(len(hist), sum(n for n, _ in hist), sum(b for _, b in hist),
                              sum(n * n for n, _ in hist), sum(n * b for n, b in hist), t_max)


def forecast_from_sums(m, sx, sy, sxx, sxy, t_max):
    den = m * sxx - sx * sx
    if den == 0:
        return None
    slope = (m * sxy - sx * sy) / den
    if slope >= 0:
        return None
    icpt = (sy - slope * sx) / m
    b_sat = TARGET / t_max
    return (b_sat - icpt) / slope


# ---------------------------------------------------------------- 패드 한 장
def run_pad(d, policy, lat_median_h, bias_from=None, bias=0.0, review_catches_bias=False,
            check_contract=True, trace=None):
    """패드 하나를 PAD_LIFE 웨이퍼 동안 돌린다.

    bias_from/bias: 그 웨이퍼부터 계측이 bias 만큼 어긋난다 (사례 연구용).
    review_catches_bias: 사람 검토가 기준 계측으로 어긋남을 찾아 반려하고 계측을 고친다고 가정.
    """
    b_hat = d["qual"]
    t_max = T_MAX0
    rev_no = 41
    pinned = 0
    pending = None            # {"at": 적용 웨이퍼, "to": 새 상한, "proposed_at": n}
    raised = []               # 적용된 상한 변경: {"old": 이전 상한, "used": bool}
    z = TARGET
    lcl = TARGET - SPC_L * SIGMA_CL * math.sqrt(SPC_LAMBDA / (2 - SPC_LAMBDA))
    spc_armed = True
    hist = []                 # 최근 FIT_WINDOW 개의 (n, b̂) — 합은 아래 넷으로 따로 굴린다
    sx = sy = sxx = sxy = 0.0
    k = 0                     # 사람 지연 난수 소비 위치
    horizon = latency_q90_wafers(lat_median_h) + BUFFER_WAFERS
    bias_live = bias_from is not None
    out = {"oos": 0, "proposals": 0, "applied": 0, "unused": 0, "dropped_at_pm": 0,
           "exhausted": 0, "refused_by_contract": 0, "first_sat": None, "first_prop": None,
           "rejected_in_review": 0}

    for n in range(PAD_LIFE):
        rate = d["rate0"] - d["wear"] * n + d["rw"][n]
        if pending and n >= pending["at"]:
            if pending.get("reject"):
                # 사람 검토가 계측 어긋남을 찾았다: 변경은 반려, 계측은 바로잡는다
                out["rejected_in_review"] += 1
                bias_live = False
            else:
                raised.append({"old": t_max, "used": False})
                t_max = pending["to"]
                rev_no += 1
                out["applied"] += 1
            pending = None
            spc_armed = False
        t_req = TARGET / b_hat
        t = min(max(t_req, T_MIN), t_max)
        if raised and t > raised[-1]["old"] + 1e-9:
            raised[-1]["used"] = True
        y = rate * t + d["proc"][n]
        if not LSL <= y <= USL:
            out["oos"] += 1
        y_meas = y + d["met"][n] + (bias if bias_live and n >= bias_from else 0.0)
        b_hat = LAMBDA * (y_meas / t) + (1 - LAMBDA) * b_hat
        pinned = pinned + 1 if t_req > t_max else 0
        if pinned and out["first_sat"] is None:
            out["first_sat"] = n
        z = SPC_LAMBDA * y_meas + (1 - SPC_LAMBDA) * z
        if z >= lcl:
            spc_armed = True
        hist.append((n, b_hat))
        sx, sy, sxx, sxy = sx + n, sy + b_hat, sxx + n * n, sxy + n * b_hat
        if len(hist) > FIT_WINDOW:
            n0, b0 = hist.pop(0)
            sx, sy, sxx, sxy = sx - n0, sy - b0, sxx - n0 * n0, sxy - n0 * b0
        if trace is not None:
            trace.append({"n": n, "rate": rate, "t": t, "t_req": t_req, "t_max": t_max,
                          "y": y, "y_meas": y_meas, "b_hat": b_hat, "z": z})

        if pending or policy == "none":
            continue
        fire = False
        delay = 0
        if policy == "spc":
            # 사람은 관리도 이탈을 보고, 원인이 R2R 상한이라는 걸 확인한 뒤에야 움직인다
            if spc_armed and z < lcl and pinned:
                fire = True
                delay = (lognormal_wafers(DRAFT_MEDIAN_H, d["draft"][k % 32])
                         + lognormal_wafers(lat_median_h, d["lat"][k % 32]))
        elif policy in ("reactive", "autonomous"):
            if pinned >= PIN_RUN:
                fire = True
                delay = 1 if policy == "autonomous" else lognormal_wafers(lat_median_h, d["lat"][k % 32])
        elif policy == "predictive":
            if pinned >= PIN_RUN:
                fire = True          # 예측이 빗나갔으면 반응형으로 떨어진다
            elif len(hist) == FIT_WINDOW:
                n_sat = forecast_from_sums(FIT_WINDOW, sx, sy, sxx, sxy, t_max)
                if n_sat is not None and n_sat - n <= horizon and n_sat < PAD_LIFE:
                    fire = True
            if fire:
                delay = lognormal_wafers(lat_median_h, d["lat"][k % 32])
        else:
            raise ValueError(policy)
        if not fire:
            continue
        new = next_max(t_max)
        if new is None:
            if not out["exhausted"]:
                out["exhausted"] = 1          # 창을 다 썼다 — 레시피로는 더 못 한다 (PM 을 당겨야 한다)
            continue
        if check_contract:
            evidence = [f"W{n + 1} 기준 연마 시간 요구 {t_req:.1f} s, 상한 {fmt(t_max)} s",
                        f"추정 제거율 {b_hat:.2f} Å/s"]
            payload = draft_payload(f"r{rev_no}", t_max, new, evidence,
                                    applies_to="다음 패드 교체(PM)까지. PM 때 r41 로 복귀")
            if accord.payload_errors(payload, "recipe_change"):
                out["refused_by_contract"] += 1
                continue
        k += 1
        out["proposals"] += 1
        if out["first_prop"] is None:
            out["first_prop"] = n
        pending = {"at": n + delay, "to": new, "proposed_at": n,
                   "reject": review_catches_bias and bias_live and policy != "autonomous"}

    if pending and not pending.get("reject"):
        out["dropped_at_pm"] += 1
    out["unused"] = sum(1 for r in raised if not r["used"])
    return out


# ---------------------------------------------------------------- 몬테카를로
def mean_ci(xs):
    m = statistics.fmean(xs)
    if len(xs) < 2:
        return m, 0.0
    return m, 1.96 * statistics.stdev(xs) / math.sqrt(len(xs))


def sweep(seed=20261010, n_pads=400, latencies=(0.5, 2.0, 4.0, 8.0), rw_sigma=0.0):
    pads = [pad_draws(seed, i, rw_sigma) for i in range(n_pads)]
    # 반사실 기준: 아무도 안 바꿨을 때 각 패드가 처음 상한에 닿는 웨이퍼 (닿지 않으면 None)
    base = [run_pad(d, "none", latencies[0], check_contract=False) for d in pads]
    sat0 = [r["first_sat"] for r in base]
    rows = []
    for lat in latencies:
        for pol in POLICIES:
            if pol in ("none", "autonomous") and lat != latencies[0]:
                continue        # 승인 지연과 무관하다 — 한 번만 돈다
            res = base if pol == "none" else [run_pad(d, pol, lat) for d in pads]
            oos = [r["oos"] / PAD_LIFE * 1000 for r in res]
            m, ci = mean_ci(oos)
            leads = [s - r["first_prop"] for r, s in zip(res, sat0)
                     if r["first_prop"] is not None and s is not None]
            rows.append({
                "policy": pol, "latency_median_h": None if pol in ("none", "autonomous") else lat,
                "oos_per_1k": m, "oos_ci95": ci,
                "proposals_per_100_pads": 100 * statistics.fmean(r["proposals"] for r in res),
                # 그대로 두었어도 PM 전에 상한에 닿지 않았을 패드에 나간 제안
                "needless_per_100_pads": 100 * sum(r["proposals"] for r, s in zip(res, sat0)
                                                   if s is None) / n_pads,
                "applied_unused_per_100_pads": 100 * statistics.fmean(r["unused"] for r in res),
                "dropped_at_pm_per_100_pads": 100 * statistics.fmean(r["dropped_at_pm"] for r in res),
                "pads_window_exhausted": sum(r["exhausted"] for r in res) / n_pads,
                "refused_by_contract": sum(r["refused_by_contract"] for r in res),
                "median_lead_wafers": statistics.median(leads) if leads else None,
                "_oos": oos,
            })
        # 반응형 대비 예측형의 짝지은 차이 (같은 패드, 같은 잡음)
        rx = next(r for r in rows if r["policy"] == "reactive" and r["latency_median_h"] == lat)
        px = next(r for r in rows if r["policy"] == "predictive" and r["latency_median_h"] == lat)
        diff = [p - q for p, q in zip(px["_oos"], rx["_oos"])]
        px["paired_diff_vs_reactive"], px["paired_diff_ci95"] = mean_ci(diff)
    for r in rows:
        del r["_oos"]
    return {"seed": seed, "n_pads": n_pads, "wafers_per_pad": PAD_LIFE, "rw_sigma": rw_sigma,
            "pads_saturating_if_untouched": sum(s is not None for s in sat0) / n_pads,
            "median_untouched_saturation_wafer": statistics.median(s for s in sat0 if s is not None),
            "rows": rows}


def bias_case(seed=7, lat_median_h=2.0, bias=-150.0, bias_from=700, wear=0.0065):
    """사례: 마모가 아니라 계측 오프셋이 같은 신호를 만들 때. 단일 경로, 통계 아님."""
    d = pad_draws(seed, 0)
    d["wear"] = wear
    out = {}
    for pol, catch in (("none", False), ("reactive", True), ("autonomous", False)):
        r = run_pad(d, pol, lat_median_h, bias_from=bias_from, bias=bias, review_catches_bias=catch)
        out[pol] = {"oos": r["oos"], "proposals": r["proposals"], "applied": r["applied"],
                    "rejected_in_review": r["rejected_in_review"], "first_sat": r["first_sat"]}
    return {"seed": seed, "wear": wear, "bias": bias, "bias_from": bias_from,
            "latency_median_h": lat_median_h, "result": out}


def closed_loop_run(rng, n, b=45.0, t_max=125.0, warm=60):
    """정상 상태(마모 없음)의 폐루프 공정에서 n 매의 계측값. warm 매는 버린다."""
    b_hat = b
    ys = []
    for k in range(warm + n):
        t = min(max(TARGET / b_hat, T_MIN), t_max)
        y_meas = b * t + rng.gauss(0.0, SIGMA_PROC) + rng.gauss(0.0, SIGMA_MET)
        b_hat = LAMBDA * (y_meas / t) + (1 - LAMBDA) * b_hat
        if k >= warm:
            ys.append(y_meas)
    return ys


def closed_loop_capability(n=200000, seed=1):
    ys = closed_loop_run(random.Random(seed), n)
    m, sd = statistics.fmean(ys), statistics.pstdev(ys)
    return {"sd": sd, "mean": m, "cpk": min(USL - m, m - LSL) / (3 * sd),
            "sd_theory": SIGMA_CL}


QUAL_TRIALS = {5: 20000, 25: 8000, 50: 4000, 100: 3000, 200: 2000}


def qual_fail_rates(n, trials=None):
    """아무 문제 없는 폐루프 공정이 n 매 검증 규칙에 떨어질 확률. 판정은 계측값으로 한다.

    첫 초안: 평균 5000±50 Å 이고 Cpk ≥ 1.33
    고친 안: 평균 5000±50 Å 이고 표준편차 ≤ QUAL_SD_MAX
    """
    trials = trials or QUAL_TRIALS.get(n, 2000)
    f_cpk = f_mean = f_sd = f_old = f_new = 0
    for i in range(trials):
        ys = closed_loop_run(random.Random(f"qual:{n}:{i}"), n)
        m, sd = statistics.fmean(ys), statistics.stdev(ys)
        bad_mean = abs(m - TARGET) > 50
        bad_cpk = min(USL - m, m - LSL) / (3 * sd) < 1.33
        bad_sd = sd > QUAL_SD_MAX
        f_mean += bad_mean
        f_cpk += bad_cpk
        f_sd += bad_sd
        f_old += bad_mean or bad_cpk
        f_new += bad_mean or bad_sd
    return {"n": n, "trials": trials, "p_fail_cpk": f_cpk / trials,
            "p_fail_mean": f_mean / trials, "p_fail_sd": f_sd / trials,
            "p_fail_first_draft_rule": f_old / trials, "p_fail_revised_rule": f_new / trials}


def time_to_violation_h(wear, t_max=T_MAX0):
    """상한에 묶인 뒤 평균 제거량이 목표에서 LSL 까지 내려가는 데 걸리는 시간."""
    return (TARGET - LSL) / (wear * t_max) / WPH


if __name__ == "__main__":
    print(json.dumps(sweep(n_pads=int(os.environ.get("N_PADS", "400"))), ensure_ascii=False,
                     indent=2))
