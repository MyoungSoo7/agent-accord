"""Loader, hash and shape checks."""

import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

import fetch_data
from secom.data import DEFAULT_RAW_DIR, load_secom
from secom.integrity import (
    EXPECTED_SHA256,
    FILES,
    IntegrityError,
    check_hashes,
    check_structure,
    parse_label_line,
    verify,
)

HAVE_DATA = all((DEFAULT_RAW_DIR / f).is_file() for f in FILES)


@unittest.skipUnless(HAVE_DATA, "data/raw missing: run fetch_data.py first")
class RealDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = load_secom()

    def test_hashes(self):
        self.assertEqual(check_hashes(DEFAULT_RAW_DIR), EXPECTED_SHA256)

    def test_structure(self):
        s = verify(DEFAULT_RAW_DIR)
        self.assertEqual((s["n_rows"], s["n_features"]), (1567, 590))
        self.assertEqual(s["label_counts"], {"-1": 1463, "1": 104})
        self.assertEqual(s["n_missing_cells"], 41951)

    def test_loader(self):
        d = self.d
        self.assertEqual(d.X.shape, (1567, 590))
        self.assertEqual(int(d.y.sum()), 104)
        self.assertEqual(int(np.isnan(d.X).sum()), 41951)
        self.assertTrue(np.all(np.diff(d.ts.astype(np.int64)) >= 0), "not time-sorted")
        self.assertEqual(sorted(d.row_id.tolist()), list(range(1567)))
        self.assertEqual(d.feature_names[0], "f000")
        self.assertEqual(d.feature_names[-1], "f589")

    def test_loader_rejects_modified_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            for f in FILES:
                shutil.copy(DEFAULT_RAW_DIR / f, Path(tmp) / f)
            path = Path(tmp) / "secom.data"
            blob = bytearray(path.read_bytes())
            blob[10] = ord("9") if blob[10] != ord("9") else ord("8")
            path.write_bytes(bytes(blob))
            with self.assertRaises(IntegrityError):
                check_hashes(tmp)
            with self.assertRaises(IntegrityError):
                load_secom(tmp)

    def test_fetch_uses_present_files_and_refuses_bad_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            for f in FILES:
                shutil.copy(DEFAULT_RAW_DIR / f, raw / f)
            quiet = io.StringIO()
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                self.assertEqual(fetch_data.fetch(raw), 0)  # no network needed
                (raw / "secom.names").write_text("tampered\n")
                self.assertEqual(fetch_data.fetch(raw), 1)
            self.assertEqual((raw / "secom.names").read_text(), "tampered\n")  # not overwritten


class StructureCheckTest(unittest.TestCase):
    def _write(self, tmp, data_lines, label_lines):
        Path(tmp, "secom.data").write_text("\n".join(data_lines) + "\n")
        Path(tmp, "secom_labels.data").write_text("\n".join(label_lines) + "\n")

    def test_good_small_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                tmp,
                ["1 2 NaN", "4 5 6"],
                ['-1 "19/07/2008 11:55:00"', '1 "19/07/2008 12:32:00"'],
            )
            s = check_structure(tmp, n_rows=2, n_features=3, label_counts={-1: 1, 1: 1})
            self.assertEqual(s["n_missing_cells"], 1)

    def test_wrong_field_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, ["1 2", "4 5 6"], ['-1 "19/07/2008 11:55:00"', '1 "19/07/2008 12:32:00"'])
            with self.assertRaises(IntegrityError):
                check_structure(tmp, n_rows=2, n_features=3, label_counts={-1: 1, 1: 1})

    def test_non_numeric_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, ["1 2 __import__", "4 5 6"], ['-1 "19/07/2008 11:55:00"', '1 "19/07/2008 12:32:00"'])
            with self.assertRaises(IntegrityError):
                check_structure(tmp, n_rows=2, n_features=3, label_counts={-1: 1, 1: 1})

    def test_label_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, ["1 2 3", "4 5 6"], ['-1 "19/07/2008 11:55:00"', '-1 "19/07/2008 12:32:00"'])
            with self.assertRaises(IntegrityError):
                check_structure(tmp, n_rows=2, n_features=3, label_counts={-1: 1, 1: 1})

    def test_label_line_parser(self):
        lab, ts = parse_label_line('1 "17/10/2008 06:07:00"\r\n')
        self.assertEqual(lab, 1)
        self.assertEqual((ts.year, ts.month, ts.day, ts.hour, ts.minute), (2008, 10, 17, 6, 7))
        for bad in ('0 "17/10/2008 06:07:00"', '1 "2008-10-17 06:07:00"', "1 17/10/2008"):
            with self.assertRaises(IntegrityError):
                parse_label_line(bad)


if __name__ == "__main__":
    unittest.main()
