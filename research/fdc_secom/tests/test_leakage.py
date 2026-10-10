"""No leakage: every preprocessing statistic comes from the training rows only."""

import unittest

import numpy as np
from sklearn.linear_model import LogisticRegression

from secom.importance import permutation_importance
from secom.models import MODEL_BY_KEY, MSPC, fail_score, final_score, fit_model
from secom.preprocess import QuantileBinner, SparseConstantDropper, make_pipeline


def make_data(seed=0):
    """Train and test drawn from different distributions, so any use of test
    rows would visibly change the fitted statistics."""
    rng = np.random.default_rng(seed)
    n_tr, n_te, p = 300, 120, 8
    X_tr = rng.normal(0.0, 1.0, size=(n_tr, p))
    X_te = rng.normal(100.0, 5.0, size=(n_te, p))
    X_tr[rng.random((n_tr, p)) < 0.1] = np.nan
    X_te[rng.random((n_te, p)) < 0.1] = np.nan
    X_tr[:, 5] = np.nan  # all-missing in train, complete in test
    X_te[:, 5] = rng.normal(size=n_te)
    X_tr[:, 6] = 3.0  # constant in train, varies in test
    X_te[:, 6] = rng.normal(size=n_te)
    X_tr[rng.random(n_tr) < 0.6, 7] = np.nan  # >50% missing in train only
    X_te[:, 7] = rng.normal(size=n_te)
    y_tr = (rng.random(n_tr) < 0.2).astype(int)
    y_te = (rng.random(n_te) < 0.2).astype(int)
    return X_tr, X_te, y_tr, y_te


class PreprocessingLeakageTest(unittest.TestCase):
    def setUp(self):
        self.X_tr, self.X_te, self.y_tr, self.y_te = make_data()
        self.pipe = make_pipeline(LogisticRegression()).fit(self.X_tr, self.y_tr)

    def test_dropper_uses_train_only(self):
        keep = self.pipe["drop"].keep_
        self.assertEqual(keep.tolist(), [True] * 5 + [False] * 3)
        # fitted on train+test it would keep the columns that only look fine in test
        both = SparseConstantDropper().fit(np.vstack([self.X_tr, self.X_te]))
        self.assertTrue(both.keep_[6])

    def test_imputer_statistics_equal_train_medians(self):
        keep = self.pipe["drop"].keep_
        expected = np.nanmedian(self.X_tr[:, keep], axis=0)
        np.testing.assert_allclose(self.pipe["impute"].statistics_, expected)
        leaky = np.nanmedian(np.vstack([self.X_tr, self.X_te])[:, keep], axis=0)
        self.assertFalse(np.allclose(self.pipe["impute"].statistics_, leaky))

    def test_scaler_statistics_equal_train_moments(self):
        keep = self.pipe["drop"].keep_
        Xk = self.X_tr[:, keep]
        imputed = np.where(np.isnan(Xk), np.nanmedian(Xk, axis=0), Xk)
        np.testing.assert_allclose(self.pipe["scale"].mean_, imputed.mean(axis=0))
        np.testing.assert_allclose(self.pipe["scale"].scale_, imputed.std(axis=0))

    def test_transform_does_not_refit(self):
        before = self.pipe["impute"].statistics_.copy()
        self.pipe.predict_proba(self.X_te)
        np.testing.assert_array_equal(self.pipe["impute"].statistics_, before)

    def test_binner_edges_from_train(self):
        rng = np.random.default_rng(1)
        a = rng.normal(size=(1000, 2))
        b = QuantileBinner(max_bins=16).fit(a)
        self.assertEqual(len(b.edges_[0]), 15)
        np.testing.assert_allclose(
            b.edges_[0], np.percentile(a[:, 0], np.linspace(0, 100, 17)[1:-1], method="midpoint")
        )
        out = b.transform(a + 100.0)  # far-away test values land in the top bin
        self.assertTrue(np.all(out == 15))


