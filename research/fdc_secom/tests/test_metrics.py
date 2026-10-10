"""Metric helpers: recall@FPR, AUC/AP vs sklearn, bootstrap, permutation null, alarm budget."""

import unittest

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from secom import metrics as M
from secom.models import _rf_fail_proba


class RecallAtFprTest(unittest.TestCase):
    def test_hand_computed(self):
        # ranked by score: P N P P N N ... (20 negatives, 4 positives)
        y = np.array([1, 0, 1, 1] + [0] * 18 + [1, 0])
        s = np.arange(len(y), 0, -1, dtype=float)  # strictly decreasing, no ties
        # FPR <= 5%: at most 1 of 20 negatives -> threshold below 4th item: 3 of 4 positives
        self.assertAlmostEqual(M.recall_at_fpr(y, s, 0.05), 0.75)
        # FPR <= 10%: 2 negatives allowed -> still 3/4 (the 4th positive is behind 19 negatives)
        self.assertAlmostEqual(M.recall_at_fpr(y, s, 0.10), 0.75)
        # FPR < 1/20: no negative allowed -> only the first positive
        self.assertAlmostEqual(M.recall_at_fpr(y, s, 0.04), 0.25)

    def test_ties_count_as_one_threshold(self):
        # a positive tied with two negatives at the top: taking it costs 2 FPs (10% of 20)
        y = np.array([1, 0, 0, 1] + [0] * 18)
        s = np.array([5.0, 5.0, 5.0, 4.0] + [1.0] * 18)
        self.assertAlmostEqual(M.recall_at_fpr(y, s, 0.05), 0.0)
        self.assertAlmostEqual(M.recall_at_fpr(y, s, 0.10), 1.0)

    def test_perfect_and_constant(self):
        y = np.array([1, 1, 0, 0, 0])
        self.assertEqual(M.recall_at_fpr(y, np.array([3.0, 2, 1, 0, -1]), 0.0), 1.0)
        self.assertEqual(M.recall_at_fpr(y, np.zeros(5), 0.5), 0.0)  # all-or-nothing threshold


class CurveMetricsTest(unittest.TestCase):
    def test_matches_sklearn_with_ties(self):
        rng = np.random.default_rng(0)
        for _ in range(200):
            n = int(rng.integers(10, 300))
            y = rng.random(n) < rng.uniform(0.03, 0.5)
            if y.all() or (~y).all():
                continue
            s = np.round(rng.normal(size=n) + y * rng.uniform(0, 2), int(rng.integers(0, 3)))
            m = M.point_metrics(y, s)
            self.assertAlmostEqual(m["roc_auc"], roc_auc_score(y, s), places=12)
            self.assertAlmostEqual(m["pr_auc"], average_precision_score(y, s), places=12)

    def test_weights_equal_duplication(self):
        rng = np.random.default_rng(1)
        y = rng.random(60) < 0.2
        s = rng.normal(size=60)
        w = rng.integers(0, 3, size=60).astype(float)
        if not (w[y].sum() and w[~y].sum()):
            w[:] = 1.0
        dup = np.repeat(np.arange(60), w.astype(int))
        a = M.curve_metrics(y, s, w[None, :])
        b = M.curve_metrics(y[dup], s[dup])
        for k in M.METRIC_KEYS:
            self.assertAlmostEqual(float(a[k][0]), float(b[k][0]), places=12)

    def test_input_checks(self):
        with self.assertRaises(ValueError):
            M.point_metrics(np.zeros(5), np.arange(5.0))
        with self.assertRaises(ValueError):
            M.point_metrics(np.array([0, 1]), np.array([np.nan, 1.0]))


class BootstrapAndNullTest(unittest.TestCase):
    def test_stratified_weights_keep_class_counts(self):
        y = np.r_[np.ones(7), np.zeros(93)].astype(bool)
        W = M.stratified_bootstrap_weights(y, 50, np.random.default_rng(0))
        np.testing.assert_array_equal(W[:, y].sum(axis=1), 7)
        np.testing.assert_array_equal(W[:, ~y].sum(axis=1), 93)

    def test_deterministic_and_brackets_estimate(self):
        rng = np.random.default_rng(2)
        y = rng.random(400) < 0.08
        s = rng.normal(size=400) + y
        a = M.summarize_folds([(y, s)], n_boot=300, seed=7, n_null=200)
        b = M.summarize_folds([(y, s)], n_boot=300, seed=7, n_null=200)
        self.assertEqual(a["mean_ci"], b["mean_ci"])
        lo, hi = a["per_fold"][0]["ci"]["roc_auc"]
        self.assertLess(lo, a["per_fold"][0]["roc_auc"])
        self.assertGreater(hi, a["per_fold"][0]["roc_auc"])
        self.assertLess(a["mean"]["p_auc"], 0.01)

    def test_random_ranking_ap_exceeds_prevalence_with_few_fails(self):
        y = np.r_[np.ones(14), np.zeros(247)].astype(bool)
        s = np.random.default_rng(3).normal(size=len(y))
        null = M.permutation_null(y, s, 3000, seed=0)
        self.assertAlmostEqual(float(np.mean(null["roc_auc"])), 0.5, delta=0.01)
        self.assertGreater(float(np.mean(null["pr_auc"])), y.mean() + 0.01)


class AlarmBudgetTest(unittest.TestCase):
    def test_chunks_respect_blocks(self):
        blocks = np.r_[np.zeros(250), np.ones(130)].astype(int)
        c = M.make_chunks(blocks, 100)
        sizes = np.bincount(c)
        self.assertEqual(sizes.tolist(), [100, 100, 50, 100, 30])

    def test_top_k_and_random(self):
        y = np.zeros(200, dtype=bool)
        y[[5, 150]] = True
        s = np.zeros(200)
        s[[5, 150]] = 10.0
        chunks = M.make_chunks(np.zeros(200, dtype=int), 100)
        alarm = M.top_k_per_chunk(s, 2, chunks)
        self.assertEqual(int(alarm.sum()), 4)
        self.assertTrue(alarm[5] and alarm[150])
        rnd = M.random_inspection(y, 2, chunks, n_sim=5000, seed=0)
        self.assertAlmostEqual(rnd["expected"], 2 * 1 / 100 * 2)
        self.assertEqual(rnd["n_alarm"], 4)

    def test_majority_trap(self):
        y = np.r_[np.ones(7), np.zeros(93)].astype(bool)
        cm = M.confusion_at(y, np.zeros(100, dtype=bool))
        self.assertAlmostEqual(cm["accuracy"], 0.93)
        self.assertEqual(cm["recall"], 0.0)
        self.assertAlmostEqual(cm["balanced_accuracy"], 0.5)


class DeterministicForestTest(unittest.TestCase):
    def test_rf_score_matches_sklearn_and_is_reproducible(self):
        rng = np.random.default_rng(0)
        X = rng.normal(size=(300, 6))
        y = (X[:, 0] + rng.normal(size=300) > 1).astype(int)
        rf = RandomForestClassifier(n_estimators=60, n_jobs=4, random_state=0).fit(X, y)
        a = _rf_fail_proba(rf, X)
        b = _rf_fail_proba(rf, X)
        np.testing.assert_array_equal(a, b)
        np.testing.assert_allclose(a, rf.predict_proba(X)[:, 1], atol=1e-12)


if __name__ == "__main__":
    unittest.main()
