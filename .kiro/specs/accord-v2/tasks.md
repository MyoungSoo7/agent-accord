# 구현 계획: ACCORD v2

## 개요

설계 문서의 절 순서대로 `accord.py` 한 파일을 아래에서 위로 쌓는다. 시계·스키마 → 지문·저장소 → SSH 바이트 형식·공개키 → Ed25519 → SSHSIG → 정규 승인 문장까지는 새 함수를 더하기만 하므로 v1 경로가 그대로 돈다. 9 에서 `derive` 를, 10 에서 기록 경로(`record`·ingest)를 새 판정으로 바꿔 끼워 P1–P10 을 닫는다. 12 에서 만료를, 14–15 에서 CLI 를, 17 에서 문서를 올린다.

- 손대는 파일은 다섯이다: `accord.py`, `test_accord.py`, `contracts/envelope.schema.json`, `SPEC.md`, `README.md`. 합의 대상 계약 3종과 `examples/` 는 바꾸지 않는다.
- `accord.py` 는 표준 라이브러리만 쓴다. subprocess·eval·exec·네트워크가 없고, 서명을 만드는 함수와 개인키를 읽는 경로가 없다. 파일은 모두 `with open(...)` 으로 연다.
- 시험도 subprocess·ssh-keygen·네트워크를 쓰지 않는다. 속성 시험은 `unittest` 안의 시드 고정 무작위 반복이다 (`random.Random(시드)`, 속성마다 100회 이상, `self.subTest(seed=…)`, 메서드 위에 `# Feature: accord-v2, Property N: …` 태그). hypothesis 는 쓰지 않는다.
- 기존 18개 시험은 모든 체크포인트에서 통과한다. 기존 시험에 대한 수정은 1.4 의 `peer_human_relay` 한 곳뿐이다. 기존 `Base` 와 시험 본문은 그대로 두고, v2 시험은 시계를 고정하는 새 기반 클래스를 쓴다.
- 체크포인트 명령 (Windows): `python test_accord.py`, `python -W error::ResourceWarning test_accord.py`.

## 작업

- [ ] 1. 기반 — 시계·시각 형식, 봉투 스키마, 미니 검증기
  - [ ] 1.1 `accord.py` §1 상수·시계·시각 형식을 둔다
    - `SKEW = timedelta(minutes=5)`, `DEFAULT_TTL_H, TTL_MIN_H, TTL_MAX_H = 72, 1, 720`
    - `clock()` (이 모듈이 시각을 얻는 유일한 곳), `eval_time()` (UTC, 초 단위로 자름), `now()` = `fmt_utc(eval_time())` (v1 이름 유지)
    - `_UTC_RE` (`\d` 대신 `[0-9]`), `fmt_utc`, `parse_utc` (형식이 맞고 실재하는 시각이면 tz-aware datetime, 아니면 None)
    - `datetime.now` 를 직접 부르던 곳을 모두 `clock()` 경유로 바꾼다. 호출 시점에 모듈 전역 이름으로 찾고, 기본 인자(`at=clock()`)·import 시점 바인딩을 두지 않는다. 명령행 인자·환경 변수로 시각을 바꾸는 길은 만들지 않는다
    - _Requirements: 18.4, 11.1, 11.4_

  - [ ] 1.2 `accord.py` §2 미니 검증기를 두 곳만 넓힌다
    - int·float(bool 제외)에 `minimum`·`maximum` 검사를 더한다. `type: integer` 는 v1 그대로 bool 과 실수(`72.0`, `1e2`)를 거부한다
    - `_pattern_ok(pat, s)`: 패턴 끝의 `$`(`\$` 제외)를 `\Z` 로 바꿔 검사한다. 합의 대상 계약의 패턴에도 똑같이 적용된다
    - 지원 키워드 주석을 고친다
    - _Requirements: 2.3, 17.7_

  - [ ] 1.3 `contracts/envelope.schema.json` 을 v2 로 올린다
    - `accord` `{"enum": ["1", "2"]}`, `rev` `minimum: 1`, `ts`·`recorded_at` 은 `^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$`, `issue_id` 는 `^Q-[0-9a-f]{6}$`
    - `approval_ttl_h` (integer, 1–720), `resolves` (`issue_id` 형식 문자열의 배열), `signed` (`statement`·`signature` 둘 다 필수, 각 `minLength: 1`, `additionalProperties: false`)
    - 최상위·`sender`·`signed` 모두 `additionalProperties: false` 를 유지한다
    - _Requirements: 2.2, 2.3, 17.7_

  - [ ] 1.4 `test_accord.py` 시험 기반을 준비한다
    - `peer_human_relay` 가 `"rev": 1` 대신 `accord.derive(thread)["rev"]` 를 싣게 고친다. 기존 시험에 대한 유일한 수정이다 (v1 `derive` 로도 통과한다)
    - 기존 `Base` 는 건드리지 않고 v2 시험용 기반 클래스(예: `V2Base(Base)`)를 더한다: `self.t` (tz-aware UTC 고정 시각), setUp 에서 `accord.clock = lambda: self.t`, tearDown 에서 원래 함수로 되돌린다. 시간 경과는 `self.t += timedelta(…)` 로만 만든다
    - _Requirements: 2.7, 21.9_

  - [ ]* 1.5 시각 형식·검증기·봉투 스키마 단위 시험
    - `parse_utc`: 전각 숫자, `2026-02-30T00:00:00Z`, 끝 줄바꿈, `+00:00` 표기 → None
    - 검증기: `minimum`·`maximum` 경계, bool·`72.0` 거부, `"T-12345678\n"` 거부
    - 스키마: accord "1"·"2" 통과·"3" 거부, `rev` 0 거부, `approval_ttl_h` 0·721·72.0·true 거부, `signed` 한쪽만 있거나 모르는 키가 있으면 거부
    - _Requirements: 2.3, 17.7_

  - [ ] 1.6 구문 트리 불변식 시험
    - `ast.parse` 로 `accord.py` 를 읽어, 가져온 최상위 모듈 ⊆ {`__future__`, `argparse`, `base64`, `binascii`, `datetime`, `functools`, `hashlib`, `json`, `os`, `re`, `struct`, `sys`, `typing`} 이고, `sys.stdlib_module_names` 가 있으면(3.10+) 그 안에도 있는지 본다
    - 금지: `subprocess`·`socket`·`ssl`·`urllib`·`http`·`ftplib`·`smtplib`·`asyncio`·`ctypes`·`multiprocessing` 가져오기, `eval`·`exec`·`compile`·`__import__` 이름 호출, `os.system`·`os.popen`·`os.exec*`·`os.spawn*`·`os.fork` 호출
    - 서명 생성 흔적: 함수 이름을 `_` 로 나눈 토막에 `sign` 이 없다 (`signed`·`signature` 는 허용)
    - `test_accord.py` 도 subprocess·socket·ssl·urllib·http 를 가져오지 않는지 같은 방법으로 본다
    - 허용 목록에 뒤 단계가 가져올 모듈까지 들어 있으므로 모든 체크포인트에서 통과한다
    - _Requirements: 21.8, 21.7, 1.1, 1.2, 1.3_

- [ ] 2. 지문·판본 내용 정규화·저장소
  - [ ] 2.1 `accord.py` §4 지문과 판본 내용
    - `state_hash(thread, rev, subject, payload, ttl_h=None)`: None 이면 v1 공식, 아니면 `approval_ttl_h` 를 넣은 v2 공식. 기존 네 인자 호출은 v1 그대로다
    - `version_hash(row)` (행에 `approval_ttl_h` 가 있는지로 공식을 고름), `effective_ttl(row)` (없으면 72)
    - `normalize(v)` (중첩 안의 문자열 값까지 `" ".join(v.split())`, 키는 그대로), `content_key(subject, payload, ttl_h)` (정규화한 정규 JSON, 유효기간은 `effective_ttl` 값)
    - _Requirements: 17.5, 17.6, 17.8, 7.7_

  - [ ] 2.2 `accord.py` §3 저장소를 넓힌다
    - 설정: `me`·`peer` 의 `pubkey` 가 없거나 null 이면 키 미등록으로 읽는다. v1 설정은 그대로 "둘 다 미등록" 이다
    - 원장 읽기: JSON 으로 읽히지 않거나, 객체가 아니거나, `thread` 문자열이 없는 줄을 건너뛰고 그 수를 센다. 전체 status(15.3)가 그 수를 받을 수 있게 한다 (예: `(행, 건너뛴 수)` 를 돌려주는 함수를 따로 둔다)
    - `recorded_time(row)`: `recorded_at`, 없으면(v1 행) `ts` 를 `parse_utc` 로 읽는다. 둘 다 못 읽으면 None
    - `append()` 는 판정 없이 한 줄을 쓰는 저수준 함수로 남긴다 (시험이 행을 심는 데 쓴다)
    - _Requirements: 2.4, 2.6, 12.6, 3.5, 11.1, 19.2, 1.6_

  - [ ]* 2.3 지문·정규화·저장소 단위 시험
    - v1 공식이 기존 시험의 네 인자 호출과 같은 값을 내고, v2 정규 JSON 이 `{approval_ttl_h, payload, rev, subject, thread}` 이며, 유효기간만 바꿔도 지문이 바뀐다
    - `normalize` 멱등, 스페이스·탭·NBSP·U+3000·줄바꿈 정규화, U+200B 는 공백이 아니다, `content_key` 는 키 순서와 무관하다
    - 깨진 줄·배열 줄·`thread` 없는 줄을 건너뛰고 센다, `recorded_time` 은 `recorded_at` 을 `ts` 보다 먼저 쓴다
    - _Requirements: 17.5, 17.6, 7.7, 2.4, 19.2_

