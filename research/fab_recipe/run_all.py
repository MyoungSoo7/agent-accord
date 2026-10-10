#!/usr/bin/env python3
"""연구 1 의 모든 수치를 처음부터 다시 만든다: results.json, results.md, demo_transcript.txt.

    python3 research/fab_recipe/run_all.py          # 패드 1000장 × 마모 3조건 (약 2분)
    N_PADS=200 python3 research/fab_recipe/run_all.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import demo  # noqa: E402
import fab  # noqa: E402

RW_LEVELS = (0.0, 0.02, 0.05)


def f1(x):
    return "—" if x is None else f"{x:.1f}"


def table(sw):
    head = ("| 정책 | 승인 지연 중앙값 | 규격 밖 /1000매 (95% CI) | 제안 /100패드 | 불필요 제안 /100패드 "
            "| 쓰이지 않은 확대 /100패드 | PM 뒤 도착 /100패드 | 창 소진 패드 | 상한 도달 대비 제안 시점 (중앙값, 매) |")
    lines = [head, "|" + "---|" * 9]
    for r in sw["rows"]:
        lat = "—" if r["latency_median_h"] is None else f"{r['latency_median_h']:g} h"
        lead = r["median_lead_wafers"]
        lead_s = "—" if lead is None else (f"{lead:+.0f} (먼저)" if lead > 0 else f"{lead:+.0f} (늦게)")
        oos = f"{r['oos_per_1k']:.2f} ± {r['oos_ci95']:.2f}"
        if "paired_diff_vs_reactive" in r and r["latency_median_h"] is not None:
            oos += f" · 반응형 대비 {r['paired_diff_vs_reactive']:+.2f} ± {r['paired_diff_ci95']:.2f}"
        lines.append(f"| {fab.POLICY_KO[r['policy']]} | {lat} | {oos} | {f1(r['proposals_per_100_pads'])} "
                     f"| {f1(r['needless_per_100_pads'])} | {f1(r['applied_unused_per_100_pads'])} "
                     f"| {f1(r['dropped_at_pm_per_100_pads'])} | {r['pads_window_exhausted']:.1%} | {lead_s} |")
    return "\n".join(lines)


def main():
    t0 = time.time()
    n_pads = int(os.environ.get("N_PADS", "1000"))
    res = {"assumptions": {k: getattr(fab, k) for k in (
        "TARGET", "LSL", "USL", "SIGMA_PROC", "SIGMA_MET", "RATE0_MEAN", "RATE0_SD", "WEAR_MEDIAN",
        "WEAR_SIGMA", "PAD_LIFE", "WPH", "T_MIN", "T_MAX0", "MAX_STEP", "LAMBDA", "PIN_RUN",
        "FIT_WINDOW", "SPC_LAMBDA", "SPC_L", "LAT_SIGMA", "DRAFT_MEDIAN_H", "BUFFER_WAFERS",
        "WIN_LO", "WIN_HI", "SIGMA_CL", "QUAL_SD_MAX")}}
    res["sweeps"] = [fab.sweep(n_pads=n_pads, rw_sigma=rw) for rw in RW_LEVELS]
    res["time_to_violation_h"] = {f"{w:g}": fab.time_to_violation_h(w)
                                  for w in (0.004, 0.005, 0.0065, 0.008)}
    res["latency_q90_wafers"] = {f"{m:g}": fab.latency_q90_wafers(m) for m in (0.5, 2, 4, 8)}
    res["closed_loop"] = fab.closed_loop_capability()
    res["qual_rules"] = [fab.qual_fail_rates(n) for n in sorted(fab.QUAL_TRIALS)]
    res["bias_case"] = fab.bias_case()

    buf = io.StringIO()
    demo.main(out=buf)
    with open(os.path.join(HERE, "demo_transcript.txt"), "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    res["runtime_s"] = round(time.time() - t0, 1)
    with open(os.path.join(HERE, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)

    md = [f"# 연구 1 결과 (자동 생성 — run_all.py, 패드 {n_pads}장 × {fab.PAD_LIFE}매, "
          f"시드 {res['sweeps'][0]['seed']})", ""]
    for sw in res["sweeps"]:
        md += [f"## 마모 랜덤워크 σ = {sw['rw_sigma']:g} Å/s/웨이퍼", "",
               f"그대로 두면 PM 전에 상한에 닿는 패드 {sw['pads_saturating_if_untouched']:.1%}, "
               f"닿는 시점 중앙값 W{sw['median_untouched_saturation_wafer']:.0f}", "", table(sw), ""]
    md += ["## 상한에 묶인 뒤 평균이 LSL 까지 가는 시간 (마모율별)", "",
           "| 마모율 (Å/s/웨이퍼) | 시간 (h) |", "|---|---|"]
    md += [f"| {k} | {v:.1f} |" for k, v in res["time_to_violation_h"].items()]
    cl = res["closed_loop"]
    md += ["", "## 검증 규칙의 오판정 — 정상 폐루프 공정",
           "", f"폐루프 계측 표준편차 {cl['sd']:.2f} Å (이론 {cl['sd_theory']:.2f}), Cpk {cl['cpk']:.3f}", "",
           "| 매수 | 시행 | Cpk ≥ 1.33 에 떨어짐 | 평균 ±50 Å 에 떨어짐 | 표준편차 ≤ "
           f"{fab.QUAL_SD_MAX:.0f} Å 에 떨어짐 | 첫 초안 규칙 | 고친 규칙 |", "|---|---|---|---|---|---|---|"]
    md += [f"| {q['n']} | {q['trials']} | {q['p_fail_cpk']:.1%} | {q['p_fail_mean']:.2%} | "
           f"{q['p_fail_sd']:.2%} | {q['p_fail_first_draft_rule']:.1%} | {q['p_fail_revised_rule']:.2%} |"
           for q in res["qual_rules"]]
    bc = res["bias_case"]
    md += ["", f"## 사례: 계측 오프셋 {bc['bias']:g} Å (W{bc['bias_from']}부터, 마모율 {bc['wear']}, "
           f"승인 지연 {bc['latency_median_h']:g} h)", "",
           "| 정책 | 규격 밖 웨이퍼 | 제안 | 적용 | 검토에서 반려 |", "|---|---|---|---|---|"]
    md += [f"| {fab.POLICY_KO[k]} | {v['oos']} | {v['proposals']} | {v['applied']} | "
           f"{v['rejected_in_review']} |" for k, v in bc["result"].items()]
    md += ["", f"실행 시간 {res['runtime_s']} s"]
    with open(os.path.join(HERE, "results.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
