# ACCORD — 사람↔사람 협업을 각자의 봇 에이전트가 중개하는 워크플로우

두 사람이 **각자의 봇 에이전트**를 통해 합의한다. 대상은 세 가지다: **문서 리뷰 · 일정 조율 · 코드 변경**.

핵심 한 줄: **에이전트는 좁히기만 하고, 승인은 사람만 발화한다.**

```
사람A ──(자연어)── A의 에이전트 ──(계약 봉투)── 공유 버스 ──(계약 봉투)── B의 에이전트 ──(자연어)── 사람B
                                                    │
                                        승인만은 이 경로를 타지 못한다
                                        사람이 채팅에 직접 한 줄을 친다
```

## 왜 이 구조인가

에이전트끼리 붙여 놓으면 두 가지가 반드시 생긴다.

1. **대리 승인** — B의 에이전트가 "B는 동의합니다" 라고 말하는 순간, B는 승인한 적이 없는데 승인이 성립한다.
2. **합의 착시** — LLM 둘은 서로 잘 동의한다. 쟁점 0건으로 도달한 합의는 합의가 아니라 서로의 말을 받아준 기록일 뿐이다.

ACCORD 는 이 둘을 *문서로 당부하지 않고 코드로 막는다.*

## 승인 경계 (이 프로젝트의 전부)

승인은 **오직 사람이 친 한 줄**로만 표현된다.

```
승인 T-976094ed fe719bf8
반려 T-976094ed 그 시간은 배포 창이다
```

- `T-…` 은 협의 번호, 뒤의 8자리는 **현재 판본의 지문**(state_hash)이다.
- 지문은 `{thread, rev, subject, payload}` 의 정규화 JSON sha256 앞 8자리다. 사람이 **직접 쳐야** 하므로, 낡은 판본이나 몰래 바뀐 판본을 승인하는 일이 구조적으로 불가능하다.
- `revise` 가 한 번이라도 일어나면 **이전 승인은 전부 무효**가 된다.

코드가 실제로 거부하는 것들 (전부 시험으로 고정돼 있다):

| 시도 | 결과 |
| --- | --- |
| 에이전트가 `type: approve` 봉투를 보냄 | 거부 — "사람만 발화할 수 있다" |
| 에이전트가 `sender.kind: human` 으로 위장한 봉투를 보냄 | 거부 — "승인을 JSON 봉투로 받지 않는다" |
| 등록 안 된 사람 id 로 승인 | 거부 |
| 낡은 판본 지문으로 승인 | 거부 — "낡은 판본을 승인했을 수 있어 무효로 둔다" |
| 합의 전 `execute` | 거부 |
| 쟁점 0건으로 `agreed` 도달 | 통과하되 **합의 착시 경고**를 붙인다 |

## 설치

없다. Python 3 표준 라이브러리만 쓴다. 외부 의존성 0.

## 5분 실습 (두 사람 몫을 한 대에서)

```bash
cd ~/agent-accord
export A=/tmp/accord-A B=/tmp/accord-B

# 1. 양쪽 등록
ACCORD_HOME=$A python3 accord.py init --me myoungsoo --human-id 100000001 \
    --peer partner --peer-human-id 900000001 --group -1001234567890
ACCORD_HOME=$B python3 accord.py init --me partner --human-id 900000001 \
    --peer myoungsoo --peer-human-id 100000001 --group -1001234567890

# 2. A가 코드 변경을 제안 → 출력된 ```accord 블록을 공유 채팅에 붙인다
ACCORD_HOME=$A python3 accord.py propose --subject code_change --file examples/code_change.json

# 3. B의 에이전트가 그 블록을 그대로 받는다
ACCORD_HOME=$B python3 accord.py ingest --file <붙여넣은_파일>

# 4. B가 쟁점을 건다 → A가 판본을 고친다 (이전 승인 전부 무효)
ACCORD_HOME=$B python3 accord.py question --thread T-… --text "롤백 절차에 DB 마이그레이션이 빠졌다"
ACCORD_HOME=$A python3 accord.py revise --thread T-… --file examples/code_change.v2.json