- [ ] 3. 체크포인트 — 기반
  - `python test_accord.py` 와 `python -W error::ResourceWarning test_accord.py` 를 돌려 기존 18개를 포함한 모든 시험이 통과하는지 확인한다. 질문이 생기면 사용자에게 묻는다.

- [ ] 4. SSH 바이트 형식·공개키 해석·키 지문
  - [ ] 4.1 `accord.py` §5 를 만든다
    - `SigError` 와 읽기 도구 `_Wire` (`u8()`, `u32()`, `string()` — 길이가 남은 바이트보다 크면 `SigError`, `done()` — 남는 바이트가 있으면 `SigError`). 인덱스·구조체 예외가 새어 나갈 경로를 두지 않는다
    - `SUPPORTED_KEY_TYPES`, `PubKey(ktype, blob, pk, application, comment)`
    - `parse_pubkey_line(line)`: 설계 5절 1–5단계 (최대 3토막, `b64decode(validate=True)`, blob 속 종류 = 앞 토막, 지원 종류, 종류별 필드·32바이트·남는 바이트). 6단계(곡선 위의 점)는 5.1 에서 잇는다
    - 거부 사유에 받은 값을 다시 찍지 않는다 (blob 이 확인해 준 종류 이름만 예외). 미지원 종류는 이름과 지원 목록을, `PRIVATE KEY` 가 보이면 개인키 안내를 담는다
    - `key_fingerprint(blob)` = `"SHA256:"` + 패딩 없는 base64(sha256(blob)). sk 키는 application 까지 포함한 blob 전체다
    - 키 파일을 여는 코드는 두지 않는다
    - _Requirements: 12.3, 12.4, 12.5, 1.1, 1.3_

  - [ ] 4.2 `load_config_and_keys()` 를 만들고 모든 명령이 설정을 이 함수로 읽게 한다
    - 등록된 키를 해석해 `keys = {"me": PubKey | None, "peer": PubKey | None}` 를 만든다
    - 설정 속 키가 해석되지 않으면 모든 명령을 "설정의 공개키를 읽을 수 없다 — init 을 다시 한다" 로 거부한다
    - 키는 9.3 에서 판정에 쓴다
    - _Requirements: 2.6, 12.6, 11.1_

  - [ ] 4.3 SSH 시험 도우미와 공개키 해석 시험
    - 도우미 `ssh_string(b)`, `pubkey_blob(pk, sk=False, app=b"ssh:")`, `fake_rsa_line()` (`string "ssh-rsa" | string e | string n` 모양). 뒤의 필수 시험이 쓴다
    - RFC 8032 TEST 1 공개키로 만든 `ssh-ed25519`·`sk-ssh-ed25519@openssh.com` 줄이 해석되고 (곡선 위의 점이라 5.1 뒤에도 통과한다), 키 지문이 시험 쪽에서 따로 계산한 값과 같다
    - 거부: 토막 부족, 깨진 base64, 내부 종류 불일치, `ssh-rsa` (사유에 `ssh-rsa` 와 지원 목록), pk 31·33바이트, 남는 바이트, sk 의 application 누락. `PRIVATE KEY` 가 든 입력은 안내가 붙고 받은 값이 사유에 없다
    - `_Wire` 의 잘린 string·남는 바이트는 `SigError` 다
    - 설정의 `pubkey` 를 깨뜨리면 status 등 모든 명령이 거부된다
    - _Requirements: 12.3, 12.4, 12.5, 2.6, 21.4_

- [ ] 5. Ed25519_Verifier (RFC 8032, 순수 파이썬)
  - [ ] 5.1 `accord.py` §6 검증기
    - `_P`, `_L`, `_D`, `_SQRT_M1`, 기준점 `_B` (y = 4/5, x 짝수), 확장 좌표 (X, Y, Z, T)
    - `_decode` (§5.1.3 엄격 디코딩: y ≥ p 거부, 제곱근이 없으면 거부, x = 0 인데 부호 비트가 1 이면 거부), `_encode`, `_add` (§5.1.4 통합 덧셈식, 배가도 같은 식), `_mul` (왼쪽부터 double-and-add), `_neg`
    - `ed25519_verify(pk, msg, sig) -> bool`: 길이, A·R 디코딩, S ≥ L 거부, `k = SHA512(R ‖ A ‖ M) mod L`, `[S]B − [k]A` 를 인코딩해 R 바이트와 비교 (공인자 없는 검사). 예외를 내지 않는다
    - `parse_pubkey_line` 6단계에 `_decode` 를 잇는다 ("곡선 위의 점이 아니다")
    - 상수 시간이 아니고 공개 값만 다룬다는 주석을 단다
    - _Requirements: 14.10, 12.3, 1.1_

  - [ ] 5.2 시험 전용 서명기와 RFC 8032 벡터 시험
    - `test_accord.py` 머리에 "시험 전용 서명기 — 상수 시간이 아니다. 실제 키에 쓰지 말 것" 주석을 단다
    - `TEd25519`: RFC 8032 §6 참조 코드 모양의 독립 구현(키 생성, 서명). `accord.py` 의 점 연산을 빌리지 않는다. 고정 시드 키 2–3개는 모듈 로드 때 한 번만 만든다
    - RFC 8032 §7.1 TEST 1·2·3 을 원문에서 그대로 옮기고, 시험 서명기가 비밀키에서 같은 공개키·같은 서명을 내는지 먼저 확인한다 (옮겨 적기 실수도 여기서 잡힌다)
    - `accord.ed25519_verify` 가 세 벡터를 받고, 메시지·서명·공개키의 1비트 변조와 S + L 서명을 거부한다
    - y ≥ p 인 비정규 인코딩과 곡선 위에 없는 점은 거부되고, 그런 공개키 줄은 `parse_pubkey_line` 이 거부한다
    - _Requirements: 21.3, 21.6, 14.10, 12.3_

  - [ ]* 5.3 Ed25519 변조 거부 속성 시험
    - **Property 21: Ed25519 변조 거부**
    - 고정 키와 무작위 메시지로 서명한 뒤 메시지·서명·공개키 가운데 무작위 1비트를 바꾸거나 S 를 S + L 로 바꾸면 검증이 실패한다
    - **Validates: Requirements 14.10**

- [ ] 6. Sig_Verifier (SSHSIG)
  - [ ] 6.1 `accord.py` §7 `verify_sshsig(armored, candidates, key) -> (bool, 사유)`
    - 설계 7절 검사 순서 1–10: armor (줄마다 strip 해 잇고 `b64decode(validate=True)`), blob 구조 (`SSHSIG`, 버전 1, 필드 여섯, 남는 바이트 없음), 이름공간 = `accord`, reserved = 빈 값, 해시 ∈ {sha512, sha256}, 키 종류 (5절 해석 재사용, 미지원이면 이름과 지원 목록), 등록 키 blob 과 바이트 대조 (다르면 두 키 지문), 서명 구조 (sk 는 flags·counter 까지), sk 사용자 존재 비트, 후보마다 서명 대상 데이터 검증 (ssh-ed25519 형식 / sk 형식)
    - 내부 형식 오류는 `SigError` 하나로 올리고 `verify_sshsig` 만 잡는다. `binascii.Error` 도 감싼다. 어떤 입력에도 예외를 밖으로 내지 않는다
    - 모든 후보가 실패하면 "서명이 문장과 맞지 않는다" 에 문장 인코딩 안내(`request-approval --out`, PowerShell 5.1 의 `>`·`Set-Content`)를 붙인다
    - `functools.lru_cache` 로 `(등록 키 blob, 후보 튜플, armored)` 의 결과를 프로세스 안에서만 기억한다
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.9, 14.13, 1.7_

  - [ ] 6.2 SSHSIG 시험 도우미와 검증기 경계 시험
    - 도우미 `pubkey_line(seed, sk=False, comment="t@test")`, `sshsig(seed, message, *, namespace=b"accord", hash_alg=b"sha512", sk=False, flags=0x01, counter=1, reserved=b"", key_blob=None, sig_type=None)` → 70자 줄바꿈 armored 문자열. 뒤의 필수 시험이 쓴다
    - 유효: ssh-ed25519 × sha512·sha256, sk × flags 0x01
    - 무효 (사유 확인 포함): 다른 키의 서명, 이름공간 `git`, reserved 가 빈 값이 아님, 해시 `sha1`, 서명 속 키 종류 `ssh-rsa` (이름과 지원 목록), sk flags 0x00, 버전 2, 표지 불일치, 잘린 blob, 깨진 base64, 남는 바이트
    - _Requirements: 21.4, 21.6, 14.2, 14.3, 14.4, 14.5, 14.6, 14.9_

  - [ ]* 6.3 서명 블록 감싸기 속성 시험
    - **Property 18: 서명 블록 감싸기는 내용을 바꾸지 않는다**
    - 유효한 blob 을 줄 폭 1–76, 줄마다 무작위 앞뒤 공백으로 다시 감싸도 armor 해석이 같은 blob 을 돌려준다
    - **Validates: Requirements 14.1**

  - [ ]* 6.4 서명·검증 왕복 속성 시험
    - **Property 20: 서명·검증 왕복**
    - 무작위 메시지 × 키 종류 × 해시로 만든 이름공간 `accord` 서명이 등록 키로 검증되고, sk 서명은 flags 의 0x01 비트가 켜졌을 때만 유효하다
    - **Validates: Requirements 14.7, 14.8, 14.9**

