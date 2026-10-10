"""Detectors and classifiers. Hyper-parameters are fixed a priori (not tuned on any test fold).

Novelty detectors (MSPC, Isolation Forest) are fitted on pass-only training
history; supervised models on all training rows with class weighting. Every
model exposes a "higher = more likely fail" score through `fail_score`.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy import stats
from sklearn.base import BaseEstimator
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from .preprocess import make_pipeline

SEED = 0
N_JOBS = 4


class MSPC(BaseEstimator):
    """PCA-based multivariate SPC on normal-operation (NOC) data.

    Hotelling T^2 on the first k principal components and SPE (Q) on the
    residual. k = smallest number of PCs whose cumulative explained variance
    reaches `cpv`. Limits at level 1-alpha:
      T^2: k(n-1)(n+1) / (n(n-k)) * F(1-alpha; k, n-k)  (new observations)
      SPE: Jackson & Mudholkar (1979) from the residual eigenvalues.
    `decision_function` = max(T^2 / T^2_lim, SPE / SPE_lim): either chart alarms.
    Input is expected to be centred/scaled on the same NOC rows (the pipeline does that).
    """

    def __init__(self, cpv: float = 0.90, alpha: float = 0.01):
        self.cpv = cpv
        self.alpha = alpha

    def fit(self, Z, y=None):
        Z = np.asarray(Z, dtype=np.float64)
        n, p = Z.shape
        if n < 3:
            raise ValueError("MSPC needs at least 3 NOC rows")
        self.mean_ = Z.mean(axis=0)
        _, s, vt = np.linalg.svd(Z - self.mean_, full_matrices=False)
        eig = s**2 / (n - 1)
        frac = np.cumsum(eig) / eig.sum()
        k = int(np.searchsorted(frac, self.cpv - 1e-12) + 1)
        k = max(1, min(k, n - 2, p - 1))
        self.n_samples_, self.n_components_ = n, k
        self.eigenvalues_ = eig
        self.components_ = vt[:k]
        self.explained_ = float(frac[k - 1])
        self.t2_limit_ = self.t2_limit(self.alpha)
        self.spe_limit_ = self.spe_limit(self.alpha)
        return self

    def t2_limit(self, alpha: float) -> float:
        n, k = self.n_samples_, self.n_components_
        return float(k * (n - 1) * (n + 1) / (n * (n - k)) * stats.f.ppf(1 - alpha, k, n - k))

    def spe_limit(self, alpha: float) -> float:
        res = self.eigenvalues_[self.n_components_ :]
        th1, th2, th3 = (float(np.sum(res**i)) for i in (1, 2, 3))
        if th1 <= 0:
            return 0.0
        h0 = 1.0 - 2.0 * th1 * th3 / (3.0 * th2**2)
        if h0 <= 0:  # fall back to Box (1954): SPE ~ g chi2_h
            g, h = th2 / th1, th1**2 / th2
            return float(g * stats.chi2.ppf(1 - alpha, h))
        c = stats.norm.ppf(1 - alpha)
        inner = c * np.sqrt(2 * th2 * h0**2) / th1 + 1 + th2 * h0 * (h0 - 1) / th1**2
        return float(th1 * inner ** (1.0 / h0))

    def t2_spe(self, Z) -> tuple[np.ndarray, np.ndarray]:
        zc = np.asarray(Z, dtype=np.float64) - self.mean_
        t = zc @ self.components_.T
        t2 = np.sum(t**2 / self.eigenvalues_[: self.n_components_], axis=1)
        # loadings are orthonormal, so ||z - t P||^2 = ||z||^2 - ||t||^2
        spe = np.maximum(np.einsum("ij,ij->i", zc, zc) - np.einsum("ij,ij->i", t, t), 0.0)
        return t2, spe

    def t2(self, Z) -> np.ndarray:
        return self.t2_spe(Z)[0]

    def spe(self, Z) -> np.ndarray:
        return self.t2_spe(Z)[1]

    def decision_function(self, Z) -> np.ndarray:
        t2, spe = self.t2_spe(Z)
        return np.maximum(t2 / self.t2_limit_, spe / self.spe_limit_)

    def predict(self, Z) -> np.ndarray:
        return (self.decision_function(Z) > 1.0).astype(np.int8)


class IForestNovelty(BaseEstimator):
    """Isolation Forest fitted on NOC rows; decision_function: higher = more anomalous."""

    def __init__(self, n_estimators: int = 500, random_state: int = SEED, n_jobs: int = N_JOBS):
        self.n_estimators = n_estimators
        self.random_state = random_state
        self.n_jobs = n_jobs

    def fit(self, Z, y=None):
        self.forest_ = IsolationForest(
            n_estimators=self.n_estimators,
            max_samples="auto",
            contamination="auto",
            random_state=self.random_state,
            n_jobs=self.n_jobs,
        ).fit(np.asarray(Z, dtype=np.float64))
        return self

    def decision_function(self, Z) -> np.ndarray:
        return -self.forest_.score_samples(np.asarray(Z, dtype=np.float64))

    def predict(self, Z) -> np.ndarray:
        # sklearn's own threshold for contamination="auto": score_samples < -0.5
        return (self.decision_function(Z) > 0.5).astype(np.int8)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    kind: str  # "novelty" (fit on pass-only training rows) | "supervised"
    make: Callable[[], BaseEstimator]
    prebin: bool = False  # quantile pre-binning (HGB only, see preprocess.QuantileBinner)


def l1_logreg(C: float = 0.1) -> LogisticRegression:
    return LogisticRegression(
        l1_ratio=1.0,
        C=C,
        solver="liblinear",
        class_weight="balanced",
        max_iter=5000,
        random_state=SEED,
    )


MODELS: tuple[ModelSpec, ...] = (
    ModelSpec("mspc", "MSPC (PCA T²+SPE)", "novelty", lambda: MSPC(cpv=0.90, alpha=0.01)),
    ModelSpec("iforest", "Isolation Forest", "novelty", lambda: IForestNovelty()),
    ModelSpec("l1lr", "L1 logistic", "supervised", lambda: l1_logreg(0.1)),
    ModelSpec(
        "rf",
        "Random forest",
        "supervised",
        lambda: RandomForestClassifier(
            n_estimators=500,
            max_features="sqrt",
            class_weight="balanced_subsample",
            n_jobs=N_JOBS,
            random_state=SEED,
        ),
    ),
    ModelSpec(
        "hgb",
        "HistGradientBoosting",
        "supervised",
        lambda: HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=200,
            max_leaf_nodes=15,
            min_samples_leaf=20,
            l2_regularization=1.0,
            class_weight="balanced",
            early_stopping=False,
            random_state=SEED,
        ),
        prebin=True,
    ),
)
MODEL_BY_KEY = {m.key: m for m in MODELS}


def fit_model(spec: ModelSpec, X: np.ndarray, y: np.ndarray, train_idx: np.ndarray) -> Pipeline:
    """Fit preprocessing + model on training rows only (pass-only rows for novelty models)."""
    pipe = make_pipeline(spec.make(), prebin=spec.prebin)
    train_idx = np.asarray(train_idx)
    if spec.kind == "novelty":
        rows = train_idx[np.asarray(y)[train_idx] == 0]
        pipe.fit(X[rows])
    else:
        pipe.fit(X[train_idx], np.asarray(y)[train_idx])
    return pipe


def _rf_fail_proba(rf: RandomForestClassifier, Z: np.ndarray, n_jobs: int = N_JOBS) -> np.ndarray:
    """RandomForest P(fail) with a fixed summation order.

    sklearn's predict_proba adds per-tree outputs under a lock in thread
    completion order when n_jobs > 1, so the float sum (and hence score ties)
    can change from run to run. Here trees are split into fixed groups, each
    summed sequentially, and the group sums are added in a fixed order.
    """
    Z32 = np.ascontiguousarray(Z, dtype=np.float32)
    col = int(np.flatnonzero(rf.classes_ == 1)[0])
    groups = np.array_split(np.arange(len(rf.estimators_)), n_jobs)

    def part(idx):
        acc = np.zeros(Z32.shape[0])
        for t in idx:
            acc += rf.estimators_[t].predict_proba(Z32, check_input=False)[:, col]
        return acc

    with ThreadPoolExecutor(max_workers=n_jobs) as ex:
        parts = list(ex.map(part, groups))
    total = np.zeros(Z32.shape[0])
    for p in parts:
        total += p
    return total / len(rf.estimators_)


def final_score(model, Z: np.ndarray) -> np.ndarray:
    """Score already-preprocessed rows with the last pipeline step. Higher = more likely fail."""
    if isinstance(model, RandomForestClassifier):
        return _rf_fail_proba(model, Z)
    if hasattr(model, "predict_proba"):
        col = int(np.flatnonzero(model.classes_ == 1)[0])
        return model.predict_proba(Z)[:, col]
    return model.decision_function(Z)


def fail_score(pipe: Pipeline, X: np.ndarray) -> np.ndarray:
    """Higher = more likely fail."""
    return final_score(pipe[-1], pipe[:-1].transform(X))
