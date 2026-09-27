# 요구사항 문서 — ACCORD v2

## 소개

ACCORD 는 두 사람이 각자의 봇 에이전트를 통해 합의하는 프로토콜이고, `accord.py` 는 그 구현체다. v2 는 **2인 구조를 그대로 두고** 세 가지를 한다.

1. **v1 경계 구멍 봉합** — 현재 `accord.py` 로 재현된 P1–P8, 그리고 이 문서를 쓰며 코드 판독으로 찾은 P9·P10.
2. **SSH 서명** — 사람이 accord 가 출력한 정규 승인 문장에 자기 SSH 키로 *직접* `ssh-keygen -Y sign -n accord` 를 돌려 서명하고, `accord.py` 는 SSHSIG 서명을 순수 파이썬으로 *검증만* 한다. 키를 등록한 상대에 한해 SPEC §4.1 의 "검증 불가 전언" 이 닫힌다.
3. **협의별 승인 만료** — 판본마다 승인 유효기간(기본 72시간)을 두고 지문에 넣는다. 만료된 승인은 세지 않는다.

v2 뒤에도 지켜야 하는 불변식: 표준 라이브러리만(외부 의존성 0), `accord.py` 에 실행면 없음, 승인·반려는 사람만, 추가 전용 원장 + `derive()` 재계산, 기존 18개 시험 통과.

범위 밖: 3인 이상 정족수, 키 등록 이상의 신원 보증, 실행면 추가, 외부 의존성 추가.

### 구멍 대응표

| 구멍 | v1 에서 일어나는 일 | 확인 방법 | 닫는 요구사항 |
| --- | --- | --- | --- |
| P1 | 상대의 execute 봉투가 합의 전에도 수신되어 state=executed (derive 에서 executed 가 agreed 보다 우선) | 재현 | 5, 11 |
| P2 | 없는 협의 번호의 question 이 거부(rc=2)되지만 원장에 남고, 이후 전체 status 가 "제안 없이 시작된 협의다" 로 실패 | 재현 | 3 |
| P3 | 기존 협의 번호로 propose 를 다시 보내면 rev 1 그대로 payload 가 바뀜 / revise 가 rev 1→99 로 건너뜀 | 재현 | 4, 11 |
| P4 | 등록되지 않은 human_id 를 단 approve_relay 가 상대 승인으로 세짐 | 재현 | 6, 11 |
| P5 | 공백 한 칸만 바꾼 revise 로 모든 쟁점이 해소되고, 합의 착시 경고도 사라짐 | 재현 | 7 |
| P6 | 미해소 쟁점이 열린 채 agreed 가 되고 경고가 없음 | 재현 | 8 |
| P7 | 상대가 *내가 연* 협의를 withdraw 할 수 있고, withdraw 명령이 없음 | 재현 | 9, 11 |
| P8 | 반려 뒤 판본 변경 없이 같은 판본을 다시 승인하면 agreed 로 돌아감 | 재현 | 10, 11 |
| P9 | `relay --reject` 가 내 사람의 반려 줄 없이 반려 전언을 만들고, 그 행이 내 원장에서도 반려로 세짐 | 코드 판독 (`cmd_relay` 의 `and not a.reject`, `derive` 의 reject_relay 처리). 실행 재현은 안 함 | 6 |
| P10 | 내가 보낸 approve_relay 행이 내 원장에서 내 승인을 "전언" 으로 덮어써, 내 쪽에 "상대 사람의 승인(나)은 … 전언이다" 라는 틀린 경고가 붙음 | 코드 판독 (`derive` 가 전언의 발신 party 를 가리지 않음). 실행 재현은 안 함 | 6 |

### 이 문서가 정한 세부 기본값 (검토 대상)

- 허용 오차는 5분이다.
- 승인 유효기간은 1–720 사이의 시간 단위 정수다. propose 에서 지정하지 않으면 72, revise 에서 지정하지 않으면 직전 판본 값을 잇는다.
- 봉투의 subject 는 협의의 subject 와 같아야 하고, propose 의 rev 는 1 이어야 한다 (P3 의 연장).
- executed·withdrawn 은 종결 상태다. executed 뒤에는 실행된 지문에 대한 execute·approve_relay 만 받는다. 양쪽이 각자 실행 증거를 남기는 경우와, 늦게 도착한 승인 전언을 위해서다.
- 지문이 현재 지문과 다른 전언은 기록하지 않고 거부한다. v1 은 기록한 뒤 "무효화된 승인" 으로 셌다.
- 쟁점 번호는 협의 안에서 유일해야 한다.
- 보안키(sk) 서명은 사용자 존재(user presence) 플래그가 켜져 있어야 유효하다.
- 내 사람 키가 등록되지 않았는데 서명 블록이 붙은 사람 줄은 거부한다. 검증하지 못한 서명을 실어 보내지 않기 위해서다.
- 재생 판정은 재생 시점의 설정(등록 키)을 기준으로 한다. 키를 나중에 등록하면 그 전의 서명 없는 승인은 소급해 세지 않는다 (v1 의 misrecorded 소급 무효와 같은 방식).
- 판본 내용 비교에는 승인 유효기간도 들어간다. 유효기간만 바꾼 revise 는 내용 없는 revise 가 아니다.

## 용어집