- [ ] 7. 정규 승인 문장
  - [ ] 7.1 `accord.py` §8 문장 생성·해석·사람 입력 분리
    - `STMT_MARK = "accord/2"`, `make_statement(verb, thread, fp, rev, human_id, at, reason=None)`: LF 로 잇고 마지막 LF 하나. 반려 틀은 `사유 ` 로 끝난다
    - `StatementError`, `Statement(verb, thread, fp, rev, human_id, at, reason, lines)`, `parse_statement(text)`: `re.split(r"\r?\n", text)` 뒤 마지막 빈 원소 하나만 떼고, 줄마다 설계 6절 문법으로 `fullmatch`. 승인 정확히 5줄·반려 정확히 6줄, 구분자는 공백 한 칸, 동사는 `승인`·`반려` 만, 시각은 `parse_utc` 로 읽혀야 한다
    - `statement_candidates(lines)`: LF·CRLF × 마지막 줄바꿈 유무의 네 후보 바이트
    - `split_human_input(text)`: BEGIN 표지 줄 → END 표지 줄 (없으면 "서명 블록의 끝 표지가 없다") → END 뒤 다른 내용 거부. `statement`·`signature` 는 앞뒤 공백만 뗀 원문 문자열이다
    - _Requirements: 13.1, 14.11, 14.12, 15.5_

  - [ ] 7.2 사람 입력 도우미와 문장 경계 시험
    - 도우미 `signed_human_input(seed, statement, eol="\n", final_newline=True)` (문장 + 빈 줄 + 서명 블록). 뒤의 필수 시험이 쓴다
    - `make_statement` 바이트가 설계 6절 예시와 같다 (승인 5줄·반려 6줄·마지막 LF)
    - 해석 실패: 빈 줄 추가, 줄 순서 바뀜, 두 칸 공백, 탭, 줄 끝 공백, `approve` 동사, `accord/1` 표지, `rev 0`, 빈 사유
    - 분리: END 표지 없음·END 뒤 내용은 거부, CRLF 입력의 줄 끝은 그대로 남는다
    - LF 로 서명한 문장을 CRLF 로, 마지막 줄바꿈 없이 보내도 `verify_sshsig` 가 받는다
    - _Requirements: 13.1, 14.11, 14.12, 21.4_

  - [ ]* 7.3 정규 승인 문장 왕복 속성 시험
    - **Property 17: 정규 승인 문장 왕복**
    - 무작위 (동사, 협의 번호, 지문, rev, 사람 id, 시각, 반려면 사유)가 `parse_statement(make_statement(…))` 로 그대로 돌아오고, 무작위 구조 변형(줄 삭제·추가·교환, 값 형식, 구분 공백)은 해석에 실패한다
    - **Validates: Requirements 13.1, 14.12**

  - [ ]* 7.4 줄 끝 허용 범위 속성 시험
    - **Property 22: 줄 끝 허용 범위는 정확히 넷이다**
    - 서명된 문장의 네 줄 끝 변형은 모두 유효하고, 글자 하나·공백 하나·BOM·LF/CRLF 섞기는 모두 무효다
    - **Validates: Requirements 14.11**

- [ ] 8. 체크포인트 — 서명 검증 부품
  - `python test_accord.py` 와 `python -W error::ResourceWarning test_accord.py` 를 돌려 기존 18개를 포함한 모든 시험이 통과하는지 확인한다. 전체 실행이 60초 안인지도 본다. 질문이 생기면 사용자에게 묻는다.