class FitModelLeakageTest(unittest.TestCase):
    """`fit_model` (used for every reported number) only touches training rows."""

    def setUp(self):
        X_tr, X_te, y_tr, y_te = make_data(2)
        self.X = np.vstack([X_tr, X_te])
        self.y = np.r_[y_tr, y_te]
        self.train_idx = np.arange(len(X_tr))

    def test_supervised_pipeline_train_only(self):
        pipe = fit_model(MODEL_BY_KEY["l1lr"], self.X, self.y, self.train_idx)
        keep = pipe["drop"].keep_
        Xk = self.X[self.train_idx][:, keep]
        np.testing.assert_allclose(pipe["impute"].statistics_, np.nanmedian(Xk, axis=0))

    def test_novelty_pipeline_pass_rows_only(self):
        for key in ("mspc", "iforest"):
            pipe = fit_model(MODEL_BY_KEY[key], self.X, self.y, self.train_idx)
            rows = self.train_idx[self.y[self.train_idx] == 0]
            keep = pipe["drop"].keep_
            np.testing.assert_allclose(
                pipe["impute"].statistics_, np.nanmedian(self.X[rows][:, keep], axis=0)
            )
            if key == "mspc":
                self.assertEqual(pipe[-1].n_samples_, len(rows))


class PermutationShortcutTest(unittest.TestCase):
    """Shuffling raw column j then running the whole pipeline == shuffling the
    transformed column (all preprocessing is column-wise)."""

    def test_equivalence(self):
        X_tr, X_te, y_tr, y_te = make_data(3)
        X = np.vstack([X_tr, X_te])
        y = np.r_[y_tr, y_te]
        X[:, :5] = np.random.default_rng(3).normal(size=(len(X), 5))  # same distribution
        X[np.random.default_rng(4).random(X.shape) < 0.1] = np.nan
        tr = np.arange(len(X_tr))
        te = np.arange(len(X_tr), len(X))
        rng = np.random.default_rng(5)
        for key in ("mspc", "hgb", "rf", "l1lr"):
            pipe = fit_model(MODEL_BY_KEY[key], X, y, tr)
            raw_cols = np.flatnonzero(pipe["drop"].keep_)
            Z = pipe[:-1].transform(X[te])
            for jj, j in enumerate(raw_cols[:3]):
                perm = rng.permutation(len(te))
                Xp = X[te].copy()
                Xp[:, j] = Xp[perm, j]
                Zp = Z.copy()
                Zp[:, jj] = Z[perm, jj]
                np.testing.assert_allclose(fail_score(pipe, Xp), final_score(pipe[-1], Zp), rtol=1e-12, atol=1e-12)

    def test_importance_zero_for_dropped_and_shape(self):
        X_tr, X_te, y_tr, y_te = make_data(6)
        X = np.vstack([X_tr, X_te])
        y = np.r_[y_tr, y_te]
        tr = np.arange(len(X_tr))
        pipe = fit_model(MODEL_BY_KEY["l1lr"], X, y, tr)
        res = permutation_importance(pipe, X_te, y_te, n_repeats=2, seed=0)
        self.assertEqual(res["mean"].shape, (X.shape[1],))
        np.testing.assert_array_equal(res["mean"][~pipe["drop"].keep_], 0.0)


class MSPCTest(unittest.TestCase):
    def test_limits_and_detection(self):
        rng = np.random.default_rng(0)
        A = rng.normal(size=(10, 30))
        noc = rng.normal(size=(3000, 10)) @ A + 0.5 * rng.normal(size=(3000, 30))
        mu, sd = noc.mean(0), noc.std(0)
        m = MSPC(cpv=0.9, alpha=0.01).fit((noc - mu) / sd)
        fresh = (rng.normal(size=(20000, 10)) @ A + 0.5 * rng.normal(size=(20000, 30)) - mu) / sd
        t2, spe = m.t2_spe(fresh)
        self.assertLess(abs(np.mean(t2 > m.t2_limit_) - 0.01), 0.006)
        self.assertLess(abs(np.mean(spe > m.spe_limit_) - 0.01), 0.006)
        shifted = fresh[:500].copy()
        shifted[:, 0] += 8.0  # break the correlation structure
        self.assertGreater(np.mean(m.predict(shifted)), 0.9)
        zc = fresh[:50] - m.mean_
        direct = np.sum((zc - (zc @ m.components_.T) @ m.components_) ** 2, axis=1)
        np.testing.assert_allclose(spe[:50], direct, rtol=1e-9)


if __name__ == "__main__":
    unittest.main()
