"""Leakage-safe preprocessing: every statistic is learned in `fit` (training rows only).

Pipeline: drop features (too sparse / constant on the training rows)
-> median imputation -> standardisation. Wrapped with the model in one sklearn
Pipeline, so cross-validation code can only ever call `fit` on training rows.
"""

from __future__ import annotations

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

MAX_MISSING_FRAC = 0.5  # fixed a priori, not tuned


class SparseConstantDropper(TransformerMixin, BaseEstimator):
    """Drop features whose training missing fraction > `max_missing` or that are
    constant (fewer than two distinct non-missing values) on the training rows."""

    def __init__(self, max_missing: float = MAX_MISSING_FRAC):
        self.max_missing = max_missing

    def fit(self, X, y=None):
        X = np.asarray(X, dtype=np.float64)
        nan = np.isnan(X)
        self.missing_frac_ = nan.mean(axis=0)
        n_distinct = np.empty(X.shape[1], dtype=np.int64)
        for j in range(X.shape[1]):
            col = X[~nan[:, j], j]
            n_distinct[j] = np.unique(col).size
        self.n_distinct_ = n_distinct
        self.keep_ = (self.missing_frac_ <= self.max_missing) & (n_distinct >= 2)
        if not self.keep_.any():
            raise ValueError("every feature was dropped")
        self.n_features_in_ = X.shape[1]
        return self

    def transform(self, X):
        X = np.asarray(X, dtype=np.float64)
        if X.shape[1] != self.n_features_in_:
            raise ValueError("feature count differs from fit")
        return X[:, self.keep_]


class QuantileBinner(TransformerMixin, BaseEstimator):
    """Map each feature to <= `max_bins` ordinal bins using training quantiles.

    Used in front of HistGradientBoosting: with class weights, sklearn 1.9 bins
    with a weighted percentile computed in a Python loop (~25 s per fit here).
    Pre-binned input has <= 255 distinct values per feature, so HGB's own binner
    takes its fast path and the candidate splits are the same as HGB's
    (unweighted) 255-quantile binning. Edges are learned from training rows only.
    """

    def __init__(self, max_bins: int = 255):
        self.max_bins = max_bins

    def fit(self, X, y=None):
        X = np.asarray(X, dtype=np.float64)
        q = np.linspace(0, 100, self.max_bins + 1)[1:-1]
        edges = []
        for j in range(X.shape[1]):
            distinct = np.unique(X[:, j])
            if distinct.size <= self.max_bins:
                e = (distinct[:-1] + distinct[1:]) / 2.0
            else:
                e = np.unique(np.percentile(X[:, j], q, method="midpoint"))
            edges.append(e)
        self.edges_ = edges
        self.n_features_in_ = X.shape[1]
        return self

    def transform(self, X):
        X = np.asarray(X, dtype=np.float64)
        out = np.empty_like(X)
        for j, e in enumerate(self.edges_):
            out[:, j] = np.searchsorted(e, X[:, j], side="left")
        return out


def make_preprocessor(max_missing: float = MAX_MISSING_FRAC) -> list[tuple[str, object]]:
    return [
        ("drop", SparseConstantDropper(max_missing=max_missing)),
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ]


def make_pipeline(model, max_missing: float = MAX_MISSING_FRAC, prebin: bool = False) -> Pipeline:
    steps = make_preprocessor(max_missing)
    if prebin:
        steps.append(("bin", QuantileBinner()))
    return Pipeline([*steps, ("model", model)])
