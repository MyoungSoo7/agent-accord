#!/usr/bin/env python3
"""Regenerate results.json, results.md and figures/*.png from data/raw/.

    .venv/bin/python -I fetch_data.py
    .venv/bin/python -I run_all.py

Deterministic (fixed seeds); about 5-6 minutes on 4 vCPU.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from importlib import metadata
from pathlib import Path

# OpenMP threads (HistGradientBoosting) spin-wait by default. On a shared
# 4-vCPU box with other jobs running, that made single fits ~35x slower
# (70 s instead of 2 s); passive waiting keeps them at ~4-5 s. Results do not
# depend on this, only the run time. Must be set before numpy/sklearn load.
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")

HERE = Path(__file__).resolve().parent
# `python -I` does not put the script directory on sys.path. Add back only this
# study's own code directory (never the data directory).
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

from secom import characterize, evaluate, importance, plots, report, simulate  # noqa: E402
from secom.data import DEFAULT_RAW_DIR, load_secom  # noqa: E402
from secom.integrity import verify  # noqa: E402
from secom.models import MODELS, SEED  # noqa: E402
from secom.preprocess import MAX_MISSING_FRAC, QuantileBinner  # noqa: E402

MODEL_PARAMS = {
    "mspc": ("cpv", "alpha"),
    "iforest": ("n_estimators",),
    "l1lr": ("C", "l1_ratio", "solver", "class_weight", "max_iter"),
    "rf": ("n_estimators", "max_features", "min_samples_leaf", "class_weight"),
    "hgb": (
        "learning_rate",
        "max_iter",
        "max_leaf_nodes",
        "min_samples_leaf",
        "l2_regularization",
        "class_weight",
        "early_stopping",
    ),
}


def run_config() -> dict:
    models = {}
    for spec in MODELS:
        params = spec.make().get_params()
        models[spec.key] = {
            "label": spec.label,
            "fit_rows": "pass-only training rows" if spec.kind == "novelty" else "all training rows",
            "prebin": spec.prebin,
            "params": {k: params[k] for k in MODEL_PARAMS[spec.key]},
        }
    return {
        "max_missing_frac": MAX_MISSING_FRAC,
        "hgb_prebin_max_bins": QuantileBinner().max_bins,
        "holdout_train_frac": evaluate.HOLDOUT_TRAIN_FRAC,
        "n_blocks": evaluate.N_BLOCKS,
        "random_kfold_splits": evaluate.RANDOM_KFOLD_SPLITS,
        "random_kfold_repeats": evaluate.RANDOM_KFOLD_REPEATS,
        "random_holdout_repeats": evaluate.RANDOM_HOLDOUT_REPEATS,
        "time_knn_k": evaluate.TIME_KNN_K,
        "budgets_per_100": list(evaluate.BUDGETS),
        "chunk": evaluate.CHUNK,
        "importance_repeats": importance.N_REPEATS,
        "importance_models": list(importance.IMPORTANCE_KEYS),
        "top_k": list(importance.TOP_K),
        "omp_wait_policy": os.environ.get("OMP_WAIT_POLICY"),
        "models": models,
    }
from secom.splits import block_bounds  # noqa: E402


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return jsonable(obj.tolist())
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else float(obj)
    return obj


def mspc_test_far(fold: dict) -> dict:
    y = fold["y_test"].astype(bool)
    info = fold["info"]["mspc"]
    a_t2 = fold["scores"]["mspc_t2"] > info["t2_limit_99"]
    a_spe = fold["scores"]["mspc_spe"] > info["spe_limit_99"]
    either = a_t2 | a_spe
    return {
        "t2": float(np.mean(a_t2[~y])),
        "spe": float(np.mean(a_spe[~y])),
        "either": float(np.mean(either[~y])),
        "recall_either": float(np.mean(either[y])),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    ap.add_argument("--out-dir", type=Path, default=HERE)
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--n-perm", type=int, default=characterize.N_PERM)
    args = ap.parse_args(argv)
    t0 = time.time()
    out_dir = args.out_dir.resolve()
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    log("verify + load")
    summary = verify(args.raw_dir)
    d = load_secom(args.raw_dir)

    log("Q1 characterisation")
    char = characterize.run(d, n_perm=args.n_perm, seed=SEED)
    clusters = characterize.correlation_clusters(d.X, max_dist=0.3)

    log("Q2/Q3 evaluation")
    ev = evaluate.run(d, n_boot=args.n_boot, seed=SEED, log=log)
    schemes, evals, summaries = ev["schemes"], ev["evals"], ev["summaries"]
    char["drift"].update(
        {
            f"adversarial_auc_{k.split('_')[-1]}": v
            for k, v in evaluate.adversarial_validation(d, schemes["chrono_holdout"]).items()
        }
    )

    optimism = {}
    for key in evaluate.SCORE_KEYS:
        optimism[key] = {}
        for metric in ("roc_auc", "pr_auc", "recall_at_fpr05", "recall_at_fpr10"):
            g = {s: summaries[s][key]["mean"][metric] for s in summaries}
            optimism[key][metric] = {
                **g,
                "diff_cv": g["random_kfold"] - g["expanding"],
                "diff_holdout": g["random_holdout"] - g["chrono_holdout"],
            }

    optimism_summary = {}
    for metric in ("roc_auc", "pr_auc"):
        mk = evaluate.MAIN_KEYS
        optimism_summary[metric] = {
            "mean_diff_cv": float(np.mean([optimism[k][metric]["diff_cv"] for k in mk])),
            "mean_diff_holdout": float(np.mean([optimism[k][metric]["diff_holdout"] for k in mk])),
            "rank_random_kfold": sorted(mk, key=lambda k: -optimism[k][metric]["random_kfold"]),
            "rank_expanding": sorted(mk, key=lambda k: -optimism[k][metric]["expanding"]),
            "rank_random_holdout": sorted(mk, key=lambda k: -optimism[k][metric]["random_holdout"]),
            "rank_chrono_holdout": sorted(mk, key=lambda k: -optimism[k][metric]["chrono_holdout"]),
        }

    hold = evals["chrono_holdout"][0]
    accuracy_trap = {
        "test_prevalence": float(hold["y_test"].mean()),
        "rules": evaluate.default_threshold_table(hold),
    }
    mspc = {
        "holdout": hold["info"]["mspc"],
        "holdout_test_far": mspc_test_far(hold),
        "expanding": [f["info"]["mspc"] for f in evals["expanding"]],
        "expanding_test_far": [mspc_test_far(f) for f in evals["expanding"]],
        "fixed_limits_expanding": evaluate.mspc_fixed_limits(evals["expanding"]),
    }

    log("Q4 permutation importance + stability")
    names = d.feature_names
    imps = {k: [] for k in importance.IMPORTANCE_KEYS}
    for f, fold in enumerate(evals["expanding"]):
        te = fold["test_idx"]
        for i, k in enumerate(importance.IMPORTANCE_KEYS):
            imps[k].append(
                importance.permutation_importance(
                    fold["pipes"][k], d.X[te], d.y[te], importance.N_REPEATS, seed=SEED + 100 * f + i
                )
            )
    stab = {k: importance.model_stability(imps[k], clusters, names) for k in importance.IMPORTANCE_KEYS}
    selected = [f["info"]["l1lr_selected"] for f in evals["expanding"]]
    kept_full = int(np.sum(clusters < 10_000))
    imp_res = {
        "n_repeats": importance.N_REPEATS,
        "keys": list(importance.IMPORTANCE_KEYS),
        "cluster_cut": 0.3,
        "n_kept_full": kept_full,
        "n_clusters": int(np.unique(clusters[clusters < 10_000]).size),
        "models": stab,
        "l1lr_selection": {
            "n_selected": [len(s) for s in selected],
            "jaccard": importance.pairwise_jaccard(selected),
            "in_all_folds": [names[j] for j in importance.recurring(selected, len(selected))],
        },
        "univariate_filter": importance.univariate_filter_stability(d.X, d.y, schemes["expanding"], names, k=10),
        "halves": importance.halves(d.X, d.y),
        "_raw": imps,
    }

    log("Q5 alarm budget")
    budget = evaluate.alarm_budget(evals["expanding"])

    log("sensitivity")
    sens = evaluate.sensitivity(d, schemes)

    log("CI coverage simulation")
    coverage = simulate.run(seed=SEED)

    log("figures")
    bounds = block_bounds(d.ts, evaluate.N_BLOCKS)
    figs = {
        "fig1_missingness.png": "특성별 결측률과 시간에 따른 결측 위치",
        "fig2_fail_time.png": "주별 불량률(확장 창 블록 표시)과 불량 지시변수 자기상관",
        "fig3_eval_honesty.png": "무작위 CV vs 시간순 평가 (ROC-AUC, PR-AUC)",
        "fig4_folds.png": "확장 창 fold 별 ROC-AUC",
        "fig5_roc_pr.png": "시간순 70/30 시험 구간 ROC / PR 곡선",
        "fig6_mspc_chart.png": "시간순 시험 구간의 T² · SPE 관리도",
        "fig7_alarm_budget.png": "경보 예산별 잡은 불량 비율 vs 무작위 검사",
        "fig8_stability.png": "top-10 중요 특성의 fold 간 겹침, 앞/뒤 절반 단변량 AUC",
    }
    mf = char["missing"]["_feature_missing_frac"]
    plots.missingness(fig_dir / "fig1_missingness.png", mf, d.X, d.ts)
    plots.fail_time(
        fig_dir / "fig2_fail_time.png",
        char["weekly"],
        char["clustering"],
        d.ts,
        bounds[:-1],
        len(schemes["chrono_holdout"][0][0]),
    )
    plots.eval_honesty(
        fig_dir / "fig3_eval_honesty.png",
        summaries,
        list(evaluate.MAIN_KEYS) + ["time_knn"],
        {
            "chrono": summaries["expanding"]["l1lr"]["mean"]["ap_random"],
            "random": summaries["random_kfold"]["l1lr"]["mean"]["ap_random"],
        },
    )
    fold_info = [f["info"] for f in evals["expanding"]]
    plots.folds_auc(fig_dir / "fig4_folds.png", summaries["expanding"], list(evaluate.MAIN_KEYS), fold_info)
    plots.roc_pr(fig_dir / "fig5_roc_pr.png", hold["y_test"], hold["scores"], list(evaluate.MAIN_KEYS))
    plots.mspc_chart(fig_dir / "fig6_mspc_chart.png", hold, d.ts)
    plots.alarm_budget(fig_dir / "fig7_alarm_budget.png", budget, list(evaluate.MAIN_KEYS))
    plots.stability(fig_dir / "fig8_stability.png", stab, list(importance.IMPORTANCE_KEYS), imp_res["halves"])

    def info_only(info):
        return {k: v for k, v in info.items() if k not in ("mspc", "l1lr_selected")}

    splits = {
        "chrono_holdout": {**info_only(hold["info"]), "train_frac": evaluate.HOLDOUT_TRAIN_FRAC},
        "expanding": [info_only(f["info"]) for f in evals["expanding"]],
        "random_kfold": {
            "n_splits": evaluate.RANDOM_KFOLD_SPLITS,
            "repeats": evaluate.RANDOM_KFOLD_REPEATS,
            "seeds": f"{SEED}..{SEED + evaluate.RANDOM_KFOLD_REPEATS - 1}",
        },
        "random_holdout": {
            "repeats": evaluate.RANDOM_HOLDOUT_REPEATS,
            "n_test": len(schemes["random_holdout"][0][1]),
        },
    }

    versions = {"python": platform.python_version()}
    for pkg in ("numpy", "pandas", "scikit-learn", "scipy", "matplotlib"):
        versions[pkg] = metadata.version(pkg)

    results = {
        "meta": {
            "seed": SEED,
            "n_boot": args.n_boot,
            "n_perm": args.n_perm,
            "time_knn_k": evaluate.TIME_KNN_K,
            "n_null": evaluate.N_NULL,
            "versions": versions,
            "config": run_config(),
            "runtime_s": None,
        },
        "data": {
            "sha256": summary["sha256"],
            "n_features": summary["n_features"],
            **char,
        },
        "splits": splits,
        "evaluation": summaries,
        "optimism": optimism,
        "optimism_summary": optimism_summary,
        "accuracy_trap": accuracy_trap,
        "mspc": mspc,
        "importance": imp_res,
        "alarm_budget": budget,
        "sensitivity": sens,
        "ci_coverage": coverage,
        "figures": figs,
    }
    results["meta"]["runtime_s"] = time.time() - t0
    (out_dir / "results.json").write_text(
        json.dumps(jsonable(results), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (out_dir / "results.md").write_text(report.render(jsonable(results)), encoding="utf-8")
    log(f"done in {results['meta']['runtime_s']:.0f}s -> {out_dir / 'results.json'}, results.md, figures/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
