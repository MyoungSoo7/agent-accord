"""How trustworthy are the bootstrap intervals at this study's test-set sizes?

Synthetic scores (Gaussian, fails shifted by `shift`) with the class counts of a
SECOM test block. The 'population' metrics come from one very large sample.
Reports interval coverage, the small-sample bias of average precision, and the
expected AP of a random ranking (which exceeds the prevalence when fails are few).
"""

from __future__ import annotations

import numpy as np

from .metrics import bootstrap_replicates, curve_metrics, percentile_ci, permutation_null

SETTINGS = ((261, 14), (470, 26))  # ~ expanding-window test block, ~ chronological 70/30 test
SHIFT = 0.4  # population ROC-AUC ~ 0.61, the range seen chronologically
REPS = 200
N_BOOT = 500


def ci_coverage(n: int, n_fail: int, shift: float = SHIFT, reps: int = REPS, n_boot: int = N_BOOT, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    big = 3000
    yb = np.r_[np.ones(n_fail * big, bool), np.zeros((n - n_fail) * big, bool)]
    pop = {k: float(v[0]) for k, v in curve_metrics(yb, rng.normal(size=yb.size) + shift * yb).items()}
    y = np.r_[np.ones(n_fail, bool), np.zeros(n - n_fail, bool)]
    hit = {"roc_auc": 0, "pr_auc": 0}
    sample = {"roc_auc": [], "pr_auc": []}
    for r in range(reps):
        s = rng.normal(size=n) + shift * y
        pm = curve_metrics(y, s)
        boot = bootstrap_replicates(y, s, n_boot, seed + r + 1)
        for k in hit:
            lo, hi = percentile_ci(boot[k])
            hit[k] += int(lo <= pop[k] <= hi)
            sample[k].append(float(pm[k][0]))
    ap_random = float(np.mean(permutation_null(y, rng.normal(size=n), 4000, seed + 999)["pr_auc"]))
    return {
        "n": n,
        "n_fail": n_fail,
        "prevalence": n_fail / n,
        "shift": shift,
        "reps": reps,
        "n_boot": n_boot,
        "population_auc": pop["roc_auc"],
        "population_ap": pop["pr_auc"],
        "mean_sample_auc": float(np.mean(sample["roc_auc"])),
        "mean_sample_ap": float(np.mean(sample["pr_auc"])),
        "coverage_auc": hit["roc_auc"] / reps,
        "coverage_ap": hit["pr_auc"] / reps,
        "ap_random_ranking": ap_random,
    }


def run(seed: int = 0) -> list[dict]:
    return [ci_coverage(n, f, seed=seed + 10 * i) for i, (n, f) in enumerate(SETTINGS)]
