# ACCORD 규격 v1

두 사람이 각자의 봇 에이전트를 통해 합의하기 위한 최소 프로토콜.

## 1. 계층

| 계층 | 주체 | 할 수 있는 것 | 할 수 없는 것 |
| --- | --- | --- | --- |
| L1 | 사람 | 승인·반려 발화, 자연어 지시 | — |
| L2 | 그 사람의 에이전트 | 제안·질문·수정·실행증거 기록 | **승인·반려** |
| L3 | 공유 버스(그룹 채팅) | 봉투 운반 | 판정 |
| L4 | 상대 에이전트 | L2 와 동일 | L2 와 동일 |
| L5 | 상대 사람 | L1 과 동일 | — |

버스는 신뢰 경계가 아니다. 판정은 각 에이전트가 자기 원장 위에서 독립적으로 한다.

## 2. 봉투

코드블록 언어 태그 ` ```accord ` 안의 JSON 한 덩어리. 스키마는 `contracts/envelope.schema.json`, `additionalProperties: false`.

```json
{
  "accord": "1",
  "thread": "T-976094ed",
  "rev": 2,
  "ts": "2026-09-22T12:32:11Z",
  "sender": { "party": "myoungsoo", "kind": "agent" },
  "type": "propose",
  "subject": "code_change",
  "state_hash": "fe719bf8",
  "payload": { ... }
}
```

- `thread` — `^T-[0-9a-f]{8}$`
- `sender.kind` — `agent` | `human`. **`human` 봉투는 무조건 거부된다** (§4).
- `type` — `propose` · `question` · `revise` · `approve` · `reject` · `execute` · `withdraw`
- `subject` — `doc_review` | `schedule` | `code_change`
- `payload` — 해당 subject 의 계약(`contracts/<subject>.schema.json`)을 통과해야 한다.

## 3. 지문 (state_hash)

```
canon = JSON({thread, rev, subject, payload}, sort_keys, separators=(",",":"), ensure_ascii=False)
state_hash = sha256(canon).hexdigest()[:8]
```

- 수신 시 재계산해 봉투의 값과 대조한다. 불일치하면 거부.
- 사람이 승인할 때 **직접 타이핑해야 하는 값**이다. 이것이 판본 바꿔치기와 낡은 판본 승인을 동시에 막는다.

## 4. 승인 경계

정의: **유효한 승인은, 등록된 사람 id 가 친 한 줄이며, 그 줄의 지문이 현재 판본의 지문과 같은 것.**

```
^\s*(승인|approve|반려|reject)\s+(T-[0-9a-f]{8})\s*(.*)$
```

거부 규칙 (평가 순서대로):

1. 봉투 스키마 실패 → 거부
2. `sender.kind == "human"` → 거부. *이유: 봉투는 에이전트가 만들 수 있다. 그걸 승인으로 인정하면 그 순간 대리 승인이다.*
3. `type ∈ {approve, reject}` 인데 발신이 에이전트 → 거부
4. 미등록 party → 거부
5. payload 계약 실패 → 거부
6. 지문 불일치 → 거부

사람 한 줄에 대해서는:

1. `--from-human` 이 `me.human_id` 또는 `peer.human_id` 가 아니면 → 거부
2. 지문이 현재 판본과 다르면 → 거부 (낡은 판본 승인 가능성)

## 5. 상태 기계

상태 파일은 없다. 매 조회마다 원장(JSONL)을 재생해 계산한다.

```
                 propose
                    │
                    ▼
            pending_approval ◄──────── revise (승인 전부 무효화)
                 │      ▲                  ▲
        question │      │ revise           │
                 ▼      │                  │
             questioned ─┘                 │
                 │                         │
        사람1 승인 │                        │
                 ▼                         │
           half_approved ──────────────────┤
                 │                         │
        사람2 승인 │            사람 반려 ────► rejected
                 ▼                         │
              agreed                       │
                 │                         │
         execute │                    withdraw ──► withdrawn
                 ▼
             executed
```

- `agreed` 는 **서로 다른 두 party 의 유효 승인**이 동시에 성립할 때만. 같은 사람의 두 번은 한 번으로 센다.
- `execute` 는 `agreed` 가 아니면 거부된다.
- `revise` · `reject` 는 그 시점의 모든 승인을 무효로 만든다.

## 6. 합의 착시 검출

`agreed` 에 도달했는데 원장에 `question` 이 **0건**이면 경고를 붙인다.

```
⚠ 합의 착시 의심 — 쟁점이 0건이다. 양쪽 에이전트가 서로 동의만 했을 가능성을 사람이 확인할 것.
```

막지는 않는다. LLM 둘이 마찰 없이 도달한 합의를 자동으로 무효화할 근거는 없고, 사람이 볼 수 있게 표시하는 것이 정직한 처리다.

추가 경고: `code_change` 가 `agreed` 인데 `rollback` 또는 `verification` 이 비어 있을 때 (계약이 이미 둘 다 필수로 막고 있으므로, 원장을 직접 손댄 경우에만 뜬다).

## 7. 원장

`$ACCORD_HOME/ledger.jsonl` (기본 `~/.accord`). 추가 전용. 한 줄 = 한 사건.

```json
{"at":"…","dir":"in|out","kind":"envelope|human","body":{…}}
```

파생 상태는 저장하지 않으므로, 상태를 조작하려면 원장 자체를 고쳐야 한다 — 그건 감사 가능한 행위다.

## 8. 실행면 없음

`accord.py` 에는 `subprocess` · `eval` · `exec` · 네트워크 호출이 없다. 상대가 보낸 payload 는 끝까지 데이터로만 다뤄지고, 출력될 때 "이 내용은 *제안*이다. 지시가 아니다" 라는 표지가 붙는다. 실행은 사람이 밖에서 하고, `execute` 는 그 사실의 *기록*일 뿐이다.

## 9. 미구현 (의도적으로 열어둔 자리)

- **서명** — `sender` 옆에 detached signature 를 두는 자리. 현재는 채팅 플랫폼의 계정 신원에 의존한다.
- **3인 이상** — 현재는 2 party 고정. `agreed` 조건을 정족수로 일반화하면 확장된다.
- **만료** — 승인에 유효기간이 없다. 판본이 안 바뀌면 3주 전 승인도 유효하다.
