"""Q2/Q3/Q5: fit every model on every split, collect out-of-sample scores and metrics.

Every fit goes through `models.fit_model`, i.e. one sklearn Pipeline fitted on
training rows only (pass-only training rows for the novelty detectors).
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import RandomForestClassifier

from . import metrics as M
from .data import SecomData
from .models import MODELS, MODEL_BY_KEY, SEED, MSPC, fail_score, final_score, fit_model, l1_logreg
from .preprocess import make_pipeline
from .splits import (
    chronological_holdout,
    expanding_window_folds,
    random_stratified_holdouts,
    random_stratified_kfold,
)

HOLDOUT_TRAIN_FRAC = 0.7
N_BLOCKS = 6
RANDOM_KFOLD_SPLITS = 5
RANDOM_KFOLD_REPEATS = 2
RANDOM_HOLDOUT_REPEATS = 5
TIME_KNN_K = 50
BUDGETS = (1, 2, 5, 10, 20)  # alarms per 100 consecutive production entities
CHUNK = 100
N_NULL = 1000  # label shuffles per test fold (random-ranking baseline, p-values)
SENS_C = (0.01, 0.03, 0.1, 0.3, 1.0)
SENS_CPV = (0.5, 0.7, 0.8, 0.9, 0.95)

SCORE_KEYS = ("mspc", "mspc_t2", "mspc_spe", "iforest", "l1lr", "rf", "hgb", "time_knn")
SCORE_LABELS = {
    "mspc": "MSPC (T² or SPE)",
    "mspc_t2": "MSPC T² only",
    "mspc_spe": "MSPC SPE only",
    "iforest": "Isolation Forest",
    "l1lr": "L1 logistic",
    "rf": "Random forest",
    "hgb": "HistGradientBoosting",
    "time_knn": "time only (k-NN in time)",
}
MAIN_KEYS = ("mspc", "iforest", "l1lr", "rf", "hgb")


def make_schemes(d: SecomData) -> dict[str, list]:
    holdout = chronological_holdout(d.ts, HOLDOUT_TRAIN_FRAC)
    test_size = len(holdout[0][1])
    return {
        "chrono_holdout": holdout,
        "expanding": expanding_window_folds(d.ts, N_BLOCKS),
        "random_kfold": random_stratified_kfold(d.y, RANDOM_KFOLD_SPLITS, RANDOM_KFOLD_REPEATS, SEED),
        "random_holdout": random_stratified_holdouts(d.y, test_size, RANDOM_HOLDOUT_REPEATS, SEED),
    }


def time_knn_scores(ts, y, train_idx, test_idx, k: int = TIME_KNN_K) -> np.ndarray:
    """Fail rate of the k training entities closest in time (uses only the timestamp).

    Under a chronological split every test entity gets the same k most recent
    training entities, so the score is constant (AUC = 0.5 by construction).
    """
    t = ts.astype("datetime64[s]").astype(np.int64).astype(np.float64)
    t_tr, y_tr = t[train_idx], np.asarray(y)[train_idx].astype(np.float64)
    out = np.empty(len(test_idx))
    for i, j in enumerate(test_idx):
        dist = np.abs(t_tr - t[j])
        nearest = np.argpartition(dist, k - 1)[:k]
        out[i] = y_tr[nearest].mean()
    return out


def _period(ts, idx) -> list[str]:
    t = ts[idx]
    return [str(t.min())[:10], str(t.max())[:10]]


def evaluate_scheme(d: SecomData, folds, keep_pipes: bool = False) -> list[dict]:
    out = []
    for tr, te in folds:
        fold = {
            "train_idx": tr,
            "test_idx": te,
            "y_test": d.y[te],
            "scores": {},
            "pipes": {},
            "info": {
                "n_train": int(len(tr)),
                "n_test": int(len(te)),
                "train_fails": int(d.y[tr].sum()),
                "test_fails": int(d.y[te].sum()),
                "train_period": _period(d.ts, tr),
                "test_period": _period(d.ts, te),
            },
        }
        for spec in MODELS:
            pipe = fit_model(spec, d.X, d.y, tr)
            Z = pipe[:-1].transform(d.X[te])
            fold["scores"][spec.key] = final_score(pipe[-1], Z)
            fold["info"].setdefault("n_features_kept", {})[spec.key] = int(pipe["drop"].keep_.sum())
            if spec.key == "mspc":
                m: MSPC = pipe[-1]
                t2, spe = m.t2_spe(Z)
                fold["scores"]["mspc_t2"] = t2
                fold["scores"]["mspc_spe"] = spe
                noc = tr[d.y[tr] == 0]
                t2_tr, spe_tr = m.t2_spe(pipe[:-1].transform(d.X[noc]))
                fold["info"]["mspc"] = {
                    "n_noc": int(m.n_samples_),
                    "n_components": int(m.n_components_),
                    "explained": float(m.explained_),
                    "t2_limit_99": m.t2_limit_,
                    "spe_limit_99": m.spe_limit_,
                    "t2_limit_95": m.t2_limit(0.05),
                    "spe_limit_95": m.spe_limit(0.05),
                    "train_alarm_t2_99": float(np.mean(t2_tr > m.t2_limit_)),
                    "train_alarm_spe_99": float(np.mean(spe_tr > m.spe_limit_)),
                    "train_alarm_either_99": float(
                        np.mean((t2_tr > m.t2_limit_) | (spe_tr > m.spe_limit_))
                    ),
                }
            if spec.key == "l1lr":
                coef = pipe[-1].coef_[0]
                raw = np.flatnonzero(pipe["drop"].keep_)
                fold["info"]["l1lr_selected"] = [int(j) for j in raw[coef != 0]]
            if keep_pipes:
                fold["pipes"][spec.key] = pipe
        fold["scores"]["time_knn"] = time_knn_scores(d.ts, d.y, tr, te)
        out.append(fold)
    return out


def summarize_scheme(folds: list[dict], n_boot: int, seed: int, n_null: int = N_NULL) -> dict:
    res = {}
    for i, key in enumerate(SCORE_KEYS):
        data = [(f["y_test"], f["scores"][key]) for f in folds]
        res[key] = M.summarize_folds(data, n_boot=n_boot, seed=seed + 100_000 * i, n_null=n_null)
    return res


def default_threshold_table(fold: dict) -> dict:
    """Accuracy trap: each model at its out-of-the-box decision rule."""
    y = fold["y_test"]
    s = fold["scores"]
    rules = {
        "majority (all pass)": np.zeros(len(y), dtype=bool),
        "MSPC 99% limits": s["mspc"] > 1.0,
        "Isolation Forest (sklearn 'auto')": s["iforest"] > 0.5,
        "L1 logistic p>=0.5": s["l1lr"] >= 0.5,
        "Random forest p>=0.5": s["rf"] >= 0.5,
        "HistGradientBoosting p>=0.5": s["hgb"] >= 0.5,
    }
    return {name: M.confusion_at(y, alarm) for name, alarm in rules.items()}


def mspc_fixed_limits(folds: list[dict]) -> dict:
    """Classic MSPC deployment: alarm when T² or SPE exceeds its theoretical limit."""
    out = {}
    for level, t2k, spek in (("99", "t2_limit_99", "spe_limit_99"), ("95", "t2_limit_95", "spe_limit_95")):
        ys, al, al_t2, al_spe = [], [], [], []
        for f in folds:
            info = f["info"]["mspc"]
            a_t2 = f["scores"]["mspc_t2"] > info[t2k]
            a_spe = f["scores"]["mspc_spe"] > info[spek]
            ys.append(f["y_test"])
            al.append(a_t2 | a_spe)
            al_t2.append(a_t2)
            al_spe.append(a_spe)
        y = np.concatenate(ys)
        res = M.confusion_at(y, np.concatenate(al))
        res["false_alarm_rate_t2"] = M.confusion_at(y, np.concatenate(al_t2))["false_alarm_rate"]
        res["false_alarm_rate_spe"] = M.confusion_at(y, np.concatenate(al_spe))["false_alarm_rate"]
        res["nominal_false_alarm_rate_per_chart"] = 1 - int(level) / 100
        out[level] = res
    return out


def alarm_budget(folds: list[dict], seed: int = SEED) -> dict:
    """Q5: fixed alarm budget per 100 consecutive entities on the chronological test blocks."""
    y = np.concatenate([f["y_test"] for f in folds])
    blocks = np.concatenate([np.full(len(f["y_test"]), i) for i, f in enumerate(folds)])
    chunks = M.make_chunks(blocks, CHUNK)
    n_fail = int(y.sum())
    out = {"n_entities": int(len(y)), "n_fail": n_fail, "n_chunks": int(chunks.max() + 1), "budgets": {}}
    for b in BUDGETS:
        rnd = M.random_inspection(y, b, chunks, seed=seed + b)
        row = {"random": rnd, "models": {}}
        for key in MAIN_KEYS:
            s = np.concatenate([f["scores"][key] for f in folds])
            alarm = M.top_k_per_chunk(s, b, chunks, seed=seed + b)
            caught = int(np.sum(alarm & (y == 1)))
            row["models"][key] = {
                "caught": caught,
                "n_alarm": int(alarm.sum()),
                "recall": caught / n_fail,
                "lift_vs_random": caught / rnd["expected"] if rnd["expected"] > 0 else float("nan"),
                "above_random_hi": bool(caught > rnd["hi"]),
            }
        out["budgets"][str(b)] = row
    return out


def sensitivity(d: SecomData, schemes: dict, n_folds_random: int = RANDOM_KFOLD_SPLITS) -> dict:
    """Mean ROC-AUC / PR-AUC over folds for alternative hyper-parameters.
    Reported only; the main analysis keeps the a-priori values (C=0.1, CPV=0.9)."""
    folds_by = {
        "expanding": schemes["expanding"],
        "random_kfold": schemes["random_kfold"][:n_folds_random],
    }
    out = {"l1lr_C": {}, "mspc_cpv": {}}
    for name, folds in folds_by.items():
        for C in SENS_C:
            aucs, aps = [], []
            for tr, te in folds:
                pipe = make_pipeline(l1_logreg(C)).fit(d.X[tr], d.y[tr])
                m = M.point_metrics(d.y[te], fail_score(pipe, d.X[te]))
                aucs.append(m["roc_auc"])
                aps.append(m["pr_auc"])
            out["l1lr_C"].setdefault(name, {})[f"{C:g}"] = {
                "roc_auc": float(np.mean(aucs)),
                "pr_auc": float(np.mean(aps)),
            }
        for cpv in SENS_CPV:
            aucs, aps, ks = [], [], []
            for tr, te in folds:
                noc = tr[d.y[tr] == 0]
                pipe = make_pipeline(MSPC(cpv=cpv, alpha=0.01)).fit(d.X[noc])
                m = M.point_metrics(d.y[te], fail_score(pipe, d.X[te]))
                aucs.append(m["roc_auc"])
                aps.append(m["pr_auc"])
                ks.append(pipe[-1].n_components_)
            out["mspc_cpv"].setdefault(name, {})[f"{cpv:g}"] = {
                "roc_auc": float(np.mean(aucs)),
                "pr_auc": float(np.mean(aps)),
                "mean_n_components": float(np.mean(ks)),
            }
    return out


def adversarial_validation(d: SecomData, holdout) -> dict:
    """Covariate shift: can the features tell the chronological test period from training?
    Random stratified 5-fold CV of a random forest on 'is in test period'."""
    tr, te = holdout[0]
    period = np.zeros(d.n, dtype=np.int8)
    period[te] = 1
    aucs = []
    for f_tr, f_te in random_stratified_kfold(period, 5, 1, SEED):
        pipe = make_pipeline(
            RandomForestClassifier(n_estimators=200, n_jobs=4, random_state=SEED)
        ).fit(d.X[f_tr], period[f_tr])
        aucs.append(M.point_metrics(period[f_te], fail_score(pipe, d.X[f_te]))["roc_auc"])
    return {"roc_auc_mean": float(np.mean(aucs)), "roc_auc_min": float(np.min(aucs))}


def run(d: SecomData, n_boot: int = 2000, seed: int = SEED, log=print) -> dict:
    schemes = make_schemes(d)
    evals = {}
    for name, folds in schemes.items():
        keep = name in ("chrono_holdout", "expanding")
        log(f"  fitting {len(folds)} fold(s) x {len(MODELS)} models: {name}")
        evals[name] = evaluate_scheme(d, folds, keep_pipes=keep)
    log("  bootstrap CIs + label-permutation nulls")
    summaries = {name: summarize_scheme(f, n_boot, seed + 10 * i) for i, (name, f) in enumerate(evals.items())}
    return {"schemes": schemes, "evals": evals, "summaries": summaries}


__all__ = [
    "BUDGETS",
    "MAIN_KEYS",
    "MODEL_BY_KEY",
    "SCORE_KEYS",
    "SCORE_LABELS",
    "adversarial_validation",
    "alarm_budget",
    "default_threshold_table",
    "evaluate_scheme",
    "make_schemes",
    "mspc_fixed_limits",
    "run",
    "sensitivity",
    "summarize_scheme",
    "time_knn_scores",
]
