#!/usr/bin/env python3
"""ACCORD — 사람↔사람 협업을 각자의 대리 에이전트가 중개하는 최소 프로토콜.

설계 한 줄: 에이전트끼리는 *제안서*만 주고받고, **승인은 사람만 발화한다.**

이 파일은 프로토콜의 구현체다. 실행은 하지 않는다 — 여기에는 subprocess/eval/exec 이
없고, 상대에게서 들어온 내용은 *데이터로만* 다룬다. 들어온 메시지가 무엇을 지시하든
이 프로그램이 하는 일은 "스키마 검증 → 원장에 기록 → 다음에 무엇이 필요한지 출력"
셋뿐이다.

원장(ledger)은 append-only JSONL 이다. 상태는 저장하지 않고 매번 원장에서 재계산한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

HOME = os.environ.get("ACCORD_HOME", os.path.expanduser("~/.accord"))
CONTRACTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contracts")

# 에이전트가 발화할 수 있는 타입과, 사람만 발화할 수 있는 타입을 *코드에서* 가른다.
AGENT_TYPES = {"propose", "question", "revise", "execute", "withdraw",
               "approve_relay", "reject_relay"}
HUMAN_ONLY_TYPES = {"approve", "reject"}
SUBJECTS = ("doc_review", "schedule", "code_change", "recipe_change")


class AccordError(Exception):
    pass


# ---------------------------------------------------------------- 스키마 검증
# jsonschema 패키지가 없는 환경이라 필요한 키워드만 직접 구현한다.
# 지원: type, const, enum, required, properties, additionalProperties(false),
#       items, minItems, maxItems, minLength, pattern, minimum, maximum
_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def validate(inst, schema, path="$"):
    errs = []
    t = schema.get("type")
    if t:
        if t == "integer":
            if not isinstance(inst, int) or isinstance(inst, bool):
                return [f"{path}: integer 가 아니다"]
        elif t == "number":
            if not isinstance(inst, (int, float)) or isinstance(inst, bool):
                return [f"{path}: number 가 아니다"]
        else:
            py = _TYPES.get(t)
            if py is None:
                return [f"{path}: 알 수 없는 type '{t}'"]
            if t == "boolean":
                if not isinstance(inst, bool):
                    return [f"{path}: boolean 이 아니다"]
            elif not isinstance(inst, py) or (t != "boolean" and isinstance(inst, bool)):
                return [f"{path}: {t} 가 아니다"]
    if "const" in schema and inst != schema["const"]:
        errs.append(f"{path}: {schema['const']!r} 여야 한다 (받은 값 {inst!r})")
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {schema['enum']} 중 하나여야 한다 (받은 값 {inst!r})")
    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in schema and inst < schema["minimum"]:
            errs.append(f"{path}: {schema['minimum']} 이상이어야 한다")
        if "maximum" in schema and inst > schema["maximum"]:
            errs.append(f"{path}: {schema['maximum']} 이하여야 한다")
    if isinstance(inst, str):
        if "minLength" in schema and len(inst) < schema["minLength"]:
            errs.append(f"{path}: 최소 길이 {schema['minLength']}")
        pat = schema.get("pattern")
        if pat and not re.search(pat, inst):
            errs.append(f"{path}: 형식 불일치 ({pat})")
    if isinstance(inst, list):
        if "minItems" in schema and len(inst) < schema["minItems"]:
            errs.append(f"{path}: 항목이 최소 {schema['minItems']}개 필요하다")
        if "maxItems" in schema and len(inst) > schema["maxItems"]:
            errs.append(f"{path}: 항목이 {schema['maxItems']}개를 넘는다")
        if "items" in schema:
            for i, el in enumerate(inst):
                errs += validate(el, schema["items"], f"{path}[{i}]")
    if isinstance(inst, dict):
        for k in schema.get("required", []):
            if k not in inst:
                errs.append(f"{path}.{k}: 필수 항목이 없다")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for k in inst:
                if k not in props:
                    errs.append(f"{path}.{k}: 계약에 없는 항목이다")
        for k, sub in props.items():
            if k in inst:
                errs += validate(inst[k], sub, f"{path}.{k}")
    return errs


def load_schema(name):
    with open(os.path.join(CONTRACTS, f"{name}.schema.json"), encoding="utf-8") as fh:
        return json.load(fh)


def check(inst, schema_name, what):
    errs = validate(inst, load_schema(schema_name))
    if errs:
        raise AccordError(f"{what} 계약 위반:\n  - " + "\n  - ".join(errs))


# ---------------------------------------------------------------- 본문 규칙
# 스키마로는 걸 수 없는 *항목 사이의* 조건. 어기면 계약 위반과 똑같이 제안 자체가 안 된다.
def _finite(x):
    # NaN 은 자기 자신과 같지 않다. json 은 NaN·Infinity 를 그대로 읽어 들인다.
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and x == x and x not in (float("inf"), float("-inf")))


def recipe_change_rules(p):
    """창 밖 값·되돌릴 곳 없는 변경·이름만 있는 변경을 막는다. 스키마를 통과한 본문이 전제다."""
    errs = []
    seen = set()
    for i, c in enumerate(p["changes"]):
        at = f"$.changes[{i}]"
        nums = {k: c[k] for k in ("from", "to", "max_step") if k in c}
        nums.update({f"window.{k}": c["window"][k] for k in ("lo", "hi")})
        bad = sorted(k for k, v in nums.items() if not _finite(v))
        if bad:
            errs.append(f"{at}: 유한한 수가 아니다 ({', '.join(bad)})")
            continue
        lo, hi, frm, to = c["window"]["lo"], c["window"]["hi"], c["from"], c["to"]
        if not lo < hi:
            errs.append(f"{at}.window: lo({lo}) 가 hi({hi}) 보다 작아야 한다")
        elif not lo <= to <= hi:
            errs.append(f"{at}.to: 승인된 공정 창 [{lo}, {hi}] 밖이다 (받은 값 {to}) — "
                        f"창 밖 값은 제안할 수 없다")
        if frm == to:
            errs.append(f"{at}: from 과 to 가 같다 — 바뀌는 것이 없는 항목이다")
        if "max_step" in c and abs(to - frm) > c["max_step"]:
            errs.append(f"{at}: 한 번에 바꿀 수 있는 폭 max_step {c['max_step']} 을 넘는다 "
                        f"(|{to} - {frm}| = {abs(to - frm)})")
        if c["param"] in seen:
            errs.append(f"{at}.param: '{c['param']}' 이 두 번 나온다 — 어느 값이 맞는지 정할 수 없다")
        seen.add(c["param"])
    base, to_rev = p["recipe"]["base_rev"], p["rollback"]["to_rev"]
    if to_rev != base:
        errs.append(f"$.rollback.to_rev: 되돌릴 곳은 지금 승인된 판본 recipe.base_rev({base!r}) "
                    f"여야 한다 (받은 값 {to_rev!r})")
    return errs


RULES = {"recipe_change": recipe_change_rules}


def payload_errors(payload, subject):
    """계약 위반과 본문 규칙 위반을 모아 돌려준다. 던지지 않는다 — derive 도 쓴다."""
    if subject not in SUBJECTS:
        return [f"알 수 없는 합의 대상 {subject!r}"]
    errs = validate(payload, load_schema(subject))
    if errs or subject not in RULES:
        return errs
    return RULES[subject](payload)


def check_payload(payload, subject):
    errs = payload_errors(payload, subject)
    if errs:
        raise AccordError(f"{subject} 본문 계약 위반:\n  - " + "\n  - ".join(errs))


# ---------------------------------------------------------------- 저장소
def config_path():
    return os.path.join(HOME, "config.json")


def ledger_path():
    return os.path.join(HOME, "ledger.jsonl")


def load_config():
    try:
        with open(config_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise AccordError("설정이 없다. 먼저 `accord.py init ...` 을 실행한다.")


def append(event):
    os.makedirs(HOME, exist_ok=True)
    with open(ledger_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def read_ledger(thread=None):
    try:
        with open(ledger_path(), encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
    except FileNotFoundError:
        rows = []
    return [r for r in rows if thread is None or r.get("thread") == thread]


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 해시
def state_hash(thread, rev, subject, payload):
    """사람이 승인할 때 손으로 치는 8자리. 판본이 바뀌면 반드시 바뀐다."""
    canon = json.dumps(
        {"thread": thread, "rev": rev, "subject": subject, "payload": payload},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:8]


def new_thread_id(party, subject, title):
    seed = f"{party}|{subject}|{title}|{now()}|{os.urandom(8).hex()}"
    return "T-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------- 상태 재계산
def derive(thread):
    """원장만 보고 현재 상태를 만든다. 별도 상태 파일을 두지 않는다."""
    rows = read_ledger(thread)
    if not rows:
        raise AccordError(f"{thread}: 원장에 없는 협의다")
    my_human = None
    try:
        my_human = load_config()["me"]["human_id"]
    except AccordError:
        pass
    cur = None            # 마지막 propose/revise
    approvals = {}        # party -> (승인한 state_hash, 출처)
    misrecorded = []      # 이 에이전트가 기록할 권한이 없던 승인
    rejects = []          # 반려 기록
    issues = []           # 쟁점(question) 전체
    resolved = set()
    executed = []
    withdrawn = False
    for r in rows:
        t = r["type"]
        if t in ("propose", "revise"):
            cur = r
            approvals.clear()   # 판본이 바뀌면 이전 승인은 전부 무효다
        elif t == "question":
            issues.append(r)
        elif t == "approve":
            # 이 에이전트가 직접 들은 사람 승인. 자기 사람 것만 기록할 권한이 있다.
            if r["sender"].get("human_id") == my_human:
                approvals[r["sender"]["party"]] = (r.get("state_hash"), "attested")
            else:
                misrecorded.append(r)
        elif t == "approve_relay":
            # 상대 에이전트의 전언. 검증할 수 없다 — 그렇다고 표시해서 센다.
            approvals[r["sender"]["party"]] = (r.get("state_hash"), "relayed")
        elif t in ("reject", "reject_relay"):
            rejects.append(r)
            approvals.clear()
        elif t == "execute":
            executed.append(r)
        elif t == "withdraw":
            withdrawn = True
        if t in ("revise",):
            resolved |= {i["issue_id"] for i in issues if i.get("issue_id")}
    if cur is None:
        raise AccordError(f"{thread}: 제안 없이 시작된 협의다 (원장 손상)")
    h = state_hash(thread, cur["rev"], cur["subject"], cur["payload"])
    valid = {p for p, (ph, _src) in approvals.items() if ph == h}
    relayed = {p for p, (ph, src) in approvals.items() if ph == h and src == "relayed"}
    open_issues = [i for i in issues if i.get("issue_id") not in resolved]

    last_kind = rows[-1]["type"]
    if withdrawn:
        state = "withdrawn"
    elif rejects and last_kind in ("reject", "reject_relay"):
        state = "rejected"
    elif executed:
        state = "executed"
    elif len(valid) >= 2:
        state = "agreed"
    elif valid:
        state = "half_approved"
    elif open_issues:
        state = "questioned"
    else:
        state = "pending_approval"

    warnings = []
    if state in ("agreed", "executed") and not issues:
        warnings.append(
            "합의 착시 의심 — 쟁점이 0건이다. 양쪽 에이전트가 서로 동의만 했을 가능성을 "
            "사람이 확인할 것."
        )
    if relayed:
        warnings.append(
            "상대 사람의 승인(%s)은 상대 *에이전트의 전언*이다. 이 쪽에서 검증할 수 없다 — "
            "서명이 붙기 전까지 이 합의는 상대 에이전트를 신뢰하는 만큼만 유효하다."
            % ", ".join(sorted(relayed)))
    if misrecorded:
        warnings.append(
            "권한 없는 승인 기록 %d건이 원장에 있다 — 이 에이전트가 남의 사람 승인을 직접 "
            "기록한 흔적이다. 무효로 센다." % len(misrecorded))
    if state == "agreed" and cur["subject"] == "code_change":
        p = cur["payload"]
        if not p.get("rollback") or not p.get("verification"):
            warnings.append("코드 변경인데 롤백·검증 조건이 비어 있다")
    perrs = payload_errors(cur["payload"], cur["subject"])
    if perrs:
        warnings.append(
            "현재 판본이 계약·본문 규칙을 어긴다 — 수신 때는 막히므로 원장을 직접 손댄 흔적이다: "
            + "; ".join(perrs))
    return {
        "thread": thread, "rev": cur["rev"], "subject": cur["subject"],
        "payload": cur["payload"], "state_hash": h, "state": state,
        "approved_by": sorted(valid), "stale_approvals": sorted(set(approvals) - valid),
        "relayed_approvals": sorted(relayed), "misrecorded": misrecorded,
        "open_issues": open_issues, "total_questions": len(issues),
        "rejects": rejects, "executed": executed, "warnings": warnings,
        "rows": rows,
    }


# ---------------------------------------------------------------- 출력
def envelope(cfg, thread, rev, typ, subject, **kw):
    env = {
        "accord": "1", "thread": thread, "rev": rev, "ts": now(),
        "sender": {"party": cfg["me"]["party"], "kind": "agent"},
        "type": typ, "subject": subject,
    }
    env.update({k: v for k, v in kw.items() if v is not None})
    check(env, "envelope", "봉투")
    return env


def emit(env, note=""):
    append(env)
    print("── 공유 버스에 이대로 올린다 " + "─" * 30)
    print("```accord")
    print(json.dumps(env, ensure_ascii=False, indent=2, sort_keys=True))
    print("```")
    if note:
        print(note)


def load_payload(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- 명령
def cmd_init(a):
    os.makedirs(HOME, exist_ok=True)
    cfg = {
        "me": {"party": a.me, "human_id": str(a.human_id), "bot": a.bot or ""},
        "peer": {"party": a.peer, "human_id": str(a.peer_human_id)},
        "bus": {"kind": "telegram_group", "chat_id": str(a.group or "")},
    }
    with open(config_path(), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    print(f"설정 저장: {config_path()}")
    print(f"  나   = {cfg['me']['party']} (사람 {cfg['me']['human_id']})")
    print(f"  상대 = {cfg['peer']['party']} (사람 {cfg['peer']['human_id']})")
    print("이 두 사람 ID 만이 승인·반려를 발화할 수 있다.")


def cmd_propose(a):
    cfg = load_config()
    payload = load_payload(a.file)
    check_payload(payload, a.subject)
    title = payload.get("title") or payload.get("summary") or a.subject
    thread = new_thread_id(cfg["me"]["party"], a.subject, title)
    env = envelope(cfg, thread, 1, "propose", a.subject, payload=payload,
                   state_hash=state_hash(thread, 1, a.subject, payload))
    emit(env, note=f"\n협의 번호 {thread} · rev 1 · 지문 {env['state_hash']}")


def cmd_revise(a):
    cfg = load_config()
    st = derive(a.thread)
    payload = load_payload(a.file)
    check_payload(payload, st["subject"])
    rev = st["rev"] + 1
    env = envelope(cfg, a.thread, rev, "revise", st["subject"], payload=payload,
                   text=a.note, state_hash=state_hash(a.thread, rev, st["subject"], payload))
    emit(env, note=f"\nrev {rev} · 지문 {env['state_hash']} — 이전 승인은 모두 무효가 됐다.")


def cmd_question(a):
    cfg = load_config()
    st = derive(a.thread)
    qid = "Q-" + hashlib.sha256((a.text + now()).encode()).hexdigest()[:6]
    env = envelope(cfg, a.thread, st["rev"], "question", st["subject"],
                   text=a.text, issue_id=qid)
    emit(env, note=f"\n쟁점 {qid} 등록. 상대가 revise 로 답하면 해소된다.")


def cmd_request_approval(a):
    st = derive(a.thread)
    cfg = load_config()
    print(f"협의 {a.thread} · rev {st['rev']} · 상태 {st['state']}")
    if st["open_issues"]:
        print(f"⚠ 미해소 쟁점 {len(st['open_issues'])}건 — 먼저 정리하는 게 맞다:")
        for i in st["open_issues"]:
            print(f"   [{i['issue_id']}] {i.get('text','')}")
    print()
    print("사람이 *직접* 이 한 줄을 채팅에 쳐야 승인이 성립한다 (봇은 만들 수 없다):")
    print(f"\n    승인 {a.thread} {st['state_hash']}\n")
    print(f"반려: 반려 {a.thread} <사유>")
    print(f"양쪽 사람({cfg['me']['human_id']}, {cfg['peer']['human_id']}) 모두 쳐야 합의다.")


def cmd_relay(a):
    """내 사람이 직접 친 승인을 상대 에이전트에게 *전언*으로 전달한다.
    상대는 이걸 검증할 수 없으므로, 상대 쪽 상태에는 항상 전언 경고가 붙는다."""
    cfg = load_config()
    st = derive(a.thread)
    mine = cfg["me"]["party"]
    if mine not in st["approved_by"] and not a.reject:
        raise AccordError(
            f"내 사람({cfg['me']['human_id']})의 유효한 승인이 아직 없다 — 전달할 것이 없다. "
            f"현재 상태 {st['state']}")
    env = {"accord": "1", "thread": a.thread, "rev": st["rev"], "ts": now(),
           "sender": {"party": mine, "kind": "agent"},
           "type": "reject_relay" if a.reject else "approve_relay",
           "subject": st["subject"], "state_hash": st["state_hash"],
           "human_id": cfg["me"]["human_id"]}
    if a.reject:
        env["text"] = a.reject
    check(env, "envelope", "봉투")
    emit(env)
    print(f"내 사람({cfg['me']['human_id']})의 "
          f"{'반려' if a.reject else '승인'}을 전언으로 내보냈다. "
          f"상대 쪽에서는 '검증 불가 전언' 으로 표시된다.")


HUMAN_RE = re.compile(
    r"^\s*(승인|approve|반려|reject)\s+(T-[0-9a-f]{8})\s*(.*)$", re.IGNORECASE)


def cmd_ingest(a):
    cfg = load_config()
    if a.file == "-":
        raw = sys.stdin.read()
    else:
        with open(a.file, encoding="utf-8") as fh:
            raw = fh.read()
    body = raw.strip()

    m = HUMAN_RE.match(body.splitlines()[0] if body else "")
    if m:
        if not a.from_human:
            raise AccordError("사람 발화는 --from-human <telegram user id> 가 있어야 받는다")
        return ingest_human(cfg, m, str(a.from_human))
    return ingest_agent(cfg, body)


def ingest_human(cfg, m, from_id):
    verb, thread, rest = m.group(1).lower(), m.group(2), m.group(3).strip()
    if from_id == cfg["peer"]["human_id"]:
        raise AccordError(
            f"{from_id} 는 *상대편* 사람이다. 나는 그 사람의 말을 직접 듣지 못하므로 "
            f"그 승인을 여기에 기록할 권한이 없다 — 내가 --from-human 값을 지어낼 수 "
            f"있기 때문이다. 상대 승인은 상대 에이전트가 approve_relay 봉투로 보내야 하고, "
            f"그것도 '전언'으로만 센다.")
    if from_id != cfg["me"]["human_id"]:
        raise AccordError(
            f"등록되지 않은 사람 {from_id} 의 승인·반려다 — 무시한다. "
            f"이 에이전트가 받을 수 있는 사람은 {cfg['me']['human_id']} 뿐이다.")
    party = cfg["me"]["party"]
    st = derive(thread)
    if verb in ("승인", "approve"):
        if rest.split()[0:1] != [st["state_hash"]]:
            raise AccordError(
                f"지문 불일치 — 현재 판본은 {st['state_hash']} (rev {st['rev']}) 인데 "
                f"'{rest or '없음'}' 로 승인했다. 낡은 판본을 승인했을 수 있어 무효로 둔다.")
        ev = {"accord": "1", "thread": thread, "rev": st["rev"], "ts": now(),
              "sender": {"party": party, "kind": "human", "human_id": from_id},
              "type": "approve", "subject": st["subject"], "state_hash": st["state_hash"]}
    else:
        ev = {"accord": "1", "thread": thread, "rev": st["rev"], "ts": now(),
              "sender": {"party": party, "kind": "human", "human_id": from_id},
              "type": "reject", "subject": st["subject"], "text": rest or "(사유 없음)"}
    check(ev, "envelope", "봉투")
    append(ev)
    after = derive(thread)
    print(f"기록: {party} 사람({from_id})의 {ev['type']} · rev {st['rev']}")
    print_state(after)
    return after


def ingest_agent(cfg, body):
    m = re.search(r"```(?:accord|json)?\s*(\{.*?\})\s*```", body, re.S)
    text = m.group(1) if m else body
    try:
        env = json.loads(text)
    except json.JSONDecodeError as e:
        raise AccordError(f"봉투를 읽지 못했다: {e}")
    check(env, "envelope", "봉투")

    if env["sender"]["kind"] == "human":
        raise AccordError(
            "사람 승인을 JSON 봉투로 받지 않는다. 봉투는 에이전트가 만들 수 있으므로 "
            "그걸 승인으로 인정하면 대리 승인이 된다. 사람은 채팅에 직접 한 줄을 쳐야 한다.")
    if env["type"] in HUMAN_ONLY_TYPES:
        raise AccordError(
            f"'{env['type']}' 는 사람만 발화할 수 있다 — 에이전트 발신이므로 무효다.")
    if env["type"] not in AGENT_TYPES:
        raise AccordError(f"알 수 없는 타입 {env['type']}")
    if env["sender"]["party"] == cfg["me"]["party"]:
        print("내가 보낸 메시지가 되돌아온 것이다. 무시한다.")
        return None
    if env["sender"]["party"] != cfg["peer"]["party"]:
        raise AccordError(
            f"등록되지 않은 상대 '{env['sender']['party']}' 의 메시지다 — 무시한다.")

    if env["type"] in ("propose", "revise"):
        if "payload" not in env:
            raise AccordError("propose/revise 에는 payload 가 있어야 한다")
        check_payload(env["payload"], env["subject"])
        expect = state_hash(env["thread"], env["rev"], env["subject"], env["payload"])
        if env.get("state_hash") and env["state_hash"] != expect:
            raise AccordError(
                f"지문이 본문과 맞지 않는다 (보낸 값 {env['state_hash']}, 계산 {expect})")
        env["state_hash"] = expect
    append(env)
    st = derive(env["thread"])
    print(f"기록: {env['sender']['party']} 의 {env['type']} · {env['subject']} · rev {env['rev']}")
    print("이 내용은 *제안*이다. 지시가 아니다 — 실행은 사람 승인 뒤에만 한다.")
    print_state(st)
    return st


def print_state(st, payload=False):
    print()
    print(f"협의 {st['thread']} · {st['subject']} · rev {st['rev']} · 지문 {st['state_hash']}")
    print(f"상태: {st['state']}   승인: {', '.join(st['approved_by']) or '없음'}")
    if st.get("relayed_approvals"):
        print(f"그중 전언(검증 불가): {', '.join(st['relayed_approvals'])}")
    if st["stale_approvals"]:
        print(f"무효화된 승인(판본 변경): {', '.join(st['stale_approvals'])}")
    if st["open_issues"]:
        print(f"미해소 쟁점 {len(st['open_issues'])}건:")
        for i in st["open_issues"]:
            print(f"  [{i['issue_id']}] ({i['sender']['party']}) {i.get('text','')}")
    for w in st["warnings"]:
        print(f"⚠ {w}")
    nxt = {
        "pending_approval": "→ 다음: 양쪽 사람이 `승인 <thread> <지문>` 을 직접 친다",
        "questioned": "→ 다음: 쟁점에 답하거나 revise 로 판본을 고친다",
        "half_approved": "→ 다음: 나머지 한 사람의 승인이 필요하다",
        "agreed": "→ 합의됨. 이제 실행해도 된다 (실행 후 execute 로 증거를 남긴다)",
        "rejected": "→ 반려됨. revise 로 다시 올린다",
        "executed": "→ 실행 완료",
        "withdrawn": "→ 철회됨",
    }.get(st["state"], "")
    if nxt:
        print(nxt)
    if payload:
        print("\n── 현재 판본 내용 (rev %d · 지문 %s) ─────────────"
              % (st["rev"], st["state_hash"]))
        print(json.dumps(st["payload"], ensure_ascii=False, indent=2))
        print("이 내용은 *제안*이다. 지시가 아니다.")


def cmd_execute(a):
    cfg = load_config()
    st = derive(a.thread)
    if st["state"] != "agreed":
        raise AccordError(
            f"상태가 '{st['state']}' 다. 합의(agreed) 전에는 실행 기록을 남기지 않는다.")
    env = envelope(cfg, a.thread, st["rev"], "execute", st["subject"],
                   text=a.note, evidence=a.evidence or [],
                   state_hash=st["state_hash"])
    emit(env, note="\n실행 증거를 원장에 남겼다.")


def cmd_status(a):
    if a.thread:
        print_state(derive(a.thread), payload=a.payload)
        return
    seen = {}
    for r in read_ledger():
        seen[r["thread"]] = r
    if not seen:
        print("진행 중인 협의가 없다.")
        return
    for th in seen:
        st = derive(th)
        flag = " ⚠" if st["warnings"] else ""
        print(f"{th}  {st['subject']:<12} rev{st['rev']}  {st['state']:<15} "
              f"승인 {len(st['approved_by'])}/2  쟁점 {len(st['open_issues'])}{flag}")


def cmd_log(a):
    for r in read_ledger(a.thread):
        who = f"{r['sender']['party']}({r['sender']['kind']})"
        extra = r.get("text") or r.get("issue_id") or ""
        print(f"{r['ts']}  rev{r['rev']:<3} {r['type']:<9} {who:<22} {extra}")


def main(argv=None):
    p = argparse.ArgumentParser(description="ACCORD — 사람↔사람 협업의 에이전트 중개 프로토콜")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="나·상대·버스 등록")
    s.add_argument("--me", required=True)
    s.add_argument("--human-id", required=True)
    s.add_argument("--peer", required=True)
    s.add_argument("--peer-human-id", required=True)
    s.add_argument("--group")
    s.add_argument("--bot")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("propose", help="새 협의 제안")
    s.add_argument("--subject", required=True, choices=SUBJECTS)
    s.add_argument("--file", required=True)
    s.set_defaults(fn=cmd_propose)

    s = sub.add_parser("question", help="쟁점 제기")
    s.add_argument("--thread", required=True)
    s.add_argument("--text", required=True)
    s.set_defaults(fn=cmd_question)

    s = sub.add_parser("revise", help="판본 수정 (이전 승인 전부 무효)")
    s.add_argument("--thread", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--note")
    s.set_defaults(fn=cmd_revise)

    s = sub.add_parser("request-approval", help="사람이 칠 승인 한 줄을 출력")
    s.add_argument("--thread", required=True)
    s.set_defaults(fn=cmd_request_approval)

    s = sub.add_parser("relay", help="내 사람의 승인을 상대에게 전언으로 전달")
    s.add_argument("--thread", required=True)
    s.add_argument("--reject", help="반려를 전달할 때 사유")
    s.set_defaults(fn=cmd_relay)

    s = sub.add_parser("ingest", help="받은 메시지 처리 (봉투 또는 사람 한 줄)")
    s.add_argument("--file", default="-")
    s.add_argument("--from-human")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("execute", help="합의 후 실행 증거 기록")
    s.add_argument("--thread", required=True)
    s.add_argument("--note")
    s.add_argument("--evidence", nargs="*")
    s.set_defaults(fn=cmd_execute)

    s = sub.add_parser("status", help="현황")
    s.add_argument("--thread")
    s.add_argument("--payload", action="store_true",
                   help="현재 판본의 내용 전문을 같이 출력 (상대 에이전트가 검토할 때 필요)")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("log", help="원장 열람")
    s.add_argument("--thread")
    s.set_defaults(fn=cmd_log)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except AccordError as e:
        print(f"거부: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
