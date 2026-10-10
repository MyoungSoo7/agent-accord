"""모든 결과·그림·보고서를 처음부터 다시 만든다.

    cd research/r2r_vm && .venv/bin/python run_all.py

산출물: figures/*.png, results.json, results.md, README.md (report_template.tmpl 를 결과로 채운 것)
"""
from __future__ import annotations

import dataclasses
import os
import platform
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import matplotlib  # noqa: E402
import numpy  # noqa: E402
import scipy  # noqa: E402

from r2r import common as C, plots, q1, q2, q3, report  # noqa: E402

FIG_LIMIT = 200 * 1024


def main():
    t0 = time.time()
    fig_dir = os.path.join(ROOT, "figures")
    os.makedirs(fig_dir, exist_ok=True)
    stage = {}

    t = time.time()
    r1, a1 = q1.run()
    stage["q1_s"] = time.time() - t
    print(f"[Q1] {stage['q1_s']:.1f} s")

    t = time.time()
    r2, a2, _ = q2.run()
    stage["q2_s"] = time.time() - t
    print(f"[Q2] {stage['q2_s']:.1f} s")

    t = time.time()
    r3, a3 = q3.run()
    stage["q3_s"] = time.time() - t
    print(f"[Q3] {stage['q3_s']:.1f} s")

    t = time.time()
    f = lambda name: os.path.join(fig_dir, name)  # noqa: E731
    sizes = {
        "q1_stability_regions.png": plots.q1_regions(a1, q1.LAM_FINE, q1.XI_FINE, f("q1_stability_regions.png")),
        "q1_delay_limits.png": plots.q1_delay(a1, f("q1_delay_limits.png")),
        "q2_pm_recovery.png": plots.q2_pm_recovery(a2["pm_profile"], C.P.spec, f("q2_pm_recovery.png")),
        "q2_example_run.png": plots.q2_example(a2["example"], a2["pm_wafers"], C.P, f("q2_example_run.png")),
        "q2_saturation.png": plots.q2_saturation(r2["saturation"], a2["sat_profile"], C.P, f("q2_saturation.png")),
        "q3_mse_vs_metrology.png": plots.q3_ratio(r3["eval"], f("q3_mse_vs_metrology.png"), C.EQ_TOL),
        "q3_pm_profiles.png": plots.q3_profiles(a3["profiles"], f("q3_pm_profiles.png"), q3.PROFILE_M),
    }
    stage["figures_s"] = time.time() - t
    too_big = {k: v for k, v in sizes.items() if v > FIG_LIMIT}
    if too_big:
        raise SystemExit(f"그림이 200 KB 를 넘는다: {too_big}")
    print(f"[그림] {len(sizes)}장, 최대 {max(sizes.values()) / 1024:.0f} KB")

    meta = {
        "runtime_s": time.time() - t0, "stages_s": stage, "figure_bytes": sizes,
        "python": platform.python_version(), "numpy": numpy.__version__, "scipy": scipy.__version__,
        "matplotlib": matplotlib.__version__,
        "process": dataclasses.asdict(C.P),
        "config": {k: getattr(C, k) for k in ("N_WAFERS", "BURN", "DELAY", "LAM_GRID", "D_L1", "D_L2", "SENS_DELAY",
                                              "SENS_WEAR", "SAT_WEAR", "SAT_N", "SAT_K", "REC_RUN", "POST_PM_WIN",
                                              "Q3_M", "Q3_LAM", "Q3_STRATS", "K_BOOST", "T2_Q", "RES_K", "EQ_TOL",
                                              "CAL_LAM")},
        "eval_seeds": [C.EVAL_SEEDS[0], C.EVAL_SEEDS[-1]], "tune_seeds": [C.TUNE_SEEDS[0], C.TUNE_SEEDS[-1]],
        "note": "runtime_s·stages_s 만 실행마다 다르다. 나머지는 고정 seed 로 재현된다.",
    }
    checks = report.write_all(ROOT, r1, r2, r3, meta, os.path.join(ROOT, "report_template.tmpl"))
    print(f"[보고서] results.json · results.md · README.md — 본문 주장 {len(checks)}개 확인")
    print(f"전체 {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
