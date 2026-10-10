"""Render results.json (dict) as results.md. Every number in README.md is copied
from here, with the same formatting (checked by check_report.py)."""

from __future__ import annotations

import numpy as np

KEYS_MAIN = ("mspc", "iforest", "l1lr", "rf", "hgb")
KEYS_ALL = ("mspc", "mspc_t2", "mspc_spe", "iforest", "l1lr", "rf", "hgb", "time_knn")
LABEL = {
    "mspc": "MSPC (T² or SPE)",
    "mspc_t2": "MSPC T² only",
    "mspc_spe": "MSPC SPE only",
    "iforest": "Isolation Forest",
    "l1lr": "L1 logistic",
    "rf": "Random forest",
    "hgb": "HistGradientBoosting",
    "time_knn": "time only (k-NN in time)",
}


def f3(x) -> str:
    return "nan" if x is None or not np.isfinite(x) else f"{x:.3f}"


def f2(x) -> str:
    return f"{x:.2f}"


def ci3(c) -> str:
    return f"[{f3(c[0])}, {f3(c[1])}]"


def pct(x, d: int = 1) -> str:
    return f"{100 * x:.{d}f}%"


def pv(p) -> str:
    return f"{p:.1e}" if p < 1e-4 else f"{p:.4f}"


def sgn3(x) -> str:
    return f"{x:+.3f}"


def table(header, rows) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _metric_cell(summary, metric, mean=True, fold=None):
    if fold is None:
        return f"{f3(summary['mean'][metric])} {ci3(summary['mean_ci'][metric])}"
    pf = summary["per_fold"][fold]
    return f"{f3(pf[metric])} {ci3(pf['ci'][metric])}"