- **Accord**: `accord.py` 모듈 전체. 아래 구성요소를 모두 포함한다.
- **Accord_CLI**: `accord.py` 의 명령행 인터페이스. 명령은 init, propose, question, revise, request-approval, relay, ingest, execute, withdraw, status, log.
- **Ingest**: `ingest` 명령의 수신 처리부. 공유 버스에서 받은 봉투나 사람 줄을 검증하고, 통과한 것만 원장에 기록한다.
- **Derive**: 원장을 재생해 협의 하나의 상태를 계산하는 함수 `derive()`.
- **Sig_Verifier**: 서명 블록(SSHSIG)을 해석하고 등록 공개키로 검증하는 Accord 내부 구성요소.
- **Ed25519_Verifier**: Sig_Verifier 가 쓰는 순수 파이썬 Ed25519 서명 검증기(RFC 8032).
- **Envelope_Schema**: `contracts/envelope.schema.json`.
- **Test_Suite**: `test_accord.py` 와, 시험에서만 쓰는 도우미 코드.
- **Spec_Doc**: `SPEC.md`.
- **Readme**: `README.md`.
- **원장**: `$ACCORD_HOME/ledger.jsonl`. 한 줄 = 한 사건인 추가 전용 JSONL.
- **협의**: `T-` 와 16진 8자리 협의 번호로 식별되는 합의 단위.
- **party**: 협의에 참여하는 쪽의 식별자. 설정의 `me.party`(나)와 `peer.party`(상대) 둘뿐이다.
- **내 사람 / 상대 사람**: 설정의 `me.human_id` / `peer.human_id` 로 등록된 사람.
- **협의를 연 party**: 그 협의의 propose 봉투를 보낸 party.
- **봉투**: ` ```accord ` 코드블록 안의 JSON 한 덩어리. `accord` 값이 "1" 이면 v1 봉투, "2" 이면 v2 봉투.
- **사람 줄**: 사람이 채팅에 직접 친, `승인 <협의 번호> <지문>` 또는 `반려 <협의 번호> …` 로 시작하는 입력. 서명된 경우 정규 승인 문장과 서명 블록으로 이루어진다.
- **전언**: approve_relay(승인 전언) 또는 reject_relay(반려 전언) 봉투.
- **발신 기록**: 내 party 가 보낸 봉투가 내 원장에 남은 행.
- **판본**: propose(rev 1) 또는 revise(rev n) 로 만들어진 협의의 내용. **현재 판본**은 원장에 마지막으로 유효하게 기록된 propose·revise 다.
- **판본 내용**: 지문 계산에 들어가는 값 가운데 thread·rev 를 뺀 것. subject, payload, 승인 유효기간이다.
- **지문**: 판본의 정규 JSON 을 sha256 으로 해시한 16진 앞 8자리. **현재 지문**은 현재 판본의 지문이다.
- **쟁점**: question 봉투로 제기되어 쟁점 번호(issue_id)를 가진 것. **미해소 쟁점**은 아직 어떤 유효 revise 의 해소 목록에도 오르지 않은 쟁점이다.
- **해소 목록**: revise 봉투가 해소한다고 지목한 쟁점 번호의 목록.
- **정규화**: payload 안의 모든 문자열 값(중첩된 객체·배열 안 포함)에서 앞뒤 공백을 지우고, 연속된 공백을 한 칸으로 바꾸는 변환.
- **내용 없는 revise**: 정규화한 판본 내용이 직전 판본의 정규화한 판본 내용과 같은 revise.
- **등록 공개키**: init 에서 내 사람 또는 상대 사람의 것으로 등록한 OpenSSH 공개키 한 줄.
- **지원 키 종류**: `ssh-ed25519` 와 `sk-ssh-ed25519@openssh.com`(하드웨어 보안키).
- **정규 승인 문장**: accord 가 정한 형식으로 승인 또는 반려를 적은 여러 줄 텍스트. 사람이 서명하는 대상이다 (요구사항 13).
- **서명 블록**: `-----BEGIN SSH SIGNATURE-----` 로 시작해 `-----END SSH SIGNATURE-----` 로 끝나는, `ssh-keygen -Y sign` 이 만든 텍스트.
- **서명된 승인·반려**: 정규 승인 문장과, 그 문장에 대해 등록 공개키로 검증되는 서명 블록이 함께 있는 사람 줄 또는 전언.
- **검증된 전언**: 실린 서명이 상대 사람의 등록 공개키로 검증된 전언.
- **검증 불가 전언**: 상대 사람의 공개키가 등록돼 있지 않아 서명을 검증하지 않은 채 센 전언.
- **승인 유효기간**: 판본에 대한 승인이 유효한 시간. 1 이상 720 이하의 시간 단위 정수이며 기본값은 72.
- **기록 시각**: 이 에이전트가 자기 시계로 잰, 행을 원장에 기록한 UTC 시각. 기록 시각 필드가 없는 v1 행은 그 행의 `ts` 를 기록 시각으로 본다.
- **서명 시각**: 정규 승인 문장에 적힌 UTC 시각.
- **승인 시각**: 서명된 승인은 서명 시각, 서명 없는 승인은 기록 시각.
- **평가 시각**: Derive 가 상태를 계산하는 기준 시각. 기본은 현재 UTC 시각이며 시험에서 주입할 수 있다.
- **허용 오차**: 미래 시각을 받아들이는 한도. 5분.
- **유효 승인**: 현재 지문에 대한 승인 가운데 권한·서명·반려·만료 조건(요구사항 6, 10, 15, 16, 18, 19)을 모두 만족하는 것.
- **유효 반려**: 현재 판본에 대한 반려 가운데 권한·서명 조건을 만족하는 것.
- **agreed(합의)**: 평가 시각에 서로 다른 두 party 의 유효 승인이 동시에 성립한 상태.
- **유효 실행 기록**: 기록 시각에 그 협의가 agreed 또는 executed 였고, state_hash 가 그때의 현재 지문과 같은 execute 행.
- **종결 상태**: executed 와 withdrawn.
- **손상된 협의**: 원장에 행은 있지만 유효한 propose 가 없어 Derive 가 상태를 계산할 수 없는 협의.
- **수신 규칙**: 이 문서가 Ingest 에 요구하는 모든 검증 규칙.
- **거부**: 명령이 종료 코드 2 로 끝나고, 표준 오류에 `거부:` 로 시작하는 사유를 출력하며, 원장과 설정 파일을 명령 실행 전과 바이트 단위로 같게 두는 것.
- **경고**: Derive 결과의 `warnings` 에 들어가고 상태 출력에 `⚠` 로 표시되는 문장. 판정을 막지 않는다.

## 요구사항

### 요구사항 1: 불변식 유지

**User Story:** ACCORD 를 쓰는 사람으로서, v2 가 기능을 더해도 v1 의 안전 원칙이 그대로이기를 원한다. 그래야 새 기능이 새 공격면이 되지 않는다.

#### Acceptance Criteria

1. THE Accord SHALL Python 3 표준 라이브러리 모듈만 가져온다.
2. THE Accord SHALL subprocess 가져오기, os.system·os.popen·os.exec 계열·os.spawn 계열 호출, eval·exec 호출, 네트워크 모듈(socket, ssl, urllib, http 등) 가져오기가 하나도 없는 상태를 유지한다.
3. THE Accord SHALL 서명 검증 기능만 갖고, 서명을 만드는 명령·함수와 개인키를 읽는 경로가 없는 상태를 유지한다.
4. IF 받은 봉투의 type 이 approve 또는 reject 이거나 sender.kind 가 human 이면, THEN THE Ingest SHALL 해당 봉투를 거부한다.
5. THE Accord SHALL 승인·반려를 내 사람의 사람 줄과 상대 에이전트의 전언, 이 두 경로로만 센다.
6. THE Accord SHALL 원장에 행을 추가만 하고, 이미 있는 행은 바꾸지 않는다.
7. THE Derive SHALL 협의 상태를 매 조회마다 원장 재생으로만 계산하고, 파생 상태를 파일에 저장하지 않는다.

### 요구사항 2: v1 호환

**User Story:** v1 로 협의해 온 사람으로서, v2 로 올려도 기존 원장과 시험이 그대로 읽히기를 원한다. 그래야 기록이 끊기지 않는다.

#### Acceptance Criteria

1. THE Accord_CLI SHALL 새로 만드는 모든 봉투와 원장 행의 accord 값을 "2" 로 쓴다.
2. THE Ingest SHALL accord 값이 "1" 인 봉투와 "2" 인 봉투를 모두 받는다.
3. THE Envelope_Schema SHALL accord 값으로 "1" 과 "2" 를 허용하고, v2 가 더하는 필드(승인 유효기간, 해소 목록, 정규 승인 문장과 서명 블록, 기록 시각)를 정의하며, `additionalProperties: false` 를 유지한다.
4. WHEN Derive 가 v1 이 쓴 원장을 재생하면, THE Derive SHALL 오류 없이 상태를 계산한다.
5. THE Derive SHALL 결과에 v1 의 키(thread, rev, subject, payload, state_hash, state, approved_by, stale_approvals, relayed_approvals, misrecorded, open_issues, total_questions, rejects, executed, warnings, rows)를 v1 과 같은 뜻으로 유지하고, relayed_approvals 에는 검증 불가 전언으로 센 승인의 party 만 넣는다.
6. WHILE 설정에 등록 공개키가 하나도 없으면, THE Accord SHALL 서명 없는 사람 줄과 서명 없는 전언을 v1 과 같이 받되 요구사항 3–11 의 봉합 규칙을 적용하고, 전언에는 v1 의 검증 불가 경고를 붙인다.
7. THE Test_Suite SHALL 기존 18개 시험을 모두 통과시키며, 기존 시험의 수정은 `peer_human_relay` 도우미가 rev 를 1 로 고정해 보내던 것을 해당 협의의 현재 rev 를 싣도록 바꾸는 한 곳으로 한정한다.

> 2.7 의 근거: `test_issue_raised_then_no_warning` 은 rev 2 판본에 rev 1 전언을 보낸다. 이게 받아들여지는 건 P3 구멍이므로 v2 에서는 도우미가 현재 rev 를 실어야 한다. 이 시험의 단언(합의 착시 경고 없음)은 그대로 둔다.

### 요구사항 3: 원장 기록 전 검증 (P2)

**User Story:** 원장을 믿는 사람으로서, 거부된 메시지가 원장에 흔적을 남기지 않기를 원한다. 그래야 거부된 입력 하나가 이후의 판정과 조회를 망가뜨리지 못한다.

#### Acceptance Criteria

1. WHEN Ingest 가 봉투나 사람 줄을 받으면, THE Ingest SHALL 수신 규칙의 검증을 모두 끝낸 뒤에 원장에 기록한다.
2. IF 수신 규칙의 검증 가운데 하나라도 실패하면, THEN THE Ingest SHALL 해당 입력을 거부한다.
3. IF propose 가 아닌 봉투나 사람 줄의 협의 번호가 원장에 유효한 propose 로 존재하지 않으면, THEN THE Ingest SHALL 해당 입력을 거부하고 거부 사유에 "원장에 없는 협의" 임을 밝힌다.
4. IF 기록 명령(propose, question, revise, relay, execute, withdraw)이 검증에 실패하면, THEN THE Accord_CLI SHALL 해당 명령을 거부한다.
5. WHEN 협의 번호 없이 status 가 실행되고 원장에 손상된 협의가 있으면, THE Accord_CLI SHALL 손상되지 않은 협의마다 요약 줄을, 손상된 협의마다 협의 번호와 손상 사유를 담은 경고 줄을 출력하고 종료 코드 0 으로 끝난다.
6. WHEN 손상된 협의 하나를 지정해 status 가 실행되면, THE Accord_CLI SHALL 손상 사유를 밝혀 거부한다.

### 요구사항 4: 협의 번호와 판본 번호의 일관성 (P3)

**User Story:** 판본을 승인하는 사람으로서, 이미 있는 협의의 내용이 같은 rev 로 바꿔치기되거나 rev 가 건너뛰지 않기를 원한다. 그래야 지문이 가리키는 판본이 하나로 정해진다.

#### Acceptance Criteria

1. IF 받은 propose 봉투의 협의 번호가 이미 원장에 있으면, THEN THE Ingest SHALL 해당 봉투를 거부한다.
2. IF 받은 propose 봉투의 rev 가 1 이 아니면, THEN THE Ingest SHALL 해당 봉투를 거부한다.
3. IF 받은 revise 봉투의 rev 가 현재 rev + 1 이 아니면, THEN THE Ingest SHALL 해당 봉투를 거부하고 거부 사유에 기대한 rev 와 받은 rev 를 밝힌다.
4. IF 받은 question·approve_relay·reject_relay·execute·withdraw 봉투의 rev 가 현재 rev 와 다르면, THEN THE Ingest SHALL 해당 봉투를 거부한다.
5. IF 받은 봉투의 subject 가 그 협의 propose 의 subject 와 다르면, THEN THE Ingest SHALL 해당 봉투를 거부한다.
6. THE Accord_CLI SHALL propose 봉투는 원장에 없는 새 협의 번호와 rev 1 로, revise 봉투는 현재 rev + 1 로, 그 밖의 봉투는 현재 rev 로 만든다.

### 요구사항 5: 합의 전 실행 금지와 실행 뒤 종결 (P1)

**User Story:** 합의 당사자로서, 합의가 성립하지 않은 판본에 실행 기록이 붙지 않기를 원한다. 그래야 executed 가 "합의된 것을 실행했다" 는 뜻만 갖는다.

#### Acceptance Criteria

1. IF 받은 execute 봉투의 협의가 수신 평가 시각에 agreed 또는 executed 상태가 아니면, THEN THE Ingest SHALL 해당 봉투를 거부하고 거부 사유에 "합의 전 실행 기록" 임을 밝힌다.
2. IF 받은 execute 봉투의 state_hash 가 현재 지문과 다르면, THEN THE Ingest SHALL 해당 봉투를 거부한다.
3. IF execute 명령이 평가 시각에 agreed 또는 executed 가 아닌 협의에 대해 실행되면, THEN THE Accord_CLI SHALL 해당 명령을 거부한다.
4. THE Derive SHALL 유효 실행 기록이 1건 이상일 때만 협의 상태를 executed 로 계산한다.
5. IF 원장에 유효 실행 기록이 아닌 execute 행이 있으면, THEN THE Derive SHALL 해당 행을 상태 계산에서 빼고 무효 실행 기록의 건수를 담은 경고를 붙인다.
6. WHILE 협의가 executed 이면, THE Derive SHALL 이후의 승인 만료와 무관하게 상태를 executed 로 유지한다.
7. WHILE 협의가 executed 이면, THE Accord_CLI SHALL 그 협의에 대한 새 기록 요청(ingest 포함) 가운데 실행된 지문에 대한 execute 와 approve_relay 를 뺀 나머지를 거부한다.

### 요구사항 6: 전언의 권한 (P4, P9, P10)

**User Story:** 상대 승인을 전언으로 받는 사람으로서, 전언이 등록된 사람의 실제 발화에서만 나오기를 원한다. 그래야 어느 쪽 에이전트도 사람 없이 승인·반려를 만들 수 없다.

#### Acceptance Criteria

1. IF 받은 전언의 human_id 가 없거나 상대 사람 id 와 다르면, THEN THE Ingest SHALL 해당 전언을 거부한다.
2. IF 받은 전언의 state_hash 가 현재 지문과 다르면, THEN THE Ingest SHALL 해당 전언을 거부하고 거부 사유에 "낡은 판본에 대한 전언" 임을 밝힌다.
3. IF relay 명령이 승인 전달로 실행될 때 평가 시각에 현재 지문에 대한 내 사람의 유효 승인이 없으면, THEN THE Accord_CLI SHALL 해당 명령을 거부한다.
4. IF relay 명령이 반려 전달로 실행될 때 현재 판본에 대한 내 사람의 유효 반려가 원장에 없으면, THEN THE Accord_CLI SHALL 해당 명령을 거부한다.
5. WHEN relay 명령이 반려 전언을 만들면, THE Accord_CLI SHALL 전언의 사유를 내 사람이 기록한 반려의 사유로 채운다.
6. THE Derive SHALL 발신 기록인 전언 행을 승인·반려로 세지 않는다.
7. IF 원장에 상대 party 가 보낸 전언 가운데 human_id 가 상대 사람 id 와 다른 행이 있으면, THEN THE Derive SHALL 해당 행을 세지 않고 경고를 붙인다.

### 요구사항 7: 쟁점은 지목해서만 해소 (P5)

**User Story:** 쟁점을 건 사람으로서, 내 쟁점이 그 쟁점을 지목한 실제 판본 변경으로만 해소되기를 원한다. 그래야 공백만 바꾼 revise 가 쟁점을 지우지 못한다.

#### Acceptance Criteria

1. THE Accord_CLI SHALL revise 명령에서 해소할 쟁점 번호 목록을 받아 revise 봉투의 해소 목록에 싣는다.
2. WHEN 내용 없는 revise 가 아닌 유효 revise 가 기록되면, THE Derive SHALL 해당 revise 의 해소 목록에 있는 쟁점만 해소된 것으로 센다.
3. IF 받은 revise 봉투의 해소 목록에 그 협의에서 제기되지 않은 쟁점 번호가 있으면, THEN THE Ingest SHALL 해당 봉투를 거부한다.
4. IF revise 명령에 그 협의에서 제기되지 않은 쟁점 번호가 주어지면, THEN THE Accord_CLI SHALL 해당 명령을 거부한다.
5. IF 받은 question 봉투에 쟁점 번호가 없거나 그 쟁점 번호가 그 협의에서 이미 쓰였으면, THEN THE Ingest SHALL 해당 봉투를 거부한다.
6. WHEN 내용 없는 revise 가 기록되면, THE Derive SHALL 해당 revise 의 해소 목록을 적용하지 않고 "내용이 바뀌지 않은 revise(rev N) — 쟁점을 해소하지 못한다" 는 뜻의 경고를 붙인다.
7. THE Derive SHALL 판본 내용이 같은지를 정규화한 뒤의 정규 JSON(키 순서 무관)으로 판단한다.
8. WHEN 내용 없는 revise 가 기록되면, THE Derive SHALL 해당 revise 를 새 판본으로 세고 그 전의 승인을 무효로 둔다.

### 요구사항 8: 미해소 쟁점 합의 경고 (P6)

**User Story:** 합의를 확인하는 사람으로서, 풀리지 않은 쟁점이 남은 채 합의됐다면 그 사실을 보고 싶다. 그래야 쟁점을 덮은 합의를 알아챌 수 있다.

#### Acceptance Criteria

1. WHILE 협의가 agreed 또는 executed 이고 미해소 쟁점이 1건 이상이면, THE Derive SHALL 미해소 쟁점의 건수와 쟁점 번호를 담아 "미해소 쟁점이 열린 채 합의됐다" 는 뜻의 경고를 붙인다.
2. THE Derive SHALL 미해소 쟁점 경고를 합의 착시 경고와 다른 문장으로 두고, 미해소 쟁점 경고에는 "합의 착시" 라는 문구를 넣지 않는다.
3. WHILE 미해소 쟁점이 있으면, WHEN 서로 다른 두 party 의 유효 승인이 성립하면, THE Derive SHALL 상태를 agreed 로 계산한다 (막지 않고 드러낸다, SPEC §6).
4. WHILE 협의가 agreed 또는 executed 이고 원장의 쟁점이 0건이면, THE Derive SHALL v1 과 같은 합의 착시 경고를 붙인다.

### 요구사항 9: 철회는 협의를 연 쪽만 (P7)

**User Story:** 제안을 연 사람으로서, 내 제안을 나만 거둘 수 있기를 원한다. 그래야 상대 에이전트가 내 협의를 끝내 버리지 못한다.

#### Acceptance Criteria

1. THE Accord_CLI SHALL 협의 번호와 선택적 사유를 받는 withdraw 명령을 제공한다.
2. IF withdraw 명령이 협의를 연 party 가 아닌 쪽에서 실행되면, THEN THE Accord_CLI SHALL 해당 명령을 거부한다.
3. IF 받은 withdraw 봉투의 발신 party 가 협의를 연 party 가 아니면, THEN THE Ingest SHALL 해당 봉투를 거부한다.
4. IF 원장에 협의를 연 party 가 아닌 쪽의 withdraw 행이 있으면, THEN THE Derive SHALL 해당 행을 세지 않고 경고를 붙인다.
5. WHILE 협의가 withdrawn 이면, THE Accord_CLI SHALL 그 협의에 대한 새 기록 요청(ingest 포함)을 모두 거부한다.

### 요구사항 10: 반려 뒤 재승인 금지 (P8)

**User Story:** 반려한 사람으로서, 내 반려가 판본을 고치기 전까지 유지되기를 원한다. 그래야 같은 판본이 다시 승인되어 반려가 없던 일이 되지 않는다.

#### Acceptance Criteria

1. WHEN 현재 판본에 유효 반려가 기록되면, THE Derive SHALL 다음 유효 revise 가 기록될 때까지 상태를 rejected 로 계산한다.
2. WHILE 협의가 rejected 이면, THE Ingest SHALL 승인 사람 줄과 approve_relay 봉투를 거부하고, 거부 사유에 revise 로 새 판본을 올린 뒤 승인해야 한다는 안내를 담는다.
3. IF 원장에 같은 판본의 유효 반려 뒤에 기록된 승인 행(사람 줄 또는 전언)이 있으면, THEN THE Derive SHALL 해당 행을 세지 않고 경고를 붙인다.
4. WHILE 협의가 rejected 이면, THE Accord_CLI SHALL request-approval 에서 승인 문장 대신 revise 가 먼저 필요하다는 안내를 출력한다.
5. WHILE 협의가 rejected 이면, THE Ingest SHALL question, revise, 협의를 연 party 의 withdraw, reject_relay 를 수신 규칙에 따라 받는다.

### 요구사항 11: 재생 일관성

**User Story:** 원장을 감사하는 사람으로서, 원장을 재생한 결과가 수신 때의 판정과 같기를 원한다. 그래야 v1 시절 구멍으로 들어온 행이나 손으로 심은 행이 상태를 바꾸지 못한다.

#### Acceptance Criteria

1. WHEN Derive 가 원장을 재생하면, THE Derive SHALL 각 행을 그 행의 기록 시각과 재생 시점의 설정(사람 id, 등록 공개키)을 기준으로 수신 규칙과 같은 규칙으로 판정한다.
2. IF 원장에 수신 규칙을 어긴 행이 있으면, THEN THE Derive SHALL 해당 행을 상태 계산에서 빼고, 뺀 행의 건수와 사유를 경고로 붙인다.
3. WHEN Ingest 가 행을 기록하면, THE Ingest SHALL 수신 판정에 쓴 평가 시각을 그 행의 기록 시각으로 남기고, 받은 봉투에 이미 있는 필드의 값은 바꾸지 않는다.
4. WHILE 설정이 바뀌지 않았으면, THE Derive SHALL Ingest 와 Accord_CLI 가 기록한 모든 행을 재생에서 유효한 행으로 판정한다.
5. THE Derive SHALL 내 사람 id 가 아닌 사람 id 로 기록된 approve 행을 v1 과 같이 권한 없는 승인 기록(misrecorded)으로 세고 경고를 붙인다.

### 요구사항 12: 사람 공개키 등록

**User Story:** 서명으로 승인하려는 사람으로서, 나와 상대의 SSH 공개키를 등록하고 싶다. 그래야 에이전트가 서명을 등록된 키로만 검증한다.

#### Acceptance Criteria

1. WHERE init 에 내 사람 공개키가 주어지면, THE Accord_CLI SHALL 해당 키를 내 사람의 등록 공개키로 설정에 저장한다.
2. WHERE init 에 상대 사람 공개키가 주어지면, THE Accord_CLI SHALL 해당 키를 상대 사람의 등록 공개키로 설정에 저장한다.
3. IF 주어진 공개키가 OpenSSH 공개키 한 줄 형식(키 종류, base64 본체, 선택적 주석)이 아니거나, base64 본체 안의 키 종류가 앞에 적힌 키 종류와 다르거나, Ed25519 공개키 길이가 32바이트가 아니면, THEN THE Accord_CLI SHALL init 을 거부한다.
4. IF 주어진 공개키의 키 종류가 지원 키 종류가 아니면, THEN THE Accord_CLI SHALL init 을 거부하고, 거부 사유에 받은 키 종류 이름과 지원 키 종류 목록을 담는다.
5. WHEN init 이 끝나면, THE Accord_CLI SHALL 두 사람 각각의 공개키 등록 여부와, 등록된 키의 `SHA256:` 키 지문(`ssh-keygen -l` 과 같은 형식)을 출력한다.
6. WHERE init 에 공개키가 주어지지 않으면, THE Accord_CLI SHALL 해당 사람을 키 미등록으로 설정에 저장한다.

### 요구사항 13: 정규 승인 문장과 승인 요청

**User Story:** 승인하는 사람으로서, 무엇에 서명해야 하는지와 어떻게 서명하는지를 정확히 받고 싶다. 그래야 내가 직접 서명한 문장이 그대로 승인으로 검증된다.

#### Acceptance Criteria

1. THE Accord SHALL 정규 승인 문장을 다음 항목을 이 순서로 한 줄씩 담은 텍스트로 정의한다: 사람 줄 형식의 첫 줄(`승인 <협의 번호> <지문>` 또는 `반려 <협의 번호> <지문>`), 프로토콜 표지, rev, 사람 id, 서명 시각(`YYYY-MM-DDTHH:MM:SSZ`), 반려일 때만 사유 한 줄.
2. WHEN 내 사람의 공개키가 등록된 상태에서 request-approval 이 실행되면, THE Accord_CLI SHALL 서명 시각을 출력 시각으로 채운 현재 판본의 승인 문장과, 사유 자리를 비운 반려 문장 틀을 바이트 그대로 출력한다.
3. WHEN 내 사람의 공개키가 등록된 상태에서 request-approval 이 실행되면, THE Accord_CLI SHALL 사람이 직접 칠 서명 명령 예시(`ssh-keygen -Y sign -f <개인키 경로> -n accord <문장 파일>`)와, 문장과 서명 블록을 함께 채팅에 붙이라는 안내를 출력한다.
4. WHEN 내 사람의 공개키가 등록되지 않은 상태에서 request-approval 이 실행되면, THE Accord_CLI SHALL v1 과 같은 승인 한 줄과, 승인이 서명 없이 기록된다는 사실을 출력한다.
5. WHEN request-approval 이 실행되면, THE Accord_CLI SHALL 현재 판본의 승인 유효기간(시간)을 출력한다.
6. THE Accord_CLI SHALL 서명 명령 예시를 텍스트로만 출력하고, ssh-keygen 을 포함한 외부 프로그램을 호출하지 않는다.

### 요구사항 14: SSHSIG 해석과 Ed25519 검증

**User Story:** 서명된 승인을 받는 에이전트의 사람으로서, 서명이 외부 도구 없이 정확히 검증되기를 원한다. 그래야 실행면과 의존성을 늘리지 않고도 위조를 막는다.

#### Acceptance Criteria

1. WHEN Sig_Verifier 가 서명 블록을 받으면, THE Sig_Verifier SHALL 시작·끝 표지 사이의 base64 를 줄바꿈 위치와 줄 앞뒤 공백에 상관없이 읽어 SSHSIG 구조(표지 `SSHSIG`, 버전 1, 공개키, 이름공간, 예약 필드, 해시 알고리즘, 서명)로 해석한다.
2. IF 서명 블록이 SSHSIG 구조로 해석되지 않으면(표지·버전 불일치, 깨진 base64, 잘린 필드), THEN THE Sig_Verifier SHALL 서명을 무효로 판정한다.
3. IF 이름공간이 정확히 `accord` 가 아니면, THEN THE Sig_Verifier SHALL 서명을 무효로 판정한다.
4. IF 해시 알고리즘이 `sha512` 나 `sha256` 이 아니면, THEN THE Sig_Verifier SHALL 서명을 무효로 판정한다.
5. IF 서명 블록 안의 공개키가 해당 사람의 등록 공개키와 바이트 단위로 같지 않으면, THEN THE Sig_Verifier SHALL 서명을 무효로 판정한다.
6. IF 서명 블록 안의 키 종류가 지원 키 종류가 아니면, THEN THE Sig_Verifier SHALL 서명을 무효로 판정하고, 무효 사유에 받은 키 종류 이름과 지원 키 종류 목록을 담는다.
7. WHEN 키 종류가 ssh-ed25519 이면, THE Sig_Verifier SHALL SSHSIG 규격의 서명 대상 데이터(표지, 이름공간, 예약 필드, 해시 알고리즘, 문장 해시)를 Ed25519_Verifier 로 검증한다.
8. WHEN 키 종류가 sk-ssh-ed25519@openssh.com 이면, THE Sig_Verifier SHALL OpenSSH 보안키 서명 형식(응용 이름의 sha256, 플래그, 카운터, 서명 대상 데이터의 sha256)을 Ed25519_Verifier 로 검증한다.
9. IF 보안키 서명의 사용자 존재(user presence) 플래그가 꺼져 있으면, THEN THE Sig_Verifier SHALL 서명을 무효로 판정한다.
10. THE Ed25519_Verifier SHALL RFC 8032 의 Ed25519 검증을 순수 파이썬으로 수행하고, S 가 군의 위수 L 이상인 서명과 디코딩할 수 없는 점 인코딩을 무효로 판정한다.
11. THE Sig_Verifier SHALL 받은 정규 승인 문장의 바이트로 서명을 검증하되, 줄 끝 형식(LF·CRLF)과 마지막 줄바꿈 유무의 차이만 같은 문장으로 인정한다.
12. IF 받은 문장이 정규 승인 문장 형식과 맞지 않으면(항목 누락·추가·순서 바뀜·값 형식 오류), THEN THE Ingest SHALL 해당 입력을 거부한다.
13. IF Sig_Verifier 가 서명을 무효로 판정하면, THEN THE Ingest SHALL 파이썬 예외 추적 없이 무효 사유를 밝혀 해당 입력을 거부한다.

### 요구사항 15: 내 사람의 서명된 승인·반려

**User Story:** 키를 등록한 사람으로서, 내 서명 없이는 내 승인·반려가 기록되지 않기를 원한다. 그래야 내 에이전트가 `--from-human` 을 지어내도 승인이 성립하지 않는다.

#### Acceptance Criteria

1. WHILE 내 사람의 공개키가 등록돼 있으면, THE Ingest SHALL 내 사람의 사람 줄을 서명된 승인·반려일 때만 기록한다.
2. IF 내 사람의 공개키가 등록된 상태에서 받은 사람 줄에 서명 블록이 없거나 서명이 무효이면, THEN THE Ingest SHALL 해당 입력을 거부하고, 거부 사유에 서명이 필요하다는 것과 request-approval 로 문장을 받으라는 안내를 담는다.
3. IF 정규 승인 문장의 협의 번호·rev·지문이 현재 판본과 다르거나 사람 id 가 내 사람 id 와 다르면, THEN THE Ingest SHALL 해당 입력을 거부한다.
4. THE Ingest SHALL `--from-human` 이 내 사람 id 와 같아야 한다는 v1 규칙을 서명 여부와 무관하게 적용한다.
5. WHEN Ingest 가 서명된 사람 줄을 기록하면, THE Ingest SHALL 정규 승인 문장과 서명 블록을 바꾸지 않고 원장 행에 담는다.
6. IF 내 사람의 공개키가 등록되지 않은 상태에서 받은 사람 줄에 서명 블록이 붙어 있으면, THEN THE Ingest SHALL 해당 입력을 거부하고, 거부 사유에 검증할 등록 공개키가 없으니 init 으로 등록하라는 안내를 담는다.
7. WHILE 내 사람의 공개키가 등록돼 있으면, THE Derive SHALL 원장의 내 사람 승인·반려 행 가운데 서명이 없거나 재검증에 실패한 행을 세지 않고 경고를 붙인다.

### 요구사항 16: 서명된 전언

**User Story:** 상대 승인을 받는 사람으로서, 상대가 키를 등록했다면 상대 사람이 직접 서명한 전언만 승인으로 세고 싶다. 그래야 상대 에이전트가 상대 사람의 승인을 지어낼 수 없다.

#### Acceptance Criteria

1. WHEN relay 명령이 서명된 승인·반려를 전달하면, THE Accord_CLI SHALL 해당 기록의 정규 승인 문장과 서명 블록을 바꾸지 않고 전언에 싣는다.
2. WHILE 상대 사람의 공개키가 등록돼 있으면, THE Ingest SHALL 실린 서명이 상대 사람의 등록 공개키로 검증되는 전언만 기록한다.
3. IF 상대 사람의 공개키가 등록된 상태에서 받은 전언에 서명이 없으면, THEN THE Ingest SHALL 해당 전언을 거부하고, 거부 사유에 상대 키가 등록돼 있어 서명 없는 전언은 세지 않는다는 것을 밝힌다.
4. IF 전언의 서명이 무효이거나, 정규 승인 문장의 협의 번호·rev·지문·사람 id·행위가 전언의 thread·rev·state_hash·human_id·type 과 맞지 않으면, THEN THE Ingest SHALL 해당 전언을 거부한다.
5. WHEN 서명이 검증된 전언이 기록되면, THE Derive SHALL 해당 전언을 검증된 전언으로 세고 검증 불가 경고를 붙이지 않는다.
6. WHILE 상대 사람의 공개키가 등록돼 있지 않으면, THE Derive SHALL 받은 전언을 서명 유무와 무관하게 검증 불가 전언으로 세고 v1 의 검증 불가 경고를 붙인다.
7. WHILE 상대 사람의 공개키가 등록돼 있으면, THE Derive SHALL 원장의 상대 전언 가운데 서명이 없거나 재검증에 실패한 행을 세지 않고 경고를 붙인다.
8. WHEN status 가 협의 하나를 출력하면, THE Accord_CLI SHALL 센 전언마다 검증된 전언인지 검증 불가 전언인지 표시한다.

### 요구사항 17: 협의별 승인 유효기간과 지문

**User Story:** 제안하는 사람으로서, 협의마다 승인 유효기간을 정하고 그 값이 지문에 묶이기를 원한다. 그래야 양쪽이 같은 만료를 계산하고, 누구도 유효기간을 몰래 바꿀 수 없다.

#### Acceptance Criteria

1. THE Accord_CLI SHALL propose 와 revise 명령에서 승인 유효기간을 1 이상 720 이하의 시간 단위 정수로 받는다.
2. WHERE propose 명령에 승인 유효기간이 지정되지 않으면, THE Accord_CLI SHALL 72시간을 쓴다.
3. WHERE revise 명령에 승인 유효기간이 지정되지 않으면, THE Accord_CLI SHALL 직전 판본의 승인 유효기간을 쓴다.
4. THE Accord_CLI SHALL 새로 만드는 propose·revise 봉투에 승인 유효기간 필드를 싣는다.
5. WHEN propose·revise 봉투에 승인 유효기간 필드가 있으면, THE Accord SHALL 지문을 {thread, rev, subject, payload, 승인 유효기간} 의 정규 JSON 으로 계산한다.
6. WHEN propose·revise 봉투에 승인 유효기간 필드가 없으면, THE Accord SHALL v1 지문 공식({thread, rev, subject, payload})으로 지문을 계산하고 그 판본의 승인 유효기간을 72시간으로 본다.
7. IF 받은 봉투의 승인 유효기간이 1 이상 720 이하의 정수가 아니면, THEN THE Ingest SHALL 해당 봉투를 거부한다.
8. IF 받은 봉투의 state_hash 가 승인 유효기간을 포함해 다시 계산한 지문과 다르면, THEN THE Ingest SHALL 해당 봉투를 거부한다.

### 요구사항 18: 만료 판정

**User Story:** 합의 당사자로서, 오래된 승인이 계속 유효하지 않기를 원한다. 그래야 몇 주 전의 승인으로 지금 실행되는 일이 없다.

#### Acceptance Criteria

1. THE Derive SHALL 승인을 평가 시각이 (승인 시각 + 그 판본의 승인 유효기간)보다 이른 동안만 유효 승인으로 세고, 평가 시각이 그 시각과 같거나 늦으면 만료된 승인으로 센다.
2. WHEN agreed 이던 협의에서 유효 실행 기록 전에 한쪽 승인이 만료되면, THE Derive SHALL 남은 유효 승인이 1건이면 half_approved 로, 0건이면 미해소 쟁점 유무에 따라 questioned 또는 pending_approval 로 상태를 계산한다.
3. THE Derive SHALL 평가 시각을 인자로 받고, 인자가 없으면 현재 UTC 시각을 쓴다.
4. THE Accord SHALL 현재 시각을 모듈 안의 시계 함수 하나로 얻어 시험이 그 함수를 바꿔 끼울 수 있게 하고, 명령행 인자나 환경 변수로 시각을 바꾸는 경로는 두지 않는다.
5. IF 받은 서명된 승인이 수신 평가 시각에 이미 만료돼 있으면, THEN THE Ingest SHALL 해당 입력을 거부하고 거부 사유에 만료 시각을 밝힌다.
6. THE Accord_CLI SHALL execute·relay 명령의 판정과 request-approval 의 상태 출력에 만료를 반영한 상태를 쓴다.

### 요구사항 19: 승인 시각 기준

**User Story:** 만료를 계산하는 사람으로서, 승인 시각이 상대가 마음대로 적은 값이 아니기를 원한다. 그래야 누구도 시각을 조작해 만료를 늦출 수 없다.

#### Acceptance Criteria

1. THE Derive SHALL 서명된 승인의 승인 시각을 정규 승인 문장의 서명 시각으로 한다.
2. THE Derive SHALL 서명 없는 승인의 승인 시각을 그 행의 기록 시각으로 하고, 기록 시각 필드가 있는 행에서는 봉투의 ts 를 쓰지 않는다.
3. IF 받은 정규 승인 문장의 서명 시각이 수신 평가 시각보다 허용 오차를 넘게 미래이면, THEN THE Ingest SHALL 해당 입력을 거부하고, 거부 사유에 서명 시각과 이 쪽 시각을 밝힌다.
4. IF 원장 행의 서명 시각이 그 행의 기록 시각보다 허용 오차를 넘게 미래이면, THEN THE Derive SHALL 해당 행을 세지 않고 경고를 붙인다.

### 요구사항 20: 만료 표시

**User Story:** 합의를 지켜보는 사람으로서, 승인이 언제 만료되는지 상태 출력에서 바로 보고 싶다. 그래야 합의가 풀리기 전에 실행하거나 다시 승인할 수 있다.

#### Acceptance Criteria

1. WHEN status 가 협의 하나를 출력하면, THE Accord_CLI SHALL 유효 승인마다 party, 승인 시각의 출처(서명 시각 또는 기록 시각), 만료까지 남은 시간(시간·분 단위)을 출력한다.
2. WHEN status 가 협의 하나를 출력하고 만료된 승인이 있으면, THE Accord_CLI SHALL 만료된 승인마다 party 와 만료 시각을 출력한다.
3. WHILE 협의가 agreed 이면, THE Accord_CLI SHALL 가장 먼저 만료되는 유효 승인의 만료 시각을 "합의가 풀리는 시각" 으로 출력한다.
4. THE Derive SHALL 결과에 유효 승인마다의 만료 시각과 만료된 승인 목록을 담는다.

### 요구사항 21: 시험

**User Story:** 유지보수하는 사람으로서, 모든 경계가 시험으로 고정되기를 원한다. 그래야 다음 변경이 구멍을 다시 열면 바로 드러난다.

#### Acceptance Criteria

1. THE Test_Suite SHALL P1–P10 마다 v1 의 구멍 시나리오를 재현해 v2 가 막는지(거부와 원장 불변, 또는 경고) 확인하는 경계 시험을 1개 이상 둔다.
2. THE Test_Suite SHALL 수신 규칙을 어긴 행을 원장에 직접 심었을 때 Derive 가 해당 행을 세지 않고 경고를 붙이는지 확인한다.
3. THE Test_Suite SHALL Ed25519_Verifier 를 RFC 8032 7.1 절 시험 벡터(최소 TEST 1, 2, 3)로 검증하고, 메시지·서명·공개키 중 1비트를 바꾼 입력과 S ≥ L 인 서명이 무효로 판정되는지 확인한다.
4. THE Test_Suite SHALL 서명 경계를 시험한다: 유효한 서명 승인, 키 등록 뒤 서명 없는 승인 거부, 다른 키의 서명 거부, 이름공간 불일치 거부, 문장 변조 거부, 미지원 키 종류 거부(init 과 검증 각각), 보안키 서명의 사용자 존재 플래그 켜짐·꺼짐, 검증된 전언의 경고 없음, 키 등록 상대의 서명 없는 전언 거부, 키 미등록 상대의 검증 불가 전언, 줄 끝·마지막 줄바꿈 차이 허용, 원장 행 서명 변조의 재생 무효.
5. THE Test_Suite SHALL 만료 경계를 시험한다: 기본 72시간, 지정 유효기간, 유효기간의 지문 포함, 유효기간 필드 없는 봉투의 v1 지문, 만료 직전 유효와 만료 시각 정각 만료, 만료로 인한 agreed → half_approved, 만료 뒤 execute 거부, 허용 오차 안·밖의 미래 서명 시각, 이미 만료된 서명 승인 거부, 상태 출력의 남은 시간 표시.
6. THE Test_Suite SHALL 시험용 서명을 시험 코드 안의 순수 파이썬 Ed25519 서명기와 SSHSIG 조립기, 또는 고정 벡터로 만들고, 시험 서명기가 RFC 8032 벡터와 같은 서명을 내는지 확인한다.
7. THE Test_Suite SHALL subprocess, ssh-keygen, 네트워크를 쓰지 않는다.
8. THE Test_Suite SHALL accord.py 의 구문 트리를 검사해 요구사항 1.1 과 1.2 의 조건을 확인한다.
9. THE Test_Suite SHALL 시계 함수를 바꿔 끼워 만료를 시험하고, 실제 시간이 흐르기를 기다리지 않는다.
10. WHEN `python3 test_accord.py` 와 `python3 -W error::ResourceWarning test_accord.py` 가 실행되면, THE Test_Suite SHALL 모든 시험을 통과한다.

### 요구사항 22: 문서

**User Story:** ACCORD 를 처음 쓰는 사람으로서, 규격과 사용법이 v2 의 실제 동작과 한계를 그대로 적고 있기를 원한다. 그래야 서명이 무엇을 막고 무엇을 못 막는지 오해하지 않는다.

#### Acceptance Criteria

1. THE Spec_Doc SHALL v2 규격을 서술한다: 봉투의 v2 필드, 두 지문 공식, 서명 요구를 포함한 승인 경계, 정규 승인 문장 형식, 전언 검증 정책, 만료 규칙·승인 시각 기준·허용 오차, 갱신된 상태 기계(반려 유지, 실행 조건, 종결 상태), 재생 일관성, P1–P10 봉합 규칙.
2. THE Spec_Doc SHALL 원장 행 형식을 구현과 같게 서술한다 (v1 §7 은 `{at, dir, kind, body}` 감싸기를 적었지만 구현은 봉투를 그대로 한 줄로 쓴다).
3. THE Spec_Doc SHALL §9 에서 서명·만료를 구현된 항목으로 옮기고, 3인 이상 정족수와 키 등록 이상의 신원 보증을 남은 항목으로 적는다.
4. THE Readme SHALL v2 사용법(양쪽이 자기 키와 상대 키를 모두 등록하는 init, request-approval → 사람이 직접 ssh-keygen 서명 → 문장과 서명 블록 붙이기, relay 와 검증된 전언, 승인 유효기간 지정, revise 해소 목록, withdraw)을 실행 가능한 명령 예시로 담는다.
5. THE Readme SHALL "코드가 실제로 거부하는 것들" 표에 P1–P10 과 서명·만료 경계를 더한다.
6. THE Readme SHALL 정직한 한계에 다음을 적는다: 서명 키는 에이전트가 읽을 수 없는 곳(암호문 보호 키, ssh-agent, 하드웨어 보안키)에 있어야 효과가 있고 프로토콜은 이를 강제할 수 없다는 것, 키 미등록 상대의 전언은 여전히 검증 불가라는 것, 등록 공개키와 원장은 이 쪽 에이전트가 고칠 수 있는 파일이라 사람이 init 이 출력한 키 지문을 상대와 따로 대조해야 한다는 것, 서명 없는 승인의 만료 기준은 각 쪽 기록 시각이라 양쪽 만료 시점이 전달 지연만큼 다를 수 있다는 것, 내용 없는 revise 검출은 공백 정규화까지만 한다는 것, 만료 판정이 각 쪽 시계에 의존한다는 것, v1 구현은 v2 봉투를 받지 못하므로 양쪽이 모두 v2 로 올려야 한다는 것.
7. THE Readme SHALL 시험 개수를 실제 시험 수와 같게 적는다.