- [ ] 9. 판정 함수와 재생 — `judge`·`apply`·`replay`·`state_at`·`derive`
  - [ ] 9.1 `accord.py` §9 자료형과 `judge` 공통 규칙 J1–J8
    - `Verdict` (ok, code, reason, 서명 판정 결과), `Ctx` (설계 9절 표의 필드), `Approval` (party, source, row — 시각 필드는 12.1 에서 더한다). 1.6 의 가져오기 허용 목록 안에서 만든다 (예: 일반 클래스, `typing.NamedTuple`)
    - `judge(cfg, keys, ctx, row, at)`: 시계와 원장·설정 파일을 읽지 않고 뒤의 행을 보지 않는 순수 함수
    - J1 봉투 스키마, J2 기록 시각, J3 필드-타입 정합 (`approval_ttl_h` 는 propose·revise, `resolves` 는 revise, `signed` 는 approve·reject·전언에만), J4a–J4d (v1 거부 순서와 문구, approve 의 권한 위반은 `misrecorded`), J5 협의 번호 ("이미 있는 협의 번호다" / "원장에 없는 협의다"), J6 종결 상태, J7 rev·subject (revise 는 기대 rev 와 받은 rev 를 사유에), J8 지문 (propose·revise 는 `version_hash` 재계산, 전언의 낡은 지문은 "낡은 판본에 대한 전언이다 (현재 …)", 사람 줄은 v1 "지문 불일치")
    - 이 단계에서는 아직 아무 경로도 `judge` 를 부르지 않는다 (9.5·10 에서 잇는다)
    - _Requirements: 2.3, 11.1, 1.4, 11.5, 15.4, 3.3, 4.1, 4.2, 4.3, 4.4, 4.5, 5.2, 5.7, 6.2, 9.5, 17.5, 17.8_

  - [ ] 9.2 `judge` 타입별 규칙 J9–J17
    - J9 propose payload 계약, J10 question 쟁점 번호 필수·협의 안에서 처음, J11 revise payload 계약·해소 목록의 번호가 모두 앞서 제기된 쟁점, J12 execute 는 `state_at(ctx, at)` ∈ {agreed, executed} ("합의 전 실행 기록이다 (상태 …)"), J13 withdraw 는 협의를 연 party 만
    - J14 내 사람 승인 행: rejected 면 "반려된 판본이다 — revise 로 새 판본을 올린 뒤 승인한다". J15 내 사람 반려 행: 사유 = 서명 문장의 사유 줄 또는 v1 줄의 나머지 (없으면 "(사유 없음)")
    - J16 상대 전언: human_id = 상대 사람 id (`relay_human`), approve_relay 는 rejected 가 아니다 (`after_reject`)
    - J17 내 전언: human_id = 내 사람 id. 승인은 `at` 에 유효한 내 승인이 있고 `signed` 가 그 승인 행의 것과 같다. 반려는 `my_reject` 가 있고 text·`signed` 가 그 행의 것과 같다
    - rejected 에서도 question·revise·연 쪽 withdraw·reject_relay·내 사람 반려 줄은 받는다
    - _Requirements: 7.3, 7.5, 5.1, 5.4, 9.3, 10.2, 10.5, 6.1, 6.3, 6.4, 16.1_

  - [ ] 9.3 `judge` 서명 정책 S1–S5
    - K = 사람 행이면 `keys["me"]`, 상대 전언이면 `keys["peer"]`
    - S1 (K 없음·사람 행: `signed` 가 있으면 init --pubkey 안내와 함께 거부, 없으면 서명 없는 승인·반려), S1' (K 없음·상대 전언: `signed` 를 판정에 쓰지 않고 검증 불가 전언), S2 (K 있음·`signed` 없음: 사람 행은 서명 필요 + request-approval 안내, 전언은 "상대 키가 등록돼 있어 서명 없는 전언은 세지 않는다"), S3 `parse_statement` 실패 사유, S4 결속 (동사·thread·rev·지문·사람 id, 반려 계열은 text = 문장의 사유), S5 `verify_sshsig(signature, statement_candidates(lines), K)`
    - 실패 코드는 사람 행 `sig_mine`, 상대 전언 `sig_peer`. 통과하면 `source` 가 attested·signed·relayed·verified 가운데 하나로 정해진다
    - 서명 오류는 모두 사유 문자열로만 나온다 (예외 추적 없음)
    - _Requirements: 15.1, 15.2, 15.3, 15.6, 16.2, 16.3, 16.4, 16.6, 2.6, 14.12, 14.13_

  - [ ] 9.4 `apply(ctx, row, verdict, at)`
    - 설계 9절 반영 표 전체: propose (opened·opener·subject·현재 판본), question (`issues`), revise (내용 없는 revise 면 `contentless` 에 rev 를 더하고 해소 목록 무시, 아니면 해소 목록만 `resolved` 에 더함; `stale` 기록; 승인·`rejected`·`my_reject` 비움; 현재 판본 교체), approve (`approvals[me]`), 상대 approve_relay (`approvals[peer]`), reject·상대 reject_relay (`rejected`, 사람 행이면 `my_reject`, `rejects`, 승인 비움), 내 발신 전언 (`sent_relays` 만 — 승인·반려로 세지 않는다), execute (`executed`·`exec_hash`), withdraw (`withdrawn`)
    - _Requirements: 7.2, 7.6, 7.8, 10.1, 6.6, 1.5, 5.4_

  - [ ] 9.5 `replay`·`state_at`·`derive` 로 v1 `derive` 를 바꿔 끼운다
    - `replay(cfg, keys, rows)`: 행마다 `judge(…, recorded_time(r))` → 통과면 `apply`, `misrecorded` 면 그 목록, 그 밖은 `excluded` 에 `{row, code, reason}`. `rows_seen` 은 유효·무효 모두 센다
    - `state_at(ctx, t)`: withdrawn > executed > rejected > 승인 수 (둘 → agreed, 하나 → half_approved) > questioned / pending_approval. 만료 필터는 12.1 에서 더한다
    - `derive(thread, at=None)`: `at` 이 없으면 `eval_time()`, 설정·키는 `load_config_and_keys()`. 행이 없으면 "원장에 없는 협의다", 유효한 propose 가 없으면 "손상된 협의 — 유효한 propose 가 없다 (손상 사유)" 로 `AccordError`
    - 결과: v1 키 전부를 v1 뜻으로 (`relayed_approvals` 는 검증 불가 전언만, `rows` 는 뺀 행 포함) + `opener`, `approval_ttl_h`, `verified_relays`, `excluded` (`misrecorded` 는 넣지 않음), `contentless_revs`
    - 경고: `misrecorded` v1 문장, 뺀 행의 코드별 건수·사유 (설계 9절 재생 경고 표), 합의 착시 (v1 문장), 미해소 쟁점 합의 (건수와 번호, "합의 착시" 문구 없음), 내용 없는 revise (rev 마다), 검증 불가 전언 (v1 문장 + "init --peer-pubkey" 안내), code_change 롤백·검증
    - 파생 상태를 파일에 쓰지 않는다. v1 `derive` 본문은 지운다. 이 시점의 기록 경로는 아직 v1 이며 기존 18개 시험이 그대로 통과해야 한다
    - _Requirements: 1.7, 2.4, 2.5, 3.6, 5.4, 5.5, 5.6, 6.7, 7.6, 8.1, 8.2, 8.3, 8.4, 9.4, 10.1, 10.3, 11.1, 11.2, 11.5, 15.7, 16.5, 16.6, 16.7, 18.3_

  - [ ] 9.6 원장 심기 시험
    - v2 기반 클래스에 설정의 `pubkey` 를 직접 쓰는 도우미를 더한다 (init 의 키 인자는 14.1 에서 생긴다)
    - `accord.append` 로 판정을 건너뛰고 심은 행을 `derive` 가 빼고 알맞은 경고를 붙인다: 합의 전 execute (무효 실행 기록, 상태는 executed 가 아님), 비개설자 withdraw, 반려 뒤 승인 전언, 상대 사람 id 가 아닌 전언, 서명 바이트 하나를 바꾼 서명 승인 (`sig_mine`), rev 를 건너뛴 revise, 없는 협의의 question (그 협의의 `derive` 는 손상 사유를 밝혀 거부), JSON 이 아닌 줄 (다른 협의의 `derive` 는 정상)
    - 키를 나중에 등록하면 그 전의 서명 없는 내 승인은 소급해 세지 않는다 (재생 시점의 설정 기준)
    - _Requirements: 21.2, 11.1, 11.2, 5.5, 9.4, 10.3, 6.7, 15.7, 3.6_

