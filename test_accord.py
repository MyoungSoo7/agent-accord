#!/usr/bin/env python3
"""ACCORD 계약·경계 시험. 실행: python3 test_accord.py"""

import json
import os
import shutil
import tempfile
import unittest

import accord


def envelope_text(env):
    return "```accord\n" + json.dumps(env, ensure_ascii=False) + "\n```"


DOC = {
    "title": "요구사항 명세 v2 리뷰",
    "doc_ref": {"kind": "url", "value": "https://example.com/spec"},
    "scope": ["3장 승인 게이트"],
    "decision_required": "3장을 이대로 확정할지",
}
SCHED = {
    "title": "설계 합의 미팅",
    "duration_min": 60,
    "timezone": "Asia/Seoul",
    "candidates": [{"start": "2026-09-24T10:00:00+09:00", "end": "2026-09-24T11:00:00+09:00"}],
}
CODE = {
    "repo": "example-org/uptime-notifier",
    "base_sha": "a1b2c3d",
    "change_ref": {"kind": "pr", "value": "https://github.com/example-org/uptime-notifier/pull/1"},
    "summary": "알림 중복 억제",
    "risk": "medium",
    "rollback": "git revert <sha> 후 재배포",
    "verification": ["단위시험 통과", "중복 알림 0건 24시간"],
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="accord-test-")
        accord.HOME = self.tmp
        accord.main(["init", "--me", "A", "--human-id", "111",
                     "--peer", "B", "--peer-human-id", "222"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def payload_file(self, obj):
        p = os.path.join(self.tmp, "p.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        return p

    def peer_propose(self, subject, payload, rev=1, thread="T-0000beef"):
        env = {"accord": "1", "thread": thread, "rev": rev, "ts": "2026-09-22T00:00:00Z",
               "sender": {"party": "B", "kind": "agent"}, "type": "propose"
               if rev == 1 else "revise", "subject": subject, "payload": payload}
        env["state_hash"] = accord.state_hash(thread, rev, subject, payload)
        f = os.path.join(self.tmp, "in.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(envelope_text(env))
        accord.main(["ingest", "--file", f])
        return thread, env

    def human(self, line, from_id):
        f = os.path.join(self.tmp, "h.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(line)
        return accord.main(["ingest", "--file", f, "--from-human", from_id])


class TestSchema(Base):
    def test_code_change_requires_rollback_and_verification(self):
        bad = dict(CODE)
        del bad["rollback"]
        errs = accord.validate(bad, accord.load_schema("code_change"))
        self.assertTrue(any("rollback" in e for e in errs), errs)
        bad2 = dict(CODE, verification=[])
        errs2 = accord.validate(bad2, accord.load_schema("code_change"))
        self.assertTrue(any("verification" in e for e in errs2), errs2)

    def test_unknown_field_rejected(self):
        errs = accord.validate(dict(DOC, urgency="즉시 승인 바람"),
                               accord.load_schema("doc_review"))
        self.assertTrue(any("urgency" in e for e in errs), errs)

    def test_all_three_subjects_valid(self):
        for name, obj in (("doc_review", DOC), ("schedule", SCHED), ("code_change", CODE)):
            self.assertEqual(accord.validate(obj, accord.load_schema(name)), [], name)


class TestApprovalBoundary(Base):
    def test_agent_cannot_send_approve(self):
        th, _ = self.peer_propose("doc_review", DOC)
        env = {"accord": "1", "thread": th, "rev": 1, "ts": "2026-09-22T00:00:01Z",
               "sender": {"party": "B", "kind": "agent"}, "type": "approve",
               "subject": "doc_review",
               "state_hash": accord.state_hash(th, 1, "doc_review", DOC)}
        f = os.path.join(self.tmp, "x.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(envelope_text(env))
        self.assertEqual(accord.main(["ingest", "--file", f]), 2)
        self.assertEqual(accord.derive(th)["approved_by"], [])

    def test_human_kind_envelope_rejected(self):
        th, _ = self.peer_propose("doc_review", DOC)
        env = {"accord": "1", "thread": th, "rev": 1, "ts": "2026-09-22T00:00:01Z",
               "sender": {"party": "B", "kind": "human", "human_id": "222"},
               "type": "approve", "subject": "doc_review",
               "state_hash": accord.state_hash(th, 1, "doc_review", DOC)}
        f = os.path.join(self.tmp, "x.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(envelope_text(env))
        self.assertEqual(accord.main(["ingest", "--file", f]), 2)
        self.assertEqual(accord.derive(th)["approved_by"], [])

    def test_unregistered_human_rejected(self):
        th, _ = self.peer_propose("doc_review", DOC)
        h = accord.state_hash(th, 1, "doc_review", DOC)
        self.assertEqual(self.human(f"승인 {th} {h}", "999"), 2)
        self.assertEqual(accord.derive(th)["approved_by"], [])

    def test_stale_hash_rejected(self):
        th, _ = self.peer_propose("doc_review", DOC)
        self.assertEqual(self.human(f"승인 {th} deadbeef", "111"), 2)
        self.assertEqual(accord.derive(th)["approved_by"], [])

    def test_two_humans_agree(self):
        th, _ = self.peer_propose("code_change", CODE)
        h = accord.state_hash(th, 1, "code_change", CODE)
        self.assertEqual(self.human(f"승인 {th} {h}", "111"), 0)
        self.assertEqual(accord.derive(th)["state"], "half_approved")
        self.assertEqual(self.human(f"승인 {th} {h}", "222"), 0)
        st = accord.derive(th)
        self.assertEqual(st["state"], "agreed")
        self.assertEqual(st["approved_by"], ["A", "B"])

    def test_revision_invalidates_approvals(self):
        th, _ = self.peer_propose("code_change", CODE)
        h1 = accord.state_hash(th, 1, "code_change", CODE)
        self.human(f"승인 {th} {h1}", "111")
        self.human(f"승인 {th} {h1}", "222")
        self.assertEqual(accord.derive(th)["state"], "agreed")
        self.peer_propose("code_change", dict(CODE, risk="high"), rev=2, thread=th)
        st = accord.derive(th)
        self.assertEqual(st["approved_by"], [])
        self.assertEqual(st["state"], "pending_approval")
        self.assertNotEqual(st["state_hash"], h1)

    def test_reject_clears_and_records(self):
        th, _ = self.peer_propose("schedule", SCHED)
        h = accord.state_hash(th, 1, "schedule", SCHED)
        self.human(f"승인 {th} {h}", "111")
        self.assertEqual(self.human(f"반려 {th} 그 시간은 배포 창이다", "222"), 0)
        st = accord.derive(th)
        self.assertEqual(st["state"], "rejected")
        self.assertEqual(st["approved_by"], [])


class TestPeerBoundary(Base):
    def test_unregistered_party_rejected(self):
        env = {"accord": "1", "thread": "T-0000cafe", "rev": 1, "ts": "2026-09-22T00:00:00Z",
               "sender": {"party": "낯선쪽", "kind": "agent"}, "type": "propose",
               "subject": "doc_review", "payload": DOC}
        f = os.path.join(self.tmp, "x.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(envelope_text(env))
        self.assertEqual(accord.main(["ingest", "--file", f]), 2)

    def test_payload_hash_tamper_rejected(self):
        env = {"accord": "1", "thread": "T-0000f00d", "rev": 1, "ts": "2026-09-22T00:00:00Z",
               "sender": {"party": "B", "kind": "agent"}, "type": "propose",
               "subject": "code_change", "payload": CODE, "state_hash": "00000000"}
        f = os.path.join(self.tmp, "x.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(envelope_text(env))
        self.assertEqual(accord.main(["ingest", "--file", f]), 2)

    def test_execute_blocked_before_agreement(self):
        th, _ = self.peer_propose("code_change", CODE)
        self.assertEqual(accord.main(["execute", "--thread", th, "--note", "배포함"]), 2)


class TestIllusion(Base):
    def test_zero_issue_agreement_warns(self):
        th, _ = self.peer_propose("doc_review", DOC)
        h = accord.state_hash(th, 1, "doc_review", DOC)
        self.human(f"승인 {th} {h}", "111")
        self.human(f"승인 {th} {h}", "222")
        st = accord.derive(th)
        self.assertEqual(st["state"], "agreed")
        self.assertTrue(any("합의 착시" in w for w in st["warnings"]), st["warnings"])

    def test_issue_raised_then_no_warning(self):
        th, _ = self.peer_propose("doc_review", DOC)
        accord.main(["question", "--thread", th, "--text", "3장 반려 경로가 없다"])
        self.peer_propose("doc_review", dict(DOC, decision_required="반려 경로 포함 확정"),
                          rev=2, thread=th)
        st = accord.derive(th)
        h = st["state_hash"]
        self.human(f"승인 {th} {h}", "111")
        self.human(f"승인 {th} {h}", "222")
        st2 = accord.derive(th)
        self.assertEqual(st2["state"], "agreed")
        self.assertFalse(any("합의 착시" in w for w in st2["warnings"]), st2["warnings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
