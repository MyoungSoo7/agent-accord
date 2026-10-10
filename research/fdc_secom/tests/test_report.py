"""README.md must only quote numbers that run_all.py generated (results.md)."""

import tempfile
import unittest
from pathlib import Path

import check_report

HERE = Path(__file__).resolve().parent.parent


class CheckerTest(unittest.TestCase):
    def test_detects_unknown_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd, rs = Path(tmp, "README.md"), Path(tmp, "results.md")
            rs.write_text("| ROC-AUC | 0.621 [0.545, 0.692] | 104 |\n")
            rd.write_text("평균 0.621 이고 불량 104개다. 5 fold 다.\n")
            self.assertEqual(check_report.check(rd, rs), [])
            rd.write_text("평균 0.622 이고 불량 105개다. 2008-07-19 부터.\n")
            self.assertEqual(
                sorted(check_report.check(rd, rs)), ["date:2008-07-19", "num:0.622", "num:105"]
            )

    def test_sign_must_match_and_code_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd, rs = Path(tmp, "README.md"), Path(tmp, "results.md")
            rs.write_text("Δ +0.182, z = -4.53\n")
            rd.write_text("Δ = +0.182, z = -4.53, `seed 12345` 는 무시\n\n## 참고문헌\n\n1979, 33(4)\n")
            self.assertEqual(check_report.check(rd, rs), [])
            rd.write_text("z = 4.53 아니고 -0.182\n")
            self.assertEqual(sorted(check_report.check(rd, rs)), ["num:-0.182", "num:4.53"])

    def test_citation_years_ignored_but_other_years_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd, rs = Path(tmp, "README.md"), Path(tmp, "results.md")
            rs.write_text("0.5\n")
            rd.write_text("SPE 한계 (Jackson & Mudholkar, 1979) 와 T² (Tracy et al. 1992) 를 쓴다.\n")
            self.assertEqual(check_report.check(rd, rs), [])
            rd.write_text("1979 개의 개체\n")
            self.assertEqual(check_report.check(rd, rs), ["num:1979"])


@unittest.skipUnless((HERE / "results.md").is_file() and (HERE / "README.md").is_file(), "run run_all.py first")
class ReportConsistencyTest(unittest.TestCase):
    def test_readme_numbers_come_from_results(self):
        missing = check_report.check(HERE / "README.md", HERE / "results.md")
        self.assertEqual(missing, [], f"README numbers not in results.md: {missing}")


if __name__ == "__main__":
    unittest.main()