- [ ] 10. 기록 경로 전환과 P1–P10 봉합 — `record`·`emit`·ingest
  - [ ] 10.1 `accord.py` §10 `record`·`emit` 과 기존 기록 명령
    - `record(cfg, keys, row, at)`: 그 협의를 `replay` 한 문맥으로 `judge` → 통과해야만 `recorded_at = fmt_utc(at)` 를 붙여 `append`. 실패하면 `AccordError(사유)` 로 원장·설정을 그대로 둔다. CLI 와 ingest 가 원장에 쓰는 유일한 길이다
    - `envelope()` 는 `accord: "2"`, `ts = fmt_utc(at)` 로 만들고, `emit()` 은 `record()` 를 거친 뒤 `recorded_at` 을 뺀 봉투를 출력한다
    - propose·question·revise·relay·execute 는 평가 시각을 한 번만 잡아 `ts`·`recorded_at`·명령 판정에 같은 값을 쓰고, `append` 를 직접 부르지 않는다. execute 의 합의 검사는 J12 가 맡는다
    - 인자 형식(relay 의 `--reject` 등)은 14 에서 바꾼다
    - _Requirements: 2.1, 3.1, 3.2, 3.4, 4.6, 5.3, 11.3, 11.4, 1.6_

  - [ ] 10.2 ingest 의 봉투 경로와 사람 줄 경로를 `record()` 로 바꾼다
    - 입력은 `utf-8-sig` 로 읽는다. `UnicodeDecodeError` 는 PowerShell 5.1 안내와 함께 거부한다
    - 봉투: v1 선검사 순서와 문구를 유지한다 (스키마 → 사람 봉투(kind=human) → 사람 전용 타입(approve·reject) → 알 수 없는 타입 → 메아리는 무시하고 rc 0 → 미등록 party). `recorded_at` 이 있으면 "기록 시각은 받는 쪽이 적는다" 로 거부한다. propose·revise 에 `state_hash` 가 없을 때만 계산해 채우고, 그 밖의 필드 값은 바꾸지 않은 채 `record()`
    - 사람 줄 (설계 12절): v1 `HUMAN_RE` 분기 → `--from-human` 규칙 (v1 문구, 서명 여부와 무관) → `split_human_input` → 서명 없음은 v1 해석 (승인 지문 대조 "지문 불일치", 반려 행에도 현재 `state_hash`), 서명 있음은 `parse_statement` 로 행을 만들고 `signed` 에 분리한 문자열을 그대로 싣는다 → `accord: "2"` 행을 `record()`
    - 기록 뒤 v1 처럼 "기록: …" 과 상태를 출력한다
    - _Requirements: 1.4, 2.2, 3.1, 3.2, 3.3, 11.3, 14.13, 15.1, 15.4, 15.5, 15.6_

  - [ ] 10.3 P1–P10 경계 시험
    - 구멍 대응표의 시나리오를 그대로 재현해 거부 + 원장 바이트 불변, 또는 경고를 확인한다
    - P1 합의 전 상대 execute ("합의 전 실행 기록"), P2 없는 협의의 question ("원장에 없는 협의", 원장 불변, 이후 전체 status rc 0), P3 기존 번호 propose·rev 1 이 아닌 propose·rev 1→99 revise (기대 rev 2 와 받은 rev 99), P4 미등록·누락 human_id 전언, P5 공백만 바꾼 revise (쟁점이 열린 채 남고 내용 없는 revise 경고), P6 미해소 쟁점 합의 경고 (번호 포함, "합의 착시" 없음), P7 내가 연 협의에 대한 상대 withdraw 봉투 거부, P8 반려 뒤 같은 판본 승인 줄·승인 전언 거부 (revise 안내, 상태 rejected 유지), P9 내 사람 반려 없이 만든 반려 전언이 기록되지 않고 원장에 심은 내 발신 reject_relay 는 반려로 세지 않음, P10 내 relay 뒤 `relayed_approvals` 에 내가 없고 틀린 전언 경고가 없음
    - 이 시점의 relay 인자는 v1 형식이다. 14.5 뒤에도 통과하도록 P9 는 거부와 원장 불변만 단언하고, P7·P9 의 CLI 쪽(withdraw 명령, `relay --reject` 플래그)은 14.6 에서 시험한다
    - _Requirements: 21.1, 5.1, 3.3, 4.1, 4.2, 4.3, 6.1, 7.6, 8.1, 8.2, 9.3, 10.2, 6.6_

  - [ ] 10.4 수신 쪽 서명 경계 시험
    - 유효한 서명 승인 (sha512·sha256, sk flags 켜짐) 이 기록되고, 행의 `signed.statement`·`signed.signature` 가 입력에서 분리한 문자열과 같다
    - 거부: 키 등록 뒤 서명 없는 승인 (서명 필요 + request-approval 안내), 다른 키 서명, 이름공간 불일치, 문장 변조, 문장의 협의 번호·rev·지문·사람 id 가 현재와 다름, 서명 속 미지원 키 종류, sk flags 꺼짐, 키 미등록인데 서명 블록이 붙은 사람 줄 (init 안내), 상대 사람 id 로 보낸 서명 입력
    - 수락: CRLF·마지막 줄바꿈 없는 서명 입력, 앞에 BOM 이 붙은 입력 파일
    - 상대 키 등록: 검증된 전언은 전언 경고가 없고 `verified_relays` 에 들어간다, 서명 없는 전언은 16.3 사유로 거부. 상대 키 미등록: 서명이 있든 없든 검증 불가 전언 + v1 경고
    - 기록된 서명 행의 서명 바이트를 원장 파일에서 바꾸면 재생에서 빠지고 경고가 붙는다. 서명 경로의 어떤 거부도 표준 오류에 `Traceback` 이 없다
    - _Requirements: 21.4, 15.1, 15.2, 15.3, 15.5, 15.6, 16.2, 16.3, 16.5, 16.6, 14.13_

  - [ ]* 10.5 사람 전용 타입 차단 속성 시험
    - **Property 1: 사람 전용 타입 차단**
    - 나머지 필드가 무작위인 봉투에 type approve·reject 또는 sender.kind human 을 넣으면 ingest 가 rc 2 이고, 원장 바이트와 `approved_by` 가 그대로다
    - **Validates: Requirements 1.4**

  - [ ]* 10.6 받은 필드 불변 속성 시험
    - **Property 6: 받은 필드는 바뀌지 않는다**
    - 수락된 무작위 상대 봉투(v1·v2, 선택 필드 무작위)의 행은 봉투 + `recorded_at`(= 판정 시각)이고 (없던 `state_hash` 만 더해질 수 있다), 서명 사람 줄의 `signed` 는 분리한 문자열과 같다
    - **Validates: Requirements 11.3, 15.5**

  - [ ]* 10.7 판본 번호 규율 속성 시험
    - **Property 7: 판본 번호 규율**
    - 무작위 협의 상태에서 기존 번호 propose, rev ≠ 1 propose, rev ≠ 현재+1 revise, rev ≠ 현재 그 밖의 봉투, subject 변경, 없는 협의 번호가 모두 거부되고, revise 사유에 기대·받은 rev 가, 없는 협의 사유에 "원장에 없는 협의" 가 있다
    - **Validates: Requirements 3.3, 4.1, 4.2, 4.3, 4.4, 4.5**

  - [ ]* 10.8 전언 권한 속성 시험
    - **Property 10: 전언의 권한**
    - 무작위 human_id (없음·내 사람·낯선 값)와 무작위 지문의 상대 전언은 상대 사람 id 가 아니면 거부되고, 지문이 현재와 다르면 "낡은 판본에 대한 전언" 으로 거부된다
    - **Validates: Requirements 6.1, 6.2**

  - [ ]* 10.9 지목 해소 속성 시험
    - **Property 12: 쟁점은 지목해서만 풀린다**
    - 무작위 쟁점 집합과, 내용이 바뀐 revise 들의 무작위 부분집합 해소 목록에 대해 `open_issues` = 제기된 집합 − 해소 목록의 합집합이고, 제기되지 않은 번호가 섞인 revise 는 거부된다
    - **Validates: Requirements 7.2, 7.3**

  - [ ]* 10.10 내용 없는 revise 속성 시험
    - **Property 13: 공백만 바꾼 revise 는 새 판본일 뿐이다**
    - payload 문자열에 무작위 공백(스페이스·탭·NBSP·U+3000·줄바꿈)을 넣고 빼거나 키 순서를 섞어도 `normalize` 는 멱등이고 `content_key` 가 같다. 그런 revise 는 rev 를 올리고 승인을 모두 무효로 하지만 쟁점을 풀지 않고 경고를 붙인다. 유효기간만 바꾼 revise 는 내용 없는 revise 가 아니다
    - **Validates: Requirements 7.6, 7.7, 7.8**

  - [ ]* 10.11 미해소 쟁점 합의 속성 시험
    - **Property 14: 미해소 쟁점 합의는 드러난다**
    - 미해소 쟁점이 k ≥ 1 건인 채 두 party 의 유효 승인이 서면 상태는 agreed 이고, k 와 번호 전부를 담은 경고가 하나 있으며 그 경고에 "합의 착시" 가 없다
    - **Validates: Requirements 8.1, 8.2, 8.3**

  - [ ]* 10.12 반려 유지 속성 시험
    - **Property 15: 반려는 revise 까지 유지된다**
    - 유효 반려 뒤 revise 가 아닌 무작위 입력 시퀀스(question, 승인 줄, approve_relay, reject_relay, 내 반려 줄)에서 상태는 rejected 로 남고, 승인 줄·approve_relay 는 revise 안내를 담아 거부된다
    - **Validates: Requirements 10.1, 10.2**

  - [ ]* 10.13 검증기 무예외 속성 시험
    - **Property 19: 검증기는 어떤 입력에도 판정만 돌려준다**
    - 유효한 서명 블록을 무작위로 자르거나 바이트 하나를 바꾼 입력과 무작위 문자열에 대해 `verify_sshsig` 는 예외 없이 `(False, 사유)` 이고, 같은 입력의 ingest 는 표준 오류에 `Traceback` 없이 rc 2 다
    - **Validates: Requirements 14.2, 14.13**

  - [ ]* 10.14 서명 결속 속성 시험
    - **Property 23: 서명 문장은 그것을 실은 행에 묶인다**
    - 올바르게 서명된 문장이라도 동사·협의 번호·rev·지문·사람 id 가운데 하나가 현재 판본(전언이면 type·thread·rev·state_hash·human_id·text)과 무작위로 어긋나면 ingest 가 거부한다
    - **Validates: Requirements 15.3, 16.4**

  - [ ]* 10.15 두 지문 공식 속성 시험
    - **Property 24: 두 지문 공식**
    - 무작위 (thread, rev, subject, payload, ttl) 에 대해 `state_hash` 가 설계 4절의 두 공식을 시험 쪽에서 따로 계산한 값과 같고, ttl 이 다르면 v2 지문이 다르며, 유효기간만 바꾸고 state_hash 를 그대로 둔 봉투는 거부된다
    - **Validates: Requirements 17.5, 17.6, 17.8**

- [ ] 11. 체크포인트 — 판정 파이프라인
  - `python test_accord.py` 와 `python -W error::ResourceWarning test_accord.py` 를 돌려 기존 18개를 포함한 모든 시험이 통과하는지 확인한다. 질문이 생기면 사용자에게 묻는다.