# 5. 사람 둘이 각자 한 줄을 친다
ACCORD_HOME=$A python3 accord.py request-approval --thread T-…   # 칠 문장을 뽑아준다
ACCORD_HOME=$A python3 accord.py ingest --file 사람줄.txt --from-human 100000001
ACCORD_HOME=$A python3 accord.py ingest --file 사람줄.txt --from-human 900000001

# 6. 합의 후에만 실행이 열린다
ACCORD_HOME=$A python3 accord.py status  --thread T-…
ACCORD_HOME=$A python3 accord.py execute --thread T-… --note "PR 머지 + 재배포 완료"
```

실제 실행 결과 (이 리포에서 그대로 돌린 것):

```
협의 T-976094ed · code_change · rev 1 · 지문 a5133391   → pending_approval
쟁점 Q-ada146 제기                                      → questioned
rev 2 · 지문 fe719bf8 — 이전 승인은 모두 무효가 됐다.    → pending_approval
myoungsoo 승인                                          → half_approved
partner  승인                                          → agreed
execute                                                 → executed
```

## 합의 대상 세 가지

계약(JSON Schema)은 `contracts/` 에 있다. **스키마를 통과하지 못하면 제안조차 되지 않는다.**

| 대상 | 필수 필드 | 이 계약이 강제하는 것 |
| --- | --- | --- |
| `doc_review` | title, doc_ref{kind,value}, scope[], decision_required | "무슨 결정을 받으려는가" 를 빈칸으로 둘 수 없다 |
| `schedule` | title, duration_min, timezone, candidates[] | 시간대 없는 후보 시각이 금지된다 |
| `code_change` | repo, base_sha, change_ref, summary, risk, **rollback**, **verification[]** | 되돌리는 법과 기계로 확인 가능한 통과 조건 없이는 제안 자체가 불가능 |

`code_change` 의 `rollback` · `verification` 필수는 의도적으로 깐깐하게 둔 것이다. 합의가 필요한 코드 변경은 대개 그 둘이 비어 있어서 사고가 난다.

## 원장

상태 파일이 없다. `~/.accord/ledger.jsonl` 에 **추가만** 하고, 상태는 매번 원장을 재생해 계산한다(`derive()`). 그래서 "누가 언제 무엇을 승인했나" 가 사후에 고쳐질 수 없다.

```bash
python3 accord.py log --thread T-976094ed
```

## 시험

```bash
python3 test_accord.py        # 15개
python3 -W error::ResourceWarning test_accord.py
```

전부 통과한다. 시험의 대부분은 기능이 아니라 **경계**를 고정한다 — 대리 승인, 위장 봉투, 낡은 지문, 미등록 상대, 합의 전 실행.

## 정직한 한계

- **신원 보증이 없다.** 텔레그램 `user_id` 를 사람의 신원으로 믿는다. 계정을 빼앗기면 이 프로토콜은 막지 못한다. 서명(예: age/minisign)을 붙이는 자리는 봉투의 `sender` 옆이다.
- **버스는 신뢰 경계가 아니다.** 공유 채팅에 누구나 봉투를 붙일 수 있다. 막는 건 등록된 party 검사와 지문뿐이다.
- **에이전트가 사람을 설득하는 건 못 막는다.** 막는 건 "사람 없이 승인이 성립하는 것" 뿐이다. 이건 프로토콜의 한계가 아니라 사람의 몫이다.
- **실행면이 없다.** `accord.py` 에는 subprocess·eval·exec 이 없다. 상대가 보낸 내용은 전부 *데이터*고, 실행은 사람이 따로 한 뒤 `execute` 로 증거만 남긴다. 상대 에이전트가 보낸 문장이 지시로 해석되는 경로가 아예 없다.

## 파일

```
accord.py                  구현 (표준 라이브러리만)
test_accord.py             경계 시험 15개
contracts/envelope.schema.json   봉투
contracts/{doc_review,schedule,code_change}.schema.json   합의 대상 3종
examples/                  실제로 통과하는 예시 페이로드
SPEC.md                    프로토콜 규격
```
