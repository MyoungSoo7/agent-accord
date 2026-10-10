"""Chronological split ordering (train strictly before test) and the random baselines."""

import unittest

import numpy as np

from secom.data import DEFAULT_RAW_DIR, load_secom
from secom.integrity import FILES
from secom.splits import (
    block_bounds,
    chronological_holdout,
    expanding_window_folds,
    random_stratified_holdouts,
    random_stratified_kfold,
)

HAVE_DATA = all((DEFAULT_RAW_DIR / f).is_file() for f in FILES)


def synthetic_ts(n=200, seed=0):
    rng = np.random.default_rng(seed)
    minutes = np.sort(rng.integers(0, 5000, size=n))  # many ties
    ts = np.datetime64("2008-07-19T00:00:00") + minutes.astype("timedelta64[m]")
    perm = rng.permutation(n)  # deliberately unsorted input
    return ts[perm].astype("datetime64[s]")


class ChronologicalTest(unittest.TestCase):
    def assertStrictlyBefore(self, ts, train, test):
        self.assertLess(ts[train].max(), ts[test].min())

    def test_holdout_order_and_partition(self):
        ts = synthetic_ts()
        (tr, te), = chronological_holdout(ts, 0.7)
        self.assertStrictlyBefore(ts, tr, te)
        self.assertEqual(len(np.intersect1d(tr, te)), 0)
        self.assertEqual(sorted(np.r_[tr, te].tolist()), list(range(len(ts))))
        self.assertAlmostEqual(len(tr) / len(ts), 0.7, delta=0.05)

    def test_cut_never_splits_tied_timestamps(self):
        ts = np.array(["2008-01-01T00:00"] * 3 + ["2008-01-02T00:00"] * 6 + ["2008-01-03T00:00"], dtype="datetime64[s]")
        (tr, te), = chronological_holdout(ts, 0.5)  # naive cut at 5 would split the run of 6
        self.assertStrictlyBefore(ts, tr, te)
        self.assertEqual(len(tr), 9)

    def test_expanding_window(self):
        ts = synthetic_ts(300, seed=1)
        folds = expanding_window_folds(ts, 6)
        self.assertEqual(len(folds), 5)
        prev_train = set()
        tested = []
        for tr, te in folds:
            self.assertStrictlyBefore(ts, tr, te)
            self.assertTrue(prev_train <= set(tr.tolist()), "training sets must be nested")
            prev_train = set(tr.tolist())
            tested.extend(te.tolist())
            # test block = everything between this fold's train end and the next block start
            self.assertEqual(len(np.intersect1d(tr, te)), 0)
        self.assertEqual(len(tested), len(set(tested)), "test blocks overlap")
        first_block = set(range(len(ts))) - set(tested)
        self.assertEqual(len(first_block), len(folds[0][0]))
        for (tr, te), (tr2, _) in zip(folds[:-1], folds[1:]):
            self.assertEqual(set(tr2.tolist()), set(tr.tolist()) | set(te.tolist()))
        b = block_bounds(ts, 6)
        self.assertEqual(b[0], 0)
        self.assertEqual(b[-1], len(ts))
        self.assertTrue(all(x < y for x, y in zip(b, b[1:])))

    @unittest.skipUnless(HAVE_DATA, "data/raw missing")
    def test_real_data_order(self):
        d = load_secom()
        (tr, te), = chronological_holdout(d.ts, 0.7)
        self.assertStrictlyBefore(d.ts, tr, te)
        for tr, te in expanding_window_folds(d.ts, 6):
            self.assertStrictlyBefore(d.ts, tr, te)


class RandomBaselineTest(unittest.TestCase):
    def test_kfold_partitions_and_stratifies(self):
        rng = np.random.default_rng(0)
        y = (rng.random(500) < 0.07).astype(int)
        folds = random_stratified_kfold(y, 5, n_repeats=2, seed=0)
        self.assertEqual(len(folds), 10)
        for r in range(2):
            tests = np.concatenate([te for _, te in folds[5 * r : 5 * r + 5]])
            self.assertEqual(sorted(tests.tolist()), list(range(500)))
        for tr, te in folds:
            self.assertLessEqual(abs(y[te].sum() - y.sum() / 5), 1)

    def test_random_holdouts_size(self):
        y = np.r_[np.ones(30), np.zeros(470)].astype(int)
        for tr, te in random_stratified_holdouts(y, test_size=150, n_repeats=3, seed=0):
            self.assertEqual(len(te), 150)
            self.assertEqual(len(np.intersect1d(tr, te)), 0)
            self.assertEqual(int(y[te].sum()), 9)


if __name__ == "__main__":
    unittest.main()