- [ ] 12. 승인 만료
  - [ ] 12.1 승인 시각과 만료를 상태 계산에 넣는다
    - `Approval` 에 `approved_at` (서명된 승인은 서명 시각, 서명 없는 승인은 기록 시각 — 봉투의 `ts` 는 쓰지 않는다), `basis` (`signed_at`·`recorded_at`), `expires_at = approved_at + 현재 유효기간` 을 채운다
    - 같은 판본·같은 party 의 승인이 여러 번이면 승인 시각이 가장 늦은 것을 센다
    - `state_at` 의 유효 승인 = `t < expires_at`. executed 는 만료와 무관하다
    - 유효 승인 판정을 도우미 하나로 모아 `state_at`·J12 (execute)·J17 (내 전언)·revise 의 `stale` 이 같이 쓴다. 그래서 execute·relay 명령의 판정에도 만료가 그대로 반영된다
    - _Requirements: 18.1, 18.2, 19.1, 19.2, 5.1, 5.6, 6.3, 17.6, 18.6_

  - [ ] 12.2 `judge` 에 S6·S7 을 더한다
    - S6: 서명 시각 > `at` + 5분이면 `future` — "서명 시각 …Z 가 이 쪽 시각 …Z 보다 5분 넘게 미래다". 재생 경고는 "서명 시각이 기록 시각보다 5분 넘게 미래인 행 N건"
    - S7 (승인 계열만): `at` ≥ 서명 시각 + 현재 유효기간이면 `expired` — "이미 만료된 승인이다 (만료 …Z)". 반려에는 만료가 없다
    - _Requirements: 18.5, 19.3, 19.4_

  - [ ] 12.3 `derive` 결과에 만료 정보를 담는다
    - `evaluated_at`, `approvals` (유효 승인마다 party·source·basis·approved_at·expires_at), `expired_approvals` (party·source·expires_at), `agreed_until` (agreed 면 가장 이른 `expires_at`, 아니면 None)
    - `approved_by`·`relayed_approvals`·`verified_relays` 는 평가 시각에 유효한 승인에서만 뽑는다
    - _Requirements: 20.4, 18.3, 2.5_

  - [ ] 12.4 만료 경계 시험
    - v2 기반 클래스의 시계만 움직인다 (실제 시간을 기다리지 않는다)
    - 기본 72시간 (유효기간 필드 없는 v1 봉투), 지정 유효기간 (`approval_ttl_h` 를 실은 상대 v2 봉투), 유효기간이 지문에 들어감, 필드 없는 봉투는 v1 지문, 유효기간만 바꾸고 state_hash 를 둔 봉투는 거부
    - 만료 1초 전 유효·정각 만료, 만료로 agreed → half_approved → pending_approval (쟁점이 있으면 questioned), 만료 뒤 execute 명령·봉투 거부
    - 서명 승인의 기준은 서명 시각이라 늦게 넣어도 만료가 늘지 않고, 서명 없는 승인은 기록 시각 기준이다
    - 허용 오차 안 (정확히 5분) 은 수락, 밖 (5분 1초) 은 두 시각을 밝혀 거부, 이미 만료된 서명 승인은 만료 시각을 밝혀 거부, 기록 시각보다 10분 미래인 서명 시각의 심은 행은 재생에서 빠진다
    - _Requirements: 21.5, 21.2, 21.9, 17.5, 17.6, 17.8, 18.1, 18.2, 18.5, 19.1, 19.2, 19.3, 19.4_

  - [ ]* 12.5 만료 경계 속성 시험
    - **Property 26: 만료 경계**
    - 무작위 유효기간 h, 승인 시각 a, 오프셋 δ 에 대해 δ < h 시간일 때만 유효하고 δ = h 정각부터 만료다. 상태는 유효 승인 수와 미해소 쟁점으로 정해지고, `expires_at` = a + h 이며, a 는 봉투의 `ts` 를 무작위로 바꿔도 그대로다
    - **Validates: Requirements 18.1, 18.2, 19.1, 19.2, 20.4**

  - [ ]* 12.6 미래 서명 시각 속성 시험
    - **Property 27: 미래 서명 시각 허용 오차**
    - 받는 쪽 평가 시각 기준 무작위 오프셋 o (초 단위) 에 대해 o ≤ 5분이면 (다른 규칙을 지키는 한) 수락, o > 5분이면 서명 시각과 이 쪽 시각을 밝혀 거부된다
    - **Validates: Requirements 19.3**

  - [ ]* 12.7 실행 조건 속성 시험
    - **Property 8: 실행은 합의 뒤에만, 실행은 풀리지 않는다**
    - 무작위 상태의 비-agreed 협의에서 execute 봉투·명령은 "합의 전 실행 기록" 으로 거부되고, executed 협의는 유효기간을 한참 넘긴 무작위 시각 t 에서도 `derive(thread, t)` 가 executed 다
    - **Validates: Requirements 5.1, 5.3, 5.6, 18.6**

- [ ] 13. 체크포인트 — 만료
  - `python test_accord.py` 와 `python -W error::ResourceWarning test_accord.py` 를 돌려 기존 18개를 포함한 모든 시험이 통과하는지 확인한다. 질문이 생기면 사용자에게 묻는다.

- [ ] 14. CLI 기록 명령 — init 키 등록, propose·revise 유효기간과 해소 목록, question, withdraw, relay
  - [ ] 14.1 init `--pubkey "<한 줄>"`, `--peer-pubkey "<한 줄>"`
    - 문자열만 받는다 (파일 경로 없음). `parse_pubkey_line` 을 모두 통과한 뒤에만 설정을 쓴다 — 하나라도 거부되면 설정 파일은 바이트 단위로 그대로다
    - 저장값은 앞뒤 공백만 지운 한 줄, 주어지지 않으면 `null`. 키 지문은 저장하지 않는다
    - 두 사람의 등록 여부와 등록된 키의 `SHA256:` 지문을 출력하고, 지문을 상대와 채팅 밖에서 대조하라고 안내한다
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 1.3_

  - [ ] 14.2 propose `--ttl-hours`, 새 협의 번호, question 쟁점 번호, payload 읽기
    - `--ttl-hours` 는 문자열로 받아 `^[0-9]+$` 와 1–720 을 코드에서 검사한다 (argparse `type=int` 를 쓰지 않아 "거부:" 형식을 지킨다). 기본 72. `approval_ttl_h` 를 항상 싣고 v2 지문으로 계산한다
    - 새 협의 번호는 원장에 없는 번호가 나올 때까지 다시 뽑는다
    - 쟁점 번호는 `Q-` + `sha256(thread·text·시각·urandom)[:6]`, 협의 안에서 겹치면 다시 뽑는다
    - payload 파일은 `utf-8-sig` 로 읽고 `UnicodeDecodeError`·`json.JSONDecodeError` 를 거부로 바꾼다
    - _Requirements: 17.1, 17.2, 17.4, 4.6, 7.5, 3.4_

  - [ ] 14.3 revise `--resolves Q-… [Q-…]`, `--ttl-hours`, `--note`
    - `--ttl-hours` 가 없으면 직전 판본의 유효기간 (v1 판본이면 72)
    - `--resolves` 는 중복을 지우고, 형식이 틀리거나 그 협의에서 제기되지 않은 번호가 있으면 거부한다
    - rev = 현재 + 1, `approval_ttl_h`·`resolves` 를 싣는다
    - _Requirements: 7.1, 7.4, 17.1, 17.3, 17.4_

  - [ ] 14.4 withdraw 명령 (`--thread`, `--reason`)
    - 사유는 `text` 로 싣는다. 협의를 연 party 가 아니면 J13 으로 거부되고, 기록 뒤의 새 기록 요청은 모두 J6 으로 거부된다
    - _Requirements: 9.1, 9.2, 9.5_

  - [ ] 14.5 relay 를 기록된 사람 발화를 옮기는 명령으로 바꾼다
    - `--reject` 는 값 없는 플래그다. `nargs="?"` 로 받되 값이 붙어 오면 "사유는 내 사람이 기록한 반려에서 가져온다 — --reject 에 사유를 주지 않는다" 로 거부한다 (argparse 오류로 죽지 않는다)
    - 승인: 평가 시각에 유효한 내 승인이 있어야 하고, 그 승인 행의 `signed` 를 그대로 싣는다. 반려: 현재 판본의 내 사람 반려가 있어야 하고, text = 그 행의 사유, `signed` 그대로
    - 출력 안내를 서명 여부에 맞춘다 (서명이 실렸으면 상대 쪽에서 검증된 전언이 될 수 있다)
    - _Requirements: 6.3, 6.4, 6.5, 16.1, 18.6_

  - [ ] 14.6 CLI 기록 명령 시험
    - init: 두 키 등록과 지문 출력, 미지원 키 종류(`ssh-rsa`)는 이름과 지원 목록을 밝혀 거부하고 설정 바이트 불변, 개인키 모양 입력은 값을 출력하지 않음, 키 없는 init 은 `null`
    - propose: 기본 72, `--ttl-hours 48`, `0`·`721`·`72.0`·`abc` 거부. revise: 유효기간 계승, `--resolves` 로 지목한 쟁점만 해소, 제기되지 않은 번호 거부
    - P7 (CLI 쪽): 상대가 연 협의에 대한 내 withdraw 거부, 내가 연 협의를 withdraw 한 뒤 question·revise·relay·ingest 모두 거부
    - P9 (CLI 쪽): 내 사람 반려 줄 없이 `relay --reject` 거부 + 원장 불변, 반려 줄이 있으면 그 사유와 `signed` 가 그대로 실림, `--reject 사유` 는 거부
    - _Requirements: 21.1, 21.4, 21.5, 12.4, 12.5, 17.2, 17.3, 7.4, 9.2, 9.5, 6.4, 6.5_

  - [ ]* 14.7 반려 전언 속성 시험
    - **Property 11: 반려 전언은 기록된 반려를 옮길 뿐이다**
    - 무작위 사유(서명 있음·없음)로 내 사람 반려를 기록한 뒤 `relay --reject` 가 만든 reject_relay 의 text 는 그 사유, `signed` 는 반려 행의 것과 같다. 내 사람 반려가 없으면 거부된다
    - **Validates: Requirements 6.4, 6.5, 16.1**

  - [ ]* 14.8 공개키 한 줄 검증 속성 시험
    - **Property 16: 공개키 한 줄 검증**
    - 유효한 줄에 무작위 형식 변형(토큰 삭제, base64 깨기, 내부 종류 바꾸기, 길이 바꾸기, 바이트 붙이기)을 가하면 init 이 거부되고 설정 바이트가 그대로다. 유효한 줄의 출력 지문은 `"SHA256:"` + 패딩 없는 base64(sha256(blob)) 다
    - **Validates: Requirements 12.3, 12.5**

  - [ ]* 14.9 유효기간 계승 속성 시험
    - **Property 25: 유효기간 계승**
    - 무작위 t(1–720)로 연 협의에 `--ttl-hours` 없는 revise 가 만든 봉투의 `approval_ttl_h` 는 t 다
    - **Validates: Requirements 17.3**

  - [ ]* 14.10 무흔적 거부 속성 시험
    - **Property 2: 거부는 흔적을 남기지 않는다**
    - 유효한 봉투·사람 줄·기록 명령(propose, question, revise, relay, execute, withdraw)에 수신 규칙 하나를 어기는 무작위 변형을 가하면 rc 2, 표준 오류는 `거부:` 로 시작, 원장·설정 바이트는 그대로다
    - **Validates: Requirements 3.1, 3.2, 3.4**

  - [ ]* 14.11 추가 전용 원장 속성 시험
    - **Property 3: 원장은 늘기만 한다**
    - 무작위 기록 명령·ingest 시퀀스에서 각 명령 전 원장 바이트는 명령 뒤 원장 바이트의 접두사다
    - **Validates: Requirements 1.6**

  - [ ]* 14.12 수신·재생 일치 모형 기반 시험
    - **Property 4: 수신 판정과 재생 판정의 일치 (모형 기반)**
    - 반복마다 6–12개의 후보 행 시퀀스(상대 봉투, 내 사람 줄, 내 기록 명령, 일부는 규칙 위반, 서명은 반복당 2개 이하)를 시계를 무작위로 전진시키며 넣는다. 같은 시퀀스 전체(거부된 것 포함, `recorded_at` = 넣은 시각)를 같은 설정의 빈 원장에 `append` 로 심어 재생하면, 셈에 든 행은 수락된 행과 같고 거부된 행은 `excluded`·`misrecorded` 에 코드별 경고와 함께 들어가며 상태가 수신 쪽 `derive` 와 같다
    - **Validates: Requirements 1.5, 5.4, 6.7, 9.4, 10.3, 11.1, 11.2, 15.7, 16.7, 19.4**

  - [ ]* 14.13 기록 행 유효성 속성 시험
    - **Property 5: 기록된 행은 모두 유효하다**
    - 성공한 무작위 기록 명령·ingest 시퀀스 뒤 설정이 그대로면 `excluded`·`misrecorded` 가 비어 있고, CLI 가 만든 행의 `accord` 는 "2", rev 는 새 협의 1·revise 현재+1·그 밖 현재다
    - **Validates: Requirements 2.1, 4.6, 11.4**

  - [ ]* 14.14 종결 상태 수신 집합 속성 시험
    - **Property 9: 종결 상태의 수신 집합**
    - executed 협의에 무작위 새 기록 요청(ingest·CLI)을 넣으면 실행된 지문의 execute 와 approve_relay 만 수락되고, withdrawn 협의에는 아무것도 수락되지 않는다
    - **Validates: Requirements 5.7, 9.5**

