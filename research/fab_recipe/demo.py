#!/usr/bin/env python3
"""팹 레시피 변경 한 건을 ACCORD 로 처음부터 끝까지 돌린다 — 실제 accord.py 로.

두 쪽:
  A = 장비기술 에이전트 'eqp'   (사람 1001, 설비 엔지니어)
  B = 공정통합 에이전트 'integ' (사람 2002, 공정 엔지니어)

사람이 치는 승인 줄은 시연이라 이 스크립트가 대신 넣는다. 실제로는 사람이 자기 채팅에
직접 쳐야 하고, 그 줄만이 승인이다 — 나머지(감지, 제안서, 쟁점, 수정)는 에이전트 몫이다.
협의 번호와 시각은 실행할 때마다 바뀐다. 공정 수치는 시드로 고정돼 있다.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import statistics
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fab  # noqa: E402
from fab import accord  # noqa: E402

HUMAN_A, HUMAN_B = "1001", "2002"


class Side:
    def __init__(self, home, name):
        self.home, self.name = home, name

    def run(self, *argv):
        accord.HOME = self.home
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = accord.main(list(argv))
        return rc, out.getvalue(), err.getvalue()


def envelope_of(stdout):
    m = re.search(r"```accord\n(.*?)\n```", stdout, re.S)
    return "```accord\n" + m.group(1) + "\n```" if m else None


def body_of(env_text):
    return json.loads(env_text.split("\n", 1)[1].rsplit("\n", 1)[0])


def first_line(text):
    return text.strip().splitlines()[0] if text.strip() else ""


# ---------------------------------------------------------------- B 쪽 검토 에이전트
def review(payload, window_path):
    """B 의 에이전트가 하는 일: 확인하고, 모자란 데를 쟁점으로 건다. 승인·반려는 못 한다."""
    issues = []
    with open(window_path, "rb") as fh:
        raw = fh.read()
    doc = json.loads(raw.decode("utf-8"))
    if payload["window_ref"]["sha256"] != hashlib.sha256(raw).hexdigest():
        issues.append("window_ref 의 sha256 이 내 쪽 공정 창 파일과 다르다 — 창 값의 출처를 확인할 것")
    for c in payload["changes"]:
        w = doc["params"].get(c["param"])
        if w is None or (w["lo"], w["hi"]) != (c["window"]["lo"], c["window"]["hi"]):
            issues.append(f"{c['param']} 의 창 {c['window']} 이 승인된 공정 창 파일과 다르다")
    longer = any(c["param"].startswith("polish_time") and c["to"] > c["from"]
                 for c in payload["changes"])
    if longer and not any(v["metric"] == "wiwnu_pct" for v in payload["verification"]):
        issues.append("연마 시간을 늘리면 가장자리 과연마로 웨이퍼 내 균일도(WIWNU)가 나빠질 수 있는데 "
                      "검증 항목에 균일도 기준이 없다")
    for v in payload["verification"]:
        if "Cpk" in v["rule"] and v["n_wafers"] < fab.CPK_MONITOR_N:
            p = fab.qual_fail_rates(v["n_wafers"])["p_fail_cpk"]
            issues.append(f"{v['n_wafers']}매로 Cpk ≥ 1.33 을 판정하면 아무 문제 없는 공정도 "
                          f"{p:.0%} 확률로 떨어진다 (R2R 이 도는 상태의 Cpk 가 "
                          f"{(fab.USL - fab.TARGET) / (3 * fab.SIGMA_CL):.2f} 로 기준에 붙어 "
                          f"있다) — qual 은 평균·산포로 보고, 적용 뒤는 SPC 로 감시할 것")
    if "applies_to" not in payload:
        issues.append("적용 범위(applies_to)가 없다 — 넓힌 상한을 언제 원래 판본으로 되돌리는가")
    return issues


# ---------------------------------------------------------------- 시뮬레이션 쪽
def wiwnu(t, rng):
    """가정: 연마 시간이 길수록 가장자리 과연마로 불균일도가 조금씩 나빠진다 (%)."""
    return 2.6 + 0.04 * (t - 100.0) + rng.gauss(0.0, 0.25)


def run_wafers(d, n0, count, t_max, b_hat, rng):
    """새 상한으로 count 매를 돌린다. 시뮬레이터와 같은 공정 모델이다."""
    meas, ws = [], []
    for n in range(n0, n0 + count):
        rate = d["rate0"] - d["wear"] * n + d["rw"][n]
        t = min(max(fab.TARGET / b_hat, fab.T_MIN), t_max)
        y = rate * t + rng.gauss(0.0, fab.SIGMA_PROC)
        y_meas = y + rng.gauss(0.0, fab.SIGMA_MET)
        b_hat = fab.LAMBDA * (y_meas / t) + (1 - fab.LAMBDA) * b_hat
        meas.append(y_meas)
        ws.append(wiwnu(t, rng))
    return meas, ws, b_hat


def main(out=sys.stdout, seed=11, wear=0.0068):
    say = lambda *a: print(*a, file=out)  # noqa: E731
    tmp = tempfile.mkdtemp(prefix="accord-fab-")
    try:
        return _main(say, tmp, seed, wear)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _main(say, tmp, seed, wear):
    def write(name, text):
        p = os.path.join(tmp, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    A = Side(os.path.join(tmp, "A"), "eqp")
    B = Side(os.path.join(tmp, "B"), "integ")
    A.run("init", "--me", "eqp", "--human-id", HUMAN_A, "--peer", "integ",
          "--peer-human-id", HUMAN_B)
    B.run("init", "--me", "integ", "--human-id", HUMAN_B, "--peer", "eqp",
          "--peer-human-id", HUMAN_A)

    # 1. 장비: 패드 한 장을 돌려 R2R 이 상한에 묶이는 지점까지 간다
    d = fab.pad_draws(seed, 0)
    d["wear"] = wear
    trace = []
    fab.run_pad(d, "none", 2.0, check_contract=False, trace=trace)
    pinned, at = 0, None
    for row in trace:
        pinned = pinned + 1 if row["t_req"] > row["t_max"] else 0
        if pinned >= fab.PIN_RUN:
            at = row["n"]
            break
    run = trace[at - fab.PIN_RUN + 1: at + 1]
    ew, b_now = trace[at]["z"], trace[at]["b_hat"]
    say(f"## 1. 감지 — CMP-03, 패드 마모율 {wear} Å/s/웨이퍼 (가정), 시드 {seed}")
    say(f"W{run[0]['n'] + 1}–W{at + 1}: R2R 이 요구한 연마 시간 "
        f"{', '.join(format(r['t_req'], '.1f') for r in run)} s — 승인된 상한 {fab.T_MAX0:g} s 에 묶였다")
    say(f"제거량 EWMA {ew:.0f} Å (목표 {fab.TARGET:.0f}), 추정 제거율 {b_now:.2f} Å/s")

    # 2. A 의 에이전트가 PM 까지 한 번에 버티려고 여유 10% 를 얹은 값을 써 본다 — 계약이 막는다
    b_pm = b_now - wear * (fab.PAD_LIFE - at)
    need = fab.TARGET / b_pm
    evidence = [f"W{run[0]['n'] + 1}–W{at + 1} 연마 시간 상한 {fab.T_MAX0:g} s 고정 "
                f"(요구 {run[-1]['t_req']:.1f} s)",
                f"제거량 EWMA {ew:.0f} Å — 목표 {fab.TARGET:.0f} Å 아래로 내려가는 중",
                f"추정 제거율 {b_now:.2f} Å/s, 다음 PM(W{fab.PAD_LIFE}) 까지 버티려면 약 {need:.0f} s"]
    greedy_to = float(math.ceil(need * 1.1))
    greedy = fab.draft_payload("r41", fab.T_MAX0, greedy_to, evidence)
    rc, o, e = A.run("propose", "--subject", "recipe_change",
                     "--file", write("greedy.json", json.dumps(greedy, ensure_ascii=False)))
    say("\n## 2. A 에이전트의 첫 초안 — PM 까지 한 번에, 여유 10% 를 얹어")
    say(f"상한 {fab.T_MAX0:g} → {greedy_to:g} s 제안 → 종료 코드 {rc}")
    say(e.strip())

    # 3. 창 안·한 걸음 안으로 좁혀 다시 제안
    new = fab.next_max(fab.T_MAX0)
    v1 = fab.draft_payload("r41", fab.T_MAX0, new, evidence)
    rc, o, e = A.run("propose", "--subject", "recipe_change",
                     "--file", write("v1.json", json.dumps(v1, ensure_ascii=False)))
    env1 = envelope_of(o)
    th, h1 = body_of(env1)["thread"], body_of(env1)["state_hash"]
    say("\n## 3. 좁힌 제안 — 창 [80, 130] 안, 한 걸음(10 s) 안")
    say(f"상한 {fab.T_MAX0:g} → {new:g} s · 협의 {th} · rev 1 · 지문 {h1} · 종료 코드 {rc}")
    rc, o, e = B.run("ingest", "--file", write("env1.txt", env1))
    say(f"B 수신 → 종료 코드 {rc}")

    # 4. B 의 에이전트가 검토하고 쟁점을 건다
    issues = review(v1, fab.WINDOW_PATH)
    say("\n## 4. B 에이전트 검토 — 공정 창 파일 sha256 대조, 체크리스트")
    for text in issues:
        rc, o, e = B.run("question", "--thread", th, "--text", text)
        A.run("ingest", "--file", write("q.txt", envelope_of(o)))
        say(f"쟁점: {text}")

    # 5. A 가 고친다 (이전 지문은 이제 무효)
    v2 = fab.draft_payload("r41", fab.T_MAX0, new, evidence,
                           applies_to=f"다음 패드 교체(PM, W{fab.PAD_LIFE})까지. PM 때 r41 로 복귀",
                           wiwnu=True, stat_fix=True)
    rc, o, e = A.run("revise", "--thread", th, "--note", "적용 범위·균일도 검증 추가, Cpk 를 감시로",
                     "--file", write("v2.json", json.dumps(v2, ensure_ascii=False)))
    env2 = envelope_of(o)
    h2 = body_of(env2)["state_hash"]
    B.run("ingest", "--file", write("env2.txt", env2))
    say("\n## 5. A 의 수정 — rev 2")
    say(f"지문 {h1} → {h2}. B 체크리스트에 남은 쟁점: {len(review(v2, fab.WINDOW_PATH))}건")

    # 6. 경계 시연
    say("\n## 6. 막혀야 하는 것들")
    rc, o, e = A.run("ingest", "--file", write("old.txt", f"승인 {th} {h1}"),
                     "--from-human", HUMAN_A)
    say(f"사람 A 가 낡은 지문 {h1} 로 승인 → {rc}: {first_line(e)}")
    fake = {"accord": "1", "thread": th, "rev": 2, "ts": accord.now(),
            "sender": {"party": "integ", "kind": "agent"}, "type": "approve",
            "subject": "recipe_change", "state_hash": h2}
    rc, o, e = A.run("ingest", "--file", write("fake.txt",
                                               "```accord\n" + json.dumps(fake) + "\n```"))
    say(f"B 에이전트가 approve 봉투를 보냄 → {rc}: {first_line(e)}")
    rc, o, e = A.run("ingest", "--file", write("proxy.txt", f"승인 {th} {h2}"),
                     "--from-human", HUMAN_B)
    say(f"A 에이전트가 사람 B 의 id 로 승인을 기록 → {rc}: {first_line(e)[:60]}…")
    rc, o, e = A.run("execute", "--thread", th, "--note", "적용")
    say(f"합의 전 execute → {rc}: {first_line(e)}")

    # 7. 사람 둘이 각자 자기 채팅에 친다 (시연에서는 스크립트가 대신 넣는다)
    say("\n## 7. 사람 승인 — 각자 자기 에이전트에게 한 줄")
    A.run("ingest", "--file", write("ha.txt", f"승인 {th} {h2}"), "--from-human", HUMAN_A)
    rc, o, e = A.run("relay", "--thread", th)
    B.run("ingest", "--file", write("ra.txt", envelope_of(o)))
    B.run("ingest", "--file", write("hb.txt", f"승인 {th} {h2}"), "--from-human", HUMAN_B)
    rc, o, e = B.run("relay", "--thread", th)
    A.run("ingest", "--file", write("rb.txt", envelope_of(o)))
    for side in (A, B):
        accord.HOME = side.home
        st = accord.derive(th)
        say(f"[{side.name}] 상태 {st['state']} · 승인 {', '.join(st['approved_by'])} · "
            f"전언으로 센 것 {', '.join(st['relayed_approvals']) or '없음'}")
        if side.name in st["relayed_approvals"]:
            say(f"[{side.name}] ※ 내 사람의 승인까지 '전언' 으로 보인다 — 내가 보낸 approve_relay 가 "
                f"내 원장에서 내 승인을 덮어썼다 (v2 요구사항 문서의 P10, 여기서 실행으로 재현)")

    # 8. 적용 → qual 5매 → 실행 증거 → 누적 감시
    rng = random.Random(seed)
    meas, ws, b_after = run_wafers(d, at + 1, 5, new, b_now, rng)
    m, sd = statistics.fmean(meas), statistics.stdev(meas)
    ok = abs(m - fab.TARGET) <= 50 and sd <= fab.QUAL_SD_MAX and max(ws) <= 4.0
    say("\n## 8. 적용과 qual 5매 (rev 2 의 검증 기준)")
    say(f"평균 {m:.0f} Å, 표준편차 {sd:.0f} Å (상한 {fab.QUAL_SD_MAX:.0f}), WIWNU 최대 {max(ws):.2f}% "
        f"→ {'통과' if ok else '미달 — rollback 대상'}")
    if ok:
        rc, o, e = A.run("execute", "--thread", th, "--note", "상한 확대 적용, qual 5매 통과",
                         "--evidence", f"qual mean {m:.0f} A", f"qual sd {sd:.0f} A",
                         f"WIWNU max {max(ws):.2f}%")
        B.run("ingest", "--file", write("ex.txt", envelope_of(o)))
        n_mon = min(fab.CPK_MONITOR_N, fab.PAD_LIFE - (at + 6))
        mon, _, _ = run_wafers(d, at + 6, n_mon, new, b_after, rng)
        half = fab.SPC_L * fab.SIGMA_CL * math.sqrt(fab.SPC_LAMBDA / (2 - fab.SPC_LAMBDA))
        z, alarms = fab.TARGET, 0
        for y in mon:
            z = fab.SPC_LAMBDA * y + (1 - fab.SPC_LAMBDA) * z
            alarms += abs(z - fab.TARGET) > half
        mm, ms = statistics.fmean(mon), statistics.stdev(mon)
        cpk = min(fab.USL - mm, mm - fab.LSL) / (3 * ms)
        say(f"적용 뒤 {n_mon}매 SPC(EWMA 관리도, ±{half:.0f} Å) 이탈 {alarms}회 → "
            f"{'유지' if not alarms else 'rollback 조건'}")
        say(f"참고: 같은 구간 평균 {mm:.0f} Å, Cpk {cpk:.2f}"
            + (" — 'Cpk < 1.33 이면 rollback' 이었다면 이 정상 공정을 되돌렸을 것이다"
               if cpk < 1.33 else ""))
    for side in (A, B):
        accord.HOME = side.home
        st = accord.derive(th)
        say(f"[{side.name}] 최종 상태 {st['state']} · rev {st['rev']} · 지문 {st['state_hash']} · "
            f"쟁점 {st['total_questions']}건 · 경고 {len(st['warnings'])}건")
    rc, o, e = A.run("log", "--thread", th)
    say("\n## 원장 (A 쪽)\n" + o.rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