def render(R: dict) -> str:
    meta, D, E = R["meta"], R["data"], R["evaluation"]
    L = []
    L.append("# results.md — run_all.py 가 생성한 결과 (손으로 고치지 않는다)\n")
    L.append(
        f"생성: run_all.py · 실행 시간 {meta['runtime_s']:.0f}초 · seed {meta['seed']} · "
        f"bootstrap {meta['n_boot']}회 (층화) · 순열 {meta['n_perm']}회 · "
        f"Python {meta['versions']['python']} · numpy {meta['versions']['numpy']} · "
        f"pandas {meta['versions']['pandas']} · scikit-learn {meta['versions']['scikit-learn']} · "
        f"scipy {meta['versions']['scipy']} · matplotlib {meta['versions']['matplotlib']}\n"
    )

    cfg = meta["config"]
    L.append("## 설정 (이 실행이 실제로 쓴 값, 시험 결과를 보고 고치지 않았다)\n")
    rows = [
        ["전처리", f"학습 구간 결측률 > {cfg['max_missing_frac']!r} 또는 상수인 특성 제거 → 학습 구간 중앙값 대치 → 표준화"],
        ["HGB 사전 구간화", f"학습 구간 분위수 {cfg['hgb_prebin_max_bins']} 구간"],
        ["시간순 70/30", f"앞 {cfg['holdout_train_frac']!r} 학습"],
        ["확장 창", f"{cfg['n_blocks']} 블록, {cfg['n_blocks'] - 1} fold"],
        ["무작위 비교군", f"층화 {cfg['random_kfold_splits']}-fold × {cfg['random_kfold_repeats']}, 층화 70/30 × {cfg['random_holdout_repeats']}"],
        ["시간만 쓰는 기준선", f"시간상 가장 가까운 학습 개체 {cfg['time_knn_k']}개의 불량률"],
        ["경보 예산", f"연속 {cfg['chunk']}개마다 {', '.join(map(str, cfg['budgets_per_100']))}개"],
        ["순열 중요도", f"반복 {cfg['importance_repeats']}회, 모델 {', '.join(cfg['importance_models'])}, top-k {', '.join(map(str, cfg['top_k']))}"],
    ]
    for key, mc in cfg["models"].items():
        params = ", ".join(f"{k}={v!r}" if isinstance(v, (float, str)) else f"{k}={v}" for k, v in mc["params"].items())
        rows.append([f"{mc['label']} ({key})", f"{mc['fit_rows']}; {params}" + ("; 사전 구간화" if mc["prebin"] else "")])
    L.append(table(["항목", "값"], rows) + "\n")

    # ------------------------------------------------------------------ Q1
    t, m, c, cl, dr, nb = D["time"], D["missing"], D["constant"], D["clustering"], D["drift"], D["neighbours"]
    L.append("## Q1. 데이터 특성\n")
    L.append("### 파일 검증\n")
    L.append(table(["파일", "sha256"], [[k, f"`{v}`"] for k, v in D["sha256"].items()]) + "\n")
    rows = [
        ["행 × 특성", f"{t['n']} × {D['n_features']}"],
        ["불량 / 정상", f"{t['n_fail']} / {t['n_pass']} (불량률 {pct(t['prevalence'], 2)})"],
        ["다수 클래스(전부 정상) 정확도", pct(t["majority_accuracy"], 2)],
        ["기간", f"{t['first']} ~ {t['last']} ({t['span_days']:.2f}일, 달력 {t['calendar_days']}일 중 생산 없는 날 {t['days_without_entities']}일)"],
        ["생산일당 개체 수 (중앙값)", f"{t['entities_per_active_day_median']:.1f}"],
        ["연속 개체 간격 중앙값 / 최대", f"{t['median_gap_min']:.0f}분 / {t['max_gap_hours']:.1f}시간"],
        ["중복 타임스탬프", f"{t['duplicate_timestamps']}"],
        ["결측 셀", f"{m['missing_cells']} ({pct(m['missing_cell_frac'], 2)})"],
        [
            "결측이 있는 특성 / >5% / >50% / >90%",
            " / ".join(str(m["features_with_missing_gt"][k]) for k in ("0", "0.05", "0.5", "0.9")),
        ],
        ["특성 결측률 최대 / 중앙값", f"{pct(m['feature_missing_max'], 2)} / {pct(m['feature_missing_median'], 2)}"],
        [
            "행 결측률 최소 / 중앙값 / 최대",
            f"{pct(m['row_missing_min'], 2)} / {pct(m['row_missing_median'], 2)} / {pct(m['row_missing_max'], 2)}",
        ],
        ["결측 없는 행", f"{m['rows_complete']}"],
        [
            ">5% 결측 특성의 결측 패턴 (같이 비는 묶음)",
            f"{m['n_patterns_among_gt05']}개 패턴, 묶음 크기 {', '.join(map(str, m['pattern_group_sizes']))}",
        ],
        [
            "행 결측률: 불량 vs 정상 (중앙값, Mann-Whitney)",
            f"{pct(m['row_missing_median_fail'], 2)} vs {pct(m['row_missing_median_pass'], 2)}, p = {pv(m['row_missing_mannwhitney_p'])}",
        ],
        ["상수 특성 (서로 다른 값 ≤ 1)", f"{c['constant']}"],
        ["두 값만 갖는 특성", f"{c['two_valued']}"],
        [
            "준상수 (최빈값 비율 ≥ 95% / ≥ 99%, 상수 제외)",
            f"{c['near_constant_modal_ge_095']} / {c['near_constant_modal_ge_099']}",
        ],
        [
            "전처리 규칙(결측 >50% 또는 상수 제거)을 전체 데이터에 적용하면",
            f"{c['kept_full_data_rule']}개 남고 {c['dropped_full_data_rule']}개 탈락 (실제 학습에서는 학습 구간에서만 계산)",
        ],
    ]
    L.append(table(["항목", "값"], rows) + "\n")

    L.append("### 주별 불량률 (첫 개체 날짜부터 7일 구간, Wilson 95% CI)\n")
    L.append(
        table(
            ["주", "시작일", "개체", "불량", "불량률", "95% CI"],
            [[w["week"], w["start"], w["n"], w["fails"], pct(w["rate"]), f"[{pct(w['ci'][0])}, {pct(w['ci'][1])}]"] for w in D["weekly"]],
        )
        + "\n"
    )

    L.append("### 불량의 시간 군집 (귀무가설: 불량 라벨이 시간 순서와 무관)\n")
    rows = [
        [
            "runs 검정 (불량/정상 연속 구간 수)",
            f"관측 {cl['runs_observed']} vs 기대 {cl['runs_expected']:.1f}, z = {cl['runs_z']:.2f}, "
            f"p(정규근사, 단측) = {pv(cl['runs_p_normal_one_sided'])}, p(순열) = {pv(cl['runs_p_perm_one_sided'])}",
        ],
        [
            "불량 지시변수 자기상관",
            f"lag 1 = {f3(cl['acf_lag1'])}; 순열 95% 띠를 넘는 lag: {', '.join(map(str, cl['acf_lags_above_band']))}",
        ],
        [
            f"Ljung-Box Q({cl['ljung_box_lags']})",
            f"{cl['ljung_box_q']:.1f}, p(χ²) = {pv(cl['ljung_box_p_chi2'])}, p(순열) = {pv(cl['ljung_box_p_perm'])}",
        ],
        [
            "주별 불량률 동질성 χ²",
            f"{cl['weekly_chi2']:.1f} (자유도 {cl['weekly_chi2_df']}), p(순열) = {pv(cl['weekly_chi2_p_perm'])}",
        ],
        ["불량률 앞 절반 vs 뒤 절반", f"{pct(cl['fail_rate_first_half'], 2)} vs {pct(cl['fail_rate_second_half'], 2)}"],
    ]
    L.append(table(["검정", "결과"], rows) + f"\n\n순열 횟수 {cl['n_perm']}; 순열 p 의 최솟값은 1/({cl['n_perm']}+1).\n")

    L.append("### 공변량 이동 (drift)\n")
    rows = [
        [
            "KS 검정, 앞 절반 vs 뒤 절반 (BH q < 0.05)",
            f"{dr['ks_bh_q_lt_005']} / {dr['features_tested']} 특성 ({pct(dr['ks_bh_q_lt_005_frac'])})",
        ],
        [
            "적대적 검증: 특성만으로 '시험 구간(뒤 30%) 여부'를 맞히는 RF, 무작위 5-fold ROC-AUC",
            f"평균 {f3(dr['adversarial_auc_mean'])} (최소 {f3(dr['adversarial_auc_min'])})",
        ],
        ["특성 공간 최근접 이웃과의 시간 순번 차이 (중앙값)", f"{nb['median_index_gap']:.0f}"],
        [
            "최근접 이웃이 ±1 / ±5 / ±25 순번 안 (무작위 기대)",
            f"{pct(nb['frac_within_1'])} ({pct(nb['frac_within_1_random'], 2)}) / "
            f"{pct(nb['frac_within_5'])} ({pct(nb['frac_within_5_random'], 2)}) / "
            f"{pct(nb['frac_within_25'])} ({pct(nb['frac_within_25_random'], 2)})",
        ],
    ]
    L.append(table(["항목", "값"], rows) + "\n")

    # ------------------------------------------------------------- splits
    S = R["splits"]
    L.append("## 분할\n")
    h = S["chrono_holdout"]
    L.append(
        table(
            ["분할", "학습 (불량)", "학습 기간", "시험 (불량)", "시험 기간", "시험 불량률"],
            [
                [
                    "시간순 70/30",
                    f"{h['n_train']} ({h['train_fails']})",
                    " ~ ".join(h["train_period"]),
                    f"{h['n_test']} ({h['test_fails']})",
                    " ~ ".join(h["test_period"]),
                    pct(h["test_fails"] / h["n_test"], 2),
                ]
            ]
            + [
                [
                    f"확장 창 fold {i + 1}",
                    f"{f['n_train']} ({f['train_fails']})",
                    " ~ ".join(f["train_period"]),
                    f"{f['n_test']} ({f['test_fails']})",
                    " ~ ".join(f["test_period"]),
                    pct(f["test_fails"] / f["n_test"], 2),
                ]
                for i, f in enumerate(S["expanding"])
            ],
        )
        + "\n"
    )
    L.append(
        f"무작위 비교군: 층화 5-fold × {S['random_kfold']['repeats']}회 (seed {S['random_kfold']['seeds']}), "
        f"층화 무작위 70/30 × {S['random_holdout']['repeats']}회 (시험 {S['random_holdout']['n_test']}개).\n"
    )

    # ------------------------------------------------------------------ Q2
    L.append("## Q2. 평가의 정직성: 무작위 CV vs 시간순\n")
    L.append("fold 평균과 95% CI (각 시험 fold 안에서 층화 bootstrap 후 평균). Δ = 무작위 − 시간순.\n")
    for metric, title in (("roc_auc", "ROC-AUC"), ("pr_auc", "PR-AUC (average precision)")):
        rows = []
        for k in KEYS_ALL:
            o = R["optimism"][k][metric]
            rows.append(
                [
                    LABEL[k],
                    _metric_cell(E["random_kfold"][k], metric),
                    _metric_cell(E["expanding"][k], metric),
                    sgn3(o["diff_cv"]),
                    _metric_cell(E["random_holdout"][k], metric),
                    _metric_cell(E["chrono_holdout"][k], metric),
                    sgn3(o["diff_holdout"]),
                ]
            )
        if metric == "pr_auc":
            rows.append(
                ["(불량률)"]
                + [
                    f3(E[sch]["l1lr"]["mean"]["prevalence"]) if sch else ""
                    for sch in ("random_kfold", "expanding", None, "random_holdout", "chrono_holdout", None)
                ]
            )
            rows.append(
                ["(무작위 순위의 기대 PR-AUC, 라벨 순열)"]
                + [
                    f3(E[sch]["l1lr"]["mean"]["ap_random"]) if sch else ""
                    for sch in ("random_kfold", "expanding", None, "random_holdout", "chrono_holdout", None)
                ]
            )
        L.append(f"### {title}\n")
        L.append(
            table(
                ["모델", "무작위 5-fold×2", "확장 창 (시간순)", "Δ", "무작위 70/30×5", "시간순 70/30", "Δ"],
                rows,
            )
            + "\n"
        )
    OS = R["optimism_summary"]
    for metric, title in (("roc_auc", "ROC-AUC"), ("pr_auc", "PR-AUC")):
        o = OS[metric]
        L.append(
            f"- {title}, 5개 모델 평균 Δ: 무작위 5-fold − 확장 창 = {sgn3(o['mean_diff_cv'])}, "
            f"무작위 70/30 − 시간순 70/30 = {sgn3(o['mean_diff_holdout'])}. "
            f"순위 — 무작위 5-fold: {' > '.join(LABEL[k] for k in o['rank_random_kfold'])}; "
            f"확장 창: {' > '.join(LABEL[k] for k in o['rank_expanding'])}."
        )
    L.append("")
    L.append(
        "무작위 CV 의 CI 는 반복 간 같은 행을 다시 쓰므로 실제보다 좁다 (근사). "
        "'time only' 는 타임스탬프만 쓰는 기준선이다: 학습 구간에서 시간상 가장 가까운 "
        f"{meta['time_knn_k']}개의 불량률. 시간순 분할에서는 모든 시험 개체가 같은 점수를 받아 0.5 가 된다.\n"
    )

    L.append("### 정확도 함정 (시간순 70/30 시험 구간, 각 모델의 기본 판정 규칙)\n")
    rows = []
    for name, cm in R["accuracy_trap"]["rules"].items():
        rows.append(
            [
                name,
                pct(cm["accuracy"]),
                pct(cm["balanced_accuracy"]),
                f"{cm['tp']}/{cm['tp'] + cm['fn']}",
                pct(cm["recall"]),
                "—" if cm["precision"] is None else pct(cm["precision"]),
                pct(cm["false_alarm_rate"]),
                pct(cm["alarm_rate"]),
            ]
        )
    L.append(
        table(
            ["판정 규칙", "정확도", "균형 정확도", "잡은 불량", "재현율", "정밀도", "오경보율", "경보율"],
            rows,
        )
        + "\n"
    )

    # ------------------------------------------------------------------ Q3
    L.append("## Q3. 모델 성능 (시간순 평가, 전처리는 학습 구간에서만 적합)\n")
    for scheme, title in (("chrono_holdout", "시간순 70/30 시험 구간"), ("expanding", "확장 창 5 fold 평균")):
        rows = []
        for k in KEYS_ALL:
            s_ = E[scheme][k]
            rows.append(
                [
                    LABEL[k],
                    _metric_cell(s_, "roc_auc"),
                    pv(s_["mean"]["p_auc"]),
                    _metric_cell(s_, "pr_auc"),
                    f3(s_["mean"]["ap_random"]),
                    pv(s_["mean"]["p_ap"]),
                    _metric_cell(s_, "recall_at_fpr05"),
                    _metric_cell(s_, "recall_at_fpr10"),
                ]
            )
        prev = E[scheme]["l1lr"]["mean"]["prevalence"]
        L.append(f"### {title} (불량률 {f3(prev)})\n")
        L.append(
            table(
                [
                    "모델",
                    "ROC-AUC [95% CI]",
                    "p (AUC)",
                    "PR-AUC [95% CI]",
                    "무작위 순위 AP",
                    "p (AP)",
                    "재현율@오경보 5% [CI]",
                    "재현율@오경보 10% [CI]",
                ],
                rows,
            )
            + "\n"
        )
    L.append(
        f"p = 라벨을 점수에 대해 {meta['n_null']}회 섞은 순열 p (단측, '무작위 순위보다 낫다'). "
        "fold 평균의 p 는 각 fold 안에서 섞은 뒤 평균한 귀무분포에서 구했다. 모델 5개를 비교하므로 다중비교를 감안해 읽어야 한다.\n"
    )
    L.append("### 확장 창 fold 별\n")
    rows = []
    for i, finfo in enumerate(S["expanding"]):
        for k in KEYS_MAIN:
            pf = E["expanding"][k]["per_fold"][i]
            rows.append(
                [
                    f"{i + 1} ({finfo['test_fails']}/{finfo['n_test']})",
                    LABEL[k],
                    f"{f3(pf['roc_auc'])} {ci3(pf['ci']['roc_auc'])}",
                    pv(pf["p_auc"]),
                    f"{f3(pf['pr_auc'])} {ci3(pf['ci']['pr_auc'])}",
                    f3(pf["ap_random"]),
                    f3(pf["recall_at_fpr05"]),
                    f3(pf["recall_at_fpr10"]),
                ]
            )
    L.append(
        table(
            ["fold (불량/개체)", "모델", "ROC-AUC [95% CI]", "p (AUC)", "PR-AUC [95% CI]", "무작위 순위 AP", "재현율@5%", "재현율@10%"],
            rows,
        )
        + "\n"
    )

    L.append("### MSPC 진단 (99% 관리한계, 이론 오경보율 = 차트당 1%)\n")
    rows = []
    for name, info, fl in [("시간순 70/30", R["mspc"]["holdout"], R["mspc"]["holdout_test_far"])] + [
        (f"확장 창 fold {i + 1}", inf, far) for i, (inf, far) in enumerate(zip(R["mspc"]["expanding"], R["mspc"]["expanding_test_far"]))
    ]:
        rows.append(
            [
                name,
                info["n_noc"],
                info["n_components"],
                pct(info["explained"]),
                pct(info["train_alarm_t2_99"]),
                pct(info["train_alarm_spe_99"]),
                pct(fl["t2"]),
                pct(fl["spe"]),
                pct(fl["either"]),
                pct(fl["recall_either"]),
            ]
        )
    L.append(
        table(
            [
                "적합",
                "NOC 정상 개체",
                "PC 수 k",
                "설명 분산",
                "학습 T² 초과",
                "학습 SPE 초과",
                "시험 정상 T² 초과",
                "시험 정상 SPE 초과",
                "시험 정상 둘 중 하나",
                "시험 불량 재현율",
            ],
            rows,
        )
        + "\n"
    )
    fx = R["mspc"]["fixed_limits_expanding"]
    rows = [
        [
            f"{lvl}%",
            pct(v["false_alarm_rate_t2"]),
            pct(v["false_alarm_rate_spe"]),
            pct(v["false_alarm_rate"]),
            pct(v["alarm_rate"]),
            f"{v['tp']}/{v['tp'] + v['fn']}",
            pct(v["recall"]),
        ]
        for lvl, v in fx.items()
    ]
    L.append("확장 창 시험 블록 B2–B6 전체에서 이론 한계를 그대로 쓸 때:\n")
    L.append(table(["한계", "정상 T² 초과", "정상 SPE 초과", "오경보율(둘 중 하나)", "경보율", "잡은 불량", "재현율"], rows) + "\n")

    # ------------------------------------------------------------------ Q4
    I = R["importance"]
    L.append("## Q4. 어떤 신호가 중요한가, 그리고 시간이 지나도 같은가\n")
    L.append(
        f"순열 중요도 = 확장 창 각 시험 블록에서 특성 하나를 섞었을 때 ROC-AUC 하락 (반복 {I['n_repeats']}회 평균). "
        f"상관 군집 = 전체 데이터(라벨 없이)의 |Spearman ρ| 평균연결 군집, 1−|ρ| ≤ {I['cluster_cut']} 에서 자름 → "
        f"남는 {I['n_kept_full']}개 특성이 {I['n_clusters']}개 군집.\n"
    )
    rows = []
    for k in I["keys"]:
        st = I["models"][k]
        t10, t20 = st["top_k"]["10"], st["top_k"]["20"]
        rows.append(
            [
                LABEL[k],
                ", ".join(f3(p["base_auc"]) for p in st["per_fold"]),
                ", ".join(f3(p["max_drop"]) for p in st["per_fold"]),
                ", ".join(str(p["n_drop_gt_001"]) for p in st["per_fold"]),
                f"{f3(t10['jaccard']['mean'])} ({f3(t10['jaccard']['min'])}–{f3(t10['jaccard']['max'])})",
                f3(t10["cluster_jaccard"]["mean"]),
                f3(t20["jaccard"]["mean"]),
                ", ".join(t10["in_ge3_folds"]) or "없음",
            ]
        )
    nul10 = I["models"][I["keys"][0]]["top_k"]["10"]["null"]
    nul20 = I["models"][I["keys"][0]]["top_k"]["20"]["null"]
    L.append(
        table(
            [
                "모델",
                "fold 별 기준 AUC",
                "fold 별 최대 단일 하락",
                "fold 별 하락>0.01 특성 수",
                "top-10 Jaccard 평균 (최소–최대)",
                "top-10 군집 Jaccard",
                "top-20 Jaccard",
                "top-10 에 3 fold 이상 등장",
            ],
            rows,
        )
        + "\n"
    )
    L.append(
        f"무작위 기준: 공통 후보 특성에서 뽑은 무작위 top-10 두 개의 Jaccard 평균 {f3(nul10['mean'])} "
        f"(95 백분위 {f3(nul10['p95'])}), top-20 은 {f3(nul20['mean'])} (95 백분위 {f3(nul20['p95'])}).\n"
    )
    L.append("### fold 별 top-10 (순열 중요도)\n")
    rows = []
    for k in I["keys"]:
        for p in I["models"][k]["per_fold"]:
            rows.append([LABEL[k], p["fold"], ", ".join(p["top10"])])
    L.append(table(["모델", "fold", "top-10"], rows) + "\n")

    sel = I["l1lr_selection"]
    L.append("### L1 로지스틱이 고른 특성 (계수 ≠ 0)\n")
    L.append(
        table(
            ["fold", "고른 특성 수"],
            [[i + 1, n] for i, n in enumerate(sel["n_selected"])],
        )
        + f"\n\n쌍별 Jaccard 평균 {f3(sel['jaccard']['mean'])} (최소 {f3(sel['jaccard']['min'])}, 최대 {f3(sel['jaccard']['max'])}); "
        f"5 fold 모두에서 고른 특성: {', '.join(sel['in_all_folds']) or '없음'}.\n"
    )
    uf = I["univariate_filter"]
    L.append(f"### 단변량 선별 (secom.names 의 기준선 방식): 학습 구간 |AUC−0.5| 상위 {uf['k']}개\n")
    rows = [
        [p["fold"], ", ".join(p["top"]), f3(p["train_auc_strength_mean"]), f3(p["test_oriented_auc_mean"])]
        for p in uf["per_fold"]
    ]
    L.append(
        table(["fold", "학습 구간 상위 특성", "학습 구간 평균 (0.5+|AUC−0.5|)", "다음 시험 블록에서 같은 방향 AUC 평균"], rows)
        + f"\n\nfold 간 쌍별 Jaccard 평균 {f3(uf['jaccard']['mean'])} (최소 {f3(uf['jaccard']['min'])}, 최대 {f3(uf['jaccard']['max'])}).\n"
    )
    hv = I["halves"]
    rows = [
        ["비교한 특성 수", hv["n_features_compared"]],
        ["(AUC−0.5) 의 Spearman ρ, 앞 절반 vs 뒤 절반", f"{f3(hv['spearman_rho'])} (p = {pv(hv['spearman_p'])})"],
        ["Mann-Whitney BH q<0.05 특성: 전체 / 앞 절반 / 뒤 절반 / 양쪽 모두", f"{hv['bh_q005_full']} / {hv['bh_q005_first_half']} / {hv['bh_q005_second_half']} / {hv['bh_q005_both_halves']}"],
        ["전체 데이터 단변량 |AUC−0.5| 최대", f3(hv["max_abs_auc_dev_full"])],
    ]
    L.append("### 앞 절반 vs 뒤 절반의 단변량 신호\n")
    L.append(table(["항목", "값"], rows) + "\n")

    # ------------------------------------------------------------------ Q5
    B = R["alarm_budget"]
    L.append("## Q5. 경보 예산 (확장 창 시험 블록 B2–B6, 연속 100개마다 점수 상위 k개 검사)\n")
    L.append(f"개체 {B['n_entities']}개, 불량 {B['n_fail']}개, 블록 경계를 넘지 않는 {B['n_chunks']}개 묶음.\n")
    rows = []
    for b, row in B["budgets"].items():
        rnd = row["random"]
        cells = [b, rnd["n_alarm"], f"{rnd['expected']:.1f} [{rnd['lo']:.0f}, {rnd['hi']:.0f}]"]
        for k in KEYS_MAIN:
            mm = row["models"][k]
            mark = "*" if mm["above_random_hi"] else ""
            cells.append(f"{mm['caught']}{mark} ({pct(mm['recall'], 0)}, ×{mm['lift_vs_random']:.1f})")
        rows.append(cells)
    L.append(
        table(
            ["100개당 경보", "총 경보", "무작위 검사 기대 [95% 범위]"] + [LABEL[k] for k in KEYS_MAIN],
            rows,
        )
        + "\n\n칸: 잡은 불량 수 (재현율, 무작위 기대 대비 배수). * = 무작위 검사 95% 범위의 상한보다 많이 잡음.\n"
    )

    # ------------------------------------------------------- sensitivity
    SN = R["sensitivity"]
    L.append("## 민감도 (선택에 쓰지 않았다; 본 분석은 C = 0.1, CPV = 0.9 고정)\n")
    rows = []
    for C in SN["l1lr_C"]["expanding"]:
        e, r = SN["l1lr_C"]["expanding"][C], SN["l1lr_C"]["random_kfold"][C]
        rows.append([C, f3(e["roc_auc"]), f3(e["pr_auc"]), f3(r["roc_auc"]), f3(r["pr_auc"])])
    rows.append(["격자 평균"] + [
        f3(np.mean([SN["l1lr_C"][sch][C][m] for C in SN["l1lr_C"][sch]]))
        for sch, m in (("expanding", "roc_auc"), ("expanding", "pr_auc"), ("random_kfold", "roc_auc"), ("random_kfold", "pr_auc"))
    ])
    L.append("### L1 로지스틱 C\n")
    L.append(table(["C", "확장 창 ROC-AUC", "확장 창 PR-AUC", "무작위 5-fold ROC-AUC", "무작위 5-fold PR-AUC"], rows) + "\n")
    rows = []
    for cpv in SN["mspc_cpv"]["expanding"]:
        e, r = SN["mspc_cpv"]["expanding"][cpv], SN["mspc_cpv"]["random_kfold"][cpv]
        rows.append([cpv, f"{e['mean_n_components']:.1f}", f3(e["roc_auc"]), f3(e["pr_auc"]), f3(r["roc_auc"]), f3(r["pr_auc"])])
    rows.append(["격자 평균", ""] + [
        f3(np.mean([SN["mspc_cpv"][sch][c][m] for c in SN["mspc_cpv"][sch]]))
        for sch, m in (("expanding", "roc_auc"), ("expanding", "pr_auc"), ("random_kfold", "roc_auc"), ("random_kfold", "pr_auc"))
    ])
    L.append("### MSPC 누적 설명 분산 (CPV)\n")
    L.append(table(["CPV", "평균 k (확장 창)", "확장 창 ROC-AUC", "확장 창 PR-AUC", "무작위 5-fold ROC-AUC", "무작위 5-fold PR-AUC"], rows) + "\n")

    L.append("## 부록: bootstrap 구간은 얼마나 믿을 만한가 (모의실험)\n")
    rows = []
    for c in R["ci_coverage"]:
        rows.append(
            [
                f"{c['n']}개 중 불량 {c['n_fail']}",
                f3(c["prevalence"]),
                f3(c["ap_random_ranking"]),
                f3(c["population_auc"]),
                f3(c["mean_sample_auc"]),
                pct(c["coverage_auc"]),
                f3(c["population_ap"]),
                f3(c["mean_sample_ap"]),
                pct(c["coverage_ap"]),
            ]
        )
    c0 = R["ci_coverage"][0]
    L.append(
        table(
            [
                "시험 크기",
                "불량률",
                "무작위 순위 기대 AP",
                "모집단 AUC",
                "표본 AUC 평균",
                "AUC 95% 구간 포함률",
                "모집단 AP",
                "표본 AP 평균",
                "AP 95% 구간 포함률",
            ],
            rows,
        )
        + f"\n\n정규분포 점수, 불량만 {c0['shift']} 이동 · 반복 {c0['reps']}회 · bootstrap {c0['n_boot']}회 (층화 퍼센타일).\n"
    )

    L.append("## 그림\n")
    for name, desc in R["figures"].items():
        L.append(f"- `figures/{name}` — {desc}")
    L.append("")
    return "\n".join(L)