- [ ] 15. CLI 출력 — request-approval, status, log
  - [ ] 15.1 request-approval `--out FILE`, `--reason TEXT`
    - 머리: `협의 … · rev … · 상태 …(만료 반영) · 승인 유효 N시간` 과 미해소 쟁점
    - rejected 면 "반려된 판본이다 — revise 로 새 판본을 올린 뒤 승인을 요청한다" 만, executed·withdrawn 이면 "종결된 협의다" 만 출력한다 (문장 없음)
    - 내 키 미등록: v1 승인 한 줄과 반려 형식, "내 사람 공개키가 등록돼 있지 않아 이 승인은 서명 없이 기록된다"
    - 내 키 등록: 서명 시각 = 평가 시각인 승인 문장과 반려 문장 틀을 `make_statement` 바이트 그대로 출력하고, 설계 11절의 서명 안내(`ssh-keygen -Y sign -f <개인키 경로> -n accord <문장 파일>` 은 텍스트일 뿐)를 붙인다. 이미 유효한 내 승인이 있으면 그 만료 시각도 보여 준다
    - `--out FILE`: 승인 문장(`--reason` 이 있으면 반려 문장, 키 미등록이면 v1 한 줄)을 `open(FILE, "w", encoding="utf-8", newline="\n")` 로 쓴다. 원장·설정은 건드리지 않는다
    - `--reason TEXT`: 앞뒤 공백을 지운 뒤 비었거나 줄바꿈이 있으면 거부, 아니면 완성된 반려 문장을 출력한다
    - 외부 프로그램을 부르지 않는다
    - _Requirements: 13.2, 13.3, 13.4, 13.5, 13.6, 10.4, 18.6, 1.2_

  - [ ] 15.2 `print_state` (협의 하나)
    - 첫 줄에 `승인 유효 N시간` 을 더한다
    - 유효 승인마다 party, 출처 ("내 사람 승인(서명 없음)" / "내 사람 서명 승인" / "검증 불가 전언" / "검증된 전언"), 기준 ("서명 시각 기준" / "기록 시각 기준"), 만료까지 남은 시간 (`expires_at − 평가 시각` 을 시간·분으로 내림)
    - agreed 면 "합의가 풀리는 시각: …Z", 만료된 승인마다 party 와 만료 시각 (없으면 "(없음)")
    - 무효화된 승인·미해소 쟁점·경고·다음 할 일 줄은 v1 그대로 두고, ingest 와 기록 명령 뒤 출력도 이 함수를 쓴다
    - _Requirements: 16.8, 20.1, 20.2, 20.3_

  - [ ] 15.3 전체 status 와 log
    - 전체 status: 협의마다 `derive` 를 부르고, `AccordError` 면 "⚠ T-… 손상된 협의 — …" 경고 줄로 바꿔 계속한다. 읽을 수 없는 줄이 있으면 "⚠ 원장에 읽을 수 없는 줄 N건 — 어느 협의에도 넣지 않았다". 종료 코드 0
    - `status --thread` 로 손상된 협의를 지정하면 `derive` 의 `AccordError` 그대로 거부한다
    - log: 형식이 깨진 행도 `.get()` 으로 읽어 죽지 않고, `recorded_at` 이 있으면 그것을, 없으면 `ts` 를 시각으로 찍는다
    - _Requirements: 3.5, 3.6_

  - [ ] 15.4 CLI 출력 시험
    - 시계를 고정하고 `redirect_stdout`·`redirect_stderr` 로 본다
    - request-approval: 승인 문장·반려 틀 바이트, `--out` 파일 바이트 (BOM 없음·LF·마지막 LF 하나), `--reason` 반려 문장과 빈·줄바꿈 사유 거부, 키 미등록이면 v1 한 줄과 "서명 없이 기록된다", rejected 면 revise 안내만, 유효기간 줄. `--out` 이 쓴 파일에 시험 서명기로 서명해 ingest 하면 기록된다
    - status 하나: 전언의 검증된·검증 불가 표시, 남은 시간 (예: "70시간 12분"), 합의가 풀리는 시각, 만료된 승인
    - 전체 status: 손상 협의 경고 줄·읽을 수 없는 줄 경고·rc 0, `status --thread` 로 지정한 손상 협의는 거부. log 는 깨진 행에서도 죽지 않는다
    - _Requirements: 21.5, 13.2, 13.4, 13.5, 10.4, 16.8, 20.1, 20.2, 20.3, 3.5, 3.6_

  - [ ]* 15.5 파생 상태 파일 없음 단위 시험
    - 명령 시퀀스(init, propose, question, revise, ingest, relay, request-approval, execute, withdraw, status, log) 뒤 시험이 쓴 입력 파일을 빼면 HOME 에 `config.json`·`ledger.jsonl` 만 있다 (`--out` 파일은 HOME 밖에 쓴다)
    - _Requirements: 1.7_

- [ ] 16. 체크포인트 — CLI
  - `python test_accord.py` 와 `python -W error::ResourceWarning test_accord.py` 를 돌려 기존 18개를 포함한 모든 시험이 통과하는지 확인한다. 질문이 생기면 사용자에게 묻는다.

