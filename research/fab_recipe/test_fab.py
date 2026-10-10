#!/usr/bin/env python3
"""연구 1 시뮬레이터·시연 시험. 실행: python3 research/fab_recipe/test_fab.py"""

import io
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import demo  # noqa: E402
import fab  # noqa: E402
from fab import accord  # noqa: E402


class TestAgentDrafts(unittest.TestCase):
    def test_every_step_the_agent_can_take_passes_the_contract(self):
        t = fab.T_MAX0
        while True:
            new = fab.next_max(t)
            if new is None:
                break
            for kw in ({}, {"applies_to": "다음 PM 까지", "wiwnu": True, "stat_fix": True}):
                p = fab.draft_payload("r41", t, new, ["근거"], **kw)
                self.assertEqual(accord.payload_errors(p, "recipe_change"), [], (t, new, kw))
            t = new
        self.assertEqual(t, fab.WIN_HI)

    def test_next_max_stays_in_window_and_step(self):
        self.assertEqual(fab.next_max(115.0), 125.0)
        self.assertEqual(fab.next_max(125.0), 130.0)
        self.assertIsNone(fab.next_max(130.0))

    def test_window_file_is_what_the_payload_cites(self):
        p = fab.draft_payload("r41", 115.0, 125.0, ["근거"])
        self.assertEqual(p["window_ref"]["sha256"], fab.WINDOW_SHA)
        self.assertEqual(p["changes"][0]["window"], {"lo": fab.WIN_LO, "hi": fab.WIN_HI})


class TestModel(unittest.TestCase):
    def test_forecast_on_exact_line(self):
        hist = [(n, 50.0 - 0.01 * n) for n in range(100, 250)]
        n_sat = fab.forecast_saturation(hist, 115.0)
        # 50 - 0.01 n = 5000/115  →  n = (50 - 43.478…)/0.01
        self.assertAlmostEqual(n_sat, (50.0 - 5000.0 / 115.0) / 0.01, places=6)
        self.assertIsNone(fab.forecast_saturation([(n, 50.0) for n in range(10)], 115.0))

    def test_closed_loop_sigma_matches_theory(self):
        cl = fab.closed_loop_capability(n=60000)
        self.assertLess(abs(cl["sd"] - fab.SIGMA_CL) / fab.SIGMA_CL, 0.02, cl)

    def test_run_pad_is_deterministic(self):
        d = fab.pad_draws(3, 5, rw_sigma=0.02)
        self.assertEqual(fab.run_pad(d, "predictive", 2.0), fab.run_pad(d, "predictive", 2.0))
        self.assertEqual(fab.pad_draws(3, 5, 0.02), d)

    def test_policy_semantics(self):
        d = fab.pad_draws(1, 0)
        d["wear"] = 0.0075          # 확실히 상한에 닿는 패드
        none = fab.run_pad(d, "none", 2.0)
        auto = fab.run_pad(d, "autonomous", 2.0)
        slow = fab.run_pad(d, "reactive", 8.0)
        self.assertEqual(none["proposals"], 0)
        self.assertIsNotNone(none["first_sat"])
        self.assertGreater(auto["applied"], 0)
        self.assertLessEqual(auto["oos"], slow["oos"])
        self.assertLess(slow["oos"], none["oos"])
        for r in (auto, slow):
            self.assertEqual(r["refused_by_contract"], 0)

    def test_bias_case_review_rejects(self):
        bc = fab.bias_case()["result"]
        self.assertEqual(bc["reactive"]["rejected_in_review"], 1)
        self.assertLess(bc["reactive"]["oos"], bc["autonomous"]["oos"])


class TestDemo(unittest.TestCase):
    def test_review_agent(self):
        v1 = fab.draft_payload("r41", 115.0, 125.0, ["근거"])
        self.assertEqual(len(demo.review(v1, fab.WINDOW_PATH)), 3)
        v2 = fab.draft_payload("r41", 115.0, 125.0, ["근거"], applies_to="PM 까지",
                               wiwnu=True, stat_fix=True)
        self.assertEqual(demo.review(v2, fab.WINDOW_PATH), [])
        forged = dict(v2, window_ref=dict(v2["window_ref"], sha256="f" * 64))
        self.assertTrue(any("sha256" in i for i in demo.review(forged, fab.WINDOW_PATH)))

    def test_end_to_end(self):
        buf = io.StringIO()
        self.assertEqual(demo.main(out=buf), 0)
        text = buf.getvalue()
        self.assertIn("종료 코드 2", text)                     # 창 밖 초안은 막힌다
        self.assertIn("공정 창 [80.0, 130.0] 밖이다", text)
        self.assertEqual(text.count("최종 상태 executed"), 2)  # 양쪽 다 실행 기록까지 간다
        for refusal in ("지문 불일치", "사람만 발화할 수 있다", "*상대편* 사람이다",
                        "합의(agreed) 전에는"):
            self.assertIn(refusal, text)


if __name__ == "__main__":
    random.seed(0)
    unittest.main(verbosity=2)