- [ ] 17. 문서
  - [ ] 17.1 `SPEC.md` 를 "ACCORD 규격 v2" 로 올린다
    - 설계의 문서 갱신 계획을 따른다: 머리 (v1 대비 바뀐 점, 양쪽을 함께 올린다는 호환 메모), §1 계층 (L1 에 서명(선택)), §2 봉투 (v2 필드, 필드-타입 정합, `recorded_at` 은 원장 전용), §3 두 지문 공식과 판본 내용·정규화·내용 없는 revise, §4 승인 경계 (서명 없는·서명된 사람 줄, 정규 승인 문장 형식과 줄 끝 허용, J1–J17·S1–S7 요약, §4.1 전언 검증 정책, §4.2 SSHSIG·Ed25519 검증), §5 상태 기계 (반려 유지, 실행 조건, 종결 상태의 수신 집합, 만료에 따른 하강), §6 경고, §7 원장 (행 = 봉투 + `recorded_at`, v1 의 `{at, dir, kind, body}` 서술을 고침, 재생 일관성, 손상된 협의), §8 만료 (유효기간, 승인 시각 기준, 허용 오차 5분, 평가 시각), §9 실행면 없음 (서명을 만들지 않고 개인키를 읽지 않음), §10 P1–P10 봉합 규칙 표
    - v1 의 미구현 절에서 서명·만료를 구현된 항목으로 옮기고, 3인 이상 정족수와 키 등록 이상의 신원 보증 (키 교체·폐기 포함) 을 남은 항목으로 적는다
    - _Requirements: 22.1, 22.2, 22.3_

  - [ ] 17.2 `README.md` 를 v2 로 고친다
    - 승인 경계 절에 서명된 승인 흐름을 v1 한 줄 옆에 두고, 지문 설명에 유효기간을 더한다
    - "코드가 실제로 거부하는 것들" 표에 P1–P10 행과 서명·만료 경계 행 (서명 없는 승인, 다른 키, 이름공간, 문장 변조, 미지원 키, sk 사용자 존재, 서명 없는 전언, 만료된 승인, 미래 서명 시각) 을 더한다
    - 5분 실습 v2: 양쪽 init 에 자기 키와 상대 키 등록 (bash `--pubkey "$(cat ~/.ssh/id_ed25519.pub)"`, PowerShell `--pubkey (Get-Content $HOME\.ssh\id_ed25519.pub)`), 키 지문을 채팅 밖에서 대조, `propose --ttl-hours 48`, question → `revise --resolves Q-…`, `request-approval --out stmt.txt` → 사람이 `ssh-keygen -Y sign -f ~/.ssh/id_ed25519 -n accord stmt.txt` → 문장과 `stmt.txt.sig` 를 붙여 ingest, relay → 상대 쪽 검증된 전언, status 의 만료 표시, execute, 별도 예시로 withdraw. `ssh-keygen -Y` 는 OpenSSH 8.1 이상, 보안키는 8.2 이상이라는 메모
    - 정직한 한계에 요구사항 22.6 의 일곱 항목과 "지문 8자리는 사람이 대조하는 표식이지 충돌 저항 해시가 아니다" 를 적고, 기존의 "서명이 없다" 류 문장을 고친다
    - 시험 절과 파일 목록의 시험 개수를 `python test_accord.py` 가 보고한 실제 수로 고친다
    - _Requirements: 22.4, 22.5, 22.6, 22.7_

- [ ] 18. 최종 체크포인트
  - `python test_accord.py` 와 `python -W error::ResourceWarning test_accord.py` 를 돌려 기존 18개를 포함한 모든 시험이 통과하고, README 의 시험 개수가 보고된 수와 같은지 확인한다. 질문이 생기면 사용자에게 묻는다.

## 참고

- `*` 가 붙은 하위 작업은 선택이다 (속성 시험, 요구사항 21 이 요구하지 않는 단위 시험). 빠른 MVP 에서는 건너뛸 수 있다.
- 요구사항 21 이 직접 요구하는 시험 (구문 트리 검사, RFC 8032 벡터, P1–P10 경계, 원장 심기, 서명 경계, 만료 경계, 상태 출력의 남은 시간) 과 뒤의 필수 시험이 쓰는 도우미를 만드는 하위 작업 (1.4, 4.3, 5.2, 6.2, 7.2) 은 산출물이므로 선택으로 두지 않았다.
- 가장 위험한 곳은 9.5 (`derive` 교체) 와 10.1–10.2 (기록 경로 교체) 다. 바꾸기 전후로 기존 18개 시험을 돌린다. 기존 시험이 기대는 v1 거부 순서와 문구 (사람 봉투 → 사람 전용 타입 → 미등록 party, "지문 불일치", "권한 없는 승인 기록") 를 그대로 둔다.
- 시각은 `accord.clock` 교체로만 움직인다. 명령행·환경 변수로 시각을 바꾸지 않는다.
- 서명·검증은 한 번에 수 ms–수십 ms 다. 고정 시드 키를 재사용하고 검증 캐시를 쓴다. 전체 시험은 60초 안을 목표로 하고, 넘으면 반복 수가 아니라 시퀀스 길이를 줄인다.
- 각 하위 작업은 세부 요구사항 번호를, 속성 시험은 설계의 Property 번호를 적어 추적한다.
- 체크포인트 명령은 Windows 기준 `python` 이다. 다른 환경에서는 `python3` 로 같은 명령을 돌린다 (요구사항 21.10).
- 거의 모든 작업이 `accord.py` 나 `test_accord.py` 한 파일을 고치므로 의존 그래프는 대부분 한 줄로 이어진다. 새 함수만 더하는 구현 작업과 앞 단계 시험 작업만 짝지었고, 체크포인트 경계는 넘지 않는다.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.3"] },
    { "id": 1, "tasks": ["1.2", "1.4"] },
    { "id": 2, "tasks": ["2.1", "1.5"] },
    { "id": 3, "tasks": ["2.2", "1.6"] },
    { "id": 4, "tasks": ["2.3"] },
    { "id": 5, "tasks": ["4.1"] },
    { "id": 6, "tasks": ["4.2"] },
    { "id": 7, "tasks": ["5.1", "4.3"] },
    { "id": 8, "tasks": ["6.1", "5.2"] },
    { "id": 9, "tasks": ["7.1", "5.3"] },
    { "id": 10, "tasks": ["6.2"] },
    { "id": 11, "tasks": ["6.3"] },
    { "id": 12, "tasks": ["6.4"] },
    { "id": 13, "tasks": ["7.2"] },
    { "id": 14, "tasks": ["7.3"] },
    { "id": 15, "tasks": ["7.4"] },
    { "id": 16, "tasks": ["9.1"] },
    { "id": 17, "tasks": ["9.2"] },
    { "id": 18, "tasks": ["9.3"] },
    { "id": 19, "tasks": ["9.4"] },
    { "id": 20, "tasks": ["9.5"] },
    { "id": 21, "tasks": ["9.6"] },
    { "id": 22, "tasks": ["10.1"] },
    { "id": 23, "tasks": ["10.2"] },
    { "id": 24, "tasks": ["10.3"] },
    { "id": 25, "tasks": ["10.4"] },
    { "id": 26, "tasks": ["10.5"] },
    { "id": 27, "tasks": ["10.6"] },
    { "id": 28, "tasks": ["10.7"] },
    { "id": 29, "tasks": ["10.8"] },
    { "id": 30, "tasks": ["10.9"] },
    { "id": 31, "tasks": ["10.10"] },
    { "id": 32, "tasks": ["10.11"] },
    { "id": 33, "tasks": ["10.12"] },
    { "id": 34, "tasks": ["10.13"] },
    { "id": 35, "tasks": ["10.14"] },
    { "id": 36, "tasks": ["10.15"] },
    { "id": 37, "tasks": ["12.1"] },
    { "id": 38, "tasks": ["12.2"] },
    { "id": 39, "tasks": ["12.3"] },
    { "id": 40, "tasks": ["12.4"] },
    { "id": 41, "tasks": ["12.5"] },
    { "id": 42, "tasks": ["12.6"] },
    { "id": 43, "tasks": ["12.7"] },
    { "id": 44, "tasks": ["14.1"] },
    { "id": 45, "tasks": ["14.2"] },
    { "id": 46, "tasks": ["14.3"] },
    { "id": 47, "tasks": ["14.4"] },
    { "id": 48, "tasks": ["14.5"] },
    { "id": 49, "tasks": ["14.6"] },
    { "id": 50, "tasks": ["14.7"] },
    { "id": 51, "tasks": ["14.8"] },
    { "id": 52, "tasks": ["14.9"] },
    { "id": 53, "tasks": ["14.10"] },
    { "id": 54, "tasks": ["14.11"] },
    { "id": 55, "tasks": ["14.12"] },
    { "id": 56, "tasks": ["14.13"] },
    { "id": 57, "tasks": ["14.14"] },
    { "id": 58, "tasks": ["15.1"] },
    { "id": 59, "tasks": ["15.2"] },
    { "id": 60, "tasks": ["15.3"] },
    { "id": 61, "tasks": ["15.4"] },
    { "id": 62, "tasks": ["15.5"] },
    { "id": 63, "tasks": ["17.1", "17.2"] }
  ]
}
```
