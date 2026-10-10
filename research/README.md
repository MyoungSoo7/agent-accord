# 반도체 공정 × IoT 연구

이 리포의 클라우드 작업 환경(4 vCPU, RAM 15 GiB, GPU 없음, 네트워크 제한)에서 돌린 연구 네 편이다. 편마다 디렉터리 하나에 코드·시험·결과·보고서(README)가 같이 있고, 명령 하나로 결과를 다시 만든다.

| # | 주제 | 디렉터리 | 근거 | 상태 |
| --- | --- | --- | --- | --- |
| 1 | ACCORD × 팹 레시피 변경 승인 — 에이전트는 제안만, 승인은 엔지니어만 | [`fab_recipe/`](fab_recipe/README.md) | 시뮬레이션 + 실제 `accord.py` | 완료 |
| 2 | R2R(Run-to-Run) 제어 + 가상 계측 — 지연·이득 불일치 안정성, 계측 표본화와 VM | [`r2r_vm/`](r2r_vm/README.md) | 해석 + 시뮬레이션 | 완료 |
| 3 | 장비 연동 프로토콜 — SECS/GEM · OPC UA · MQTT(Sparkplug B), SECS/GEM→MQTT 게이트웨이 실측 | [`equipment_protocols/`](equipment_protocols/README.md) | 실제 구현을 루프백에서 측정 | 완료 |
| 4 | FDC 이상 탐지 — UCI SECOM 실데이터 | [`fdc_secom/`](fdc_secom/README.md) | 공개 실데이터 | 완료 |

## 한 편에 한 줄

1. **사람 승인을 남겨도 손실은 없앨 수 있다 — 에이전트가 승인 지연만큼 먼저 제안하면.** 승인 지연 중앙값 8 h 에서 규격 밖 웨이퍼가 반응형 제안 33.38, 예측형 6.33, 승인 없는 자율 5.41 /1000매다. 대가는 사람에게 가는 제안 수다. 계측 오프셋이 마모와 같은 신호를 낸 사례에서는 승인을 거친 경로가 규격 밖 0매, 자율이 327매였다.
2. **R2R 은 계측을 그 웨이퍼의 레시피와 짝지어 구현하고, PM 을 운영으로 다룬다.** 절편형 EWMA 는 0 < ξ < 2 이면 지연과 무관하게 안정이고(0 < λ ≤ 1), 속도형은 λξ < 2·sin(π/(4δ+2)) 로 지연에 약하다. PM 때 재기준화가 MSE 를 41 % 줄여 제어기 선택(d-EWMA 와의 차이 2 % 미만)보다 크다. PM 뒤 25장 전수 계측 + VM 재학습이면 세 시나리오 모두 계측 13.5 % 에서 동등 품질이다.
3. **장애 때 무엇이 사라지는가는 프로토콜과 ACK 위치가 정한다.** 소비자 2 초 정지에서 손실 0 은 MQTT QoS1 + 영속 세션과 GEM 스풀 + S6F23 뿐이다. OPC UA queueSize=1 은 변화 1000 개 중 11 개만 전달했다. 게이트웨이가 S6F12 를 받자마자 돌려주면 반복당 172–221 건이 장비 모르게 사라졌다. 이벤트 하나(N=10)는 SECS-II 147 B, OPC UA 509 B 다.
4. **시간을 섞어 재면 좋아 보이고, 시간순으로 재면 신호는 약하다.** 무작위 CV 는 ROC-AUC 를 평균 +0.121 부풀리고, 센서를 하나도 안 보는 시간 기준선이 무작위 CV 에서 0.682 를 받는다. 시간순으로는 L1 로지스틱 0.621 [0.544, 0.690] 이 가장 낫고, 고전 MSPC 는 0.490 이다. "소수의 핵심 신호" 는 시기마다 바뀐다 (fold 간 top-10 Jaccard 0.016–0.088).

## 네 편을 잇는 줄기

- **자동화의 경계와 사람 승인.** 연구 2 는 R2R 이 승인된 범위의 끝에 닿는 시점을 결정론으로 예측할 수 있음을 보였다 (첫 포화 998 [962, 1,034] 장, 예측 1,009). 연구 1 은 그 시점을 사람 승인 요청의 트리거로 쓰고, 승인 지연을 앞당겨 덮는 값과 대가를 쟀다. 두 연구는 공정 수치가 서로 다르다 — 같은 모델을 공유하지 않는다.
- **PM(정비)이 매번 중심에 있다.** 연구 2 에서 PM 재기준화가 가장 큰 개선이었고, VM 은 PM 뒤 재학습이 있어야 안전했다. 연구 1 에서 승인이 PM 뒤에 도착해 쓸모없어진 경우가 100패드당 최대 37.4건이었다 — 승인을 장비 상태에 묶어야 한다. 연구 4 의 SECOM 에는 PM·장비·레시피 문맥이 없어 FDC 의 기본 조치(장비별 기준선, 정비 뒤 재기준화)를 시험하지 못했다. 연구 3 의 Sparkplug 재탄생은 게이트웨이 쪽의 같은 문제다.
- **이벤트와 측정값은 다른 길로 보낸다.** 레시피 변경·승인·알람 같은 이산 이벤트는 연구 3 의 손실 0 경로(QoS1 + 영속 세션, GEM 스풀)로, FDC 트레이스는 표본을 잃어도 되는지 먼저 정한 뒤 OPC UA 데이터 변경·Sparkplug QoS0 로 보낸다. 연구 4 가 말하는 "수집 쪽에서 필요한 것"(장비·챔버 ID, 레시피, lot, PM 이력)은 연구 3 의 탄생 메시지·보고 정의가 실어 나를 자리다.
- **판정 규칙도 검증 대상이다.** 연구 1 에서 "qual 5매 Cpk ≥ 1.33" 은 정상 공정을 50.6% 떨어뜨렸고, 연구 4 에서 교과서 MSPC 의 99% 한계는 학습한 정상 자료에서도 8배쯤 틀렸다. 규칙과 한계는 실제(폐루프·시간순) 분포로 다시 재야 한다.

## 실행

리포 루트에서:

```bash
# 연구 1 — 표준 라이브러리만
python3 test_accord.py && python3 research/fab_recipe/test_fab.py && python3 research/fab_recipe/run_all.py

# 연구 2–4 — 디렉터리마다 가상환경 (처음 한 번)
for d in r2r_vm equipment_protocols fdc_secom; do
  (cd research/$d && uv venv .venv --python 3.13 && uv pip install --python .venv/bin/python -r requirements.txt)
done
apt-get install -y mosquitto     # 연구 3 의 브로커

(cd research/r2r_vm && .venv/bin/python -m unittest discover -s tests && .venv/bin/python run_all.py)   # 약 2.5분
(cd research/equipment_protocols && .venv/bin/python -m pytest && .venv/bin/python run_all.py)          # 약 10분
(cd research/fdc_secom && .venv/bin/python -I fetch_data.py \
   && .venv/bin/python -I -m unittest discover -s tests -t . && .venv/bin/python -I run_all.py)       # 약 5–8분
```
 연구 1·2·4 는 seed 가 고정이라 다시 돌리면 같은 수치가 나온다 (실행 시간 기록만 다르다). 연구 3 의 지연·처리량은 기계와 부하에 따라 달라지고, 바이트 수와 손실·병합 계수는 거의 그대로 나온다.

## 환경과 데이터

- 연구 1 은 표준 라이브러리만 쓴다 (`accord.py` 와 같은 원칙). 2–4 는 편마다 `.venv` 와 고정된 `requirements.txt` 를 둔다.
- 이 환경은 PyPI·npm·GitHub·Ubuntu apt 만 열려 있다. UCI·Kaggle·arXiv·Hugging Face 는 막혀 있다.
- SECOM 은 공개 GitHub 사본 두 곳(MarwanAhmedHelmy/secom-defect-prediction, LakshmiNakshatra/Predictive-Models-for-Equipment-Fault-Detection---Secom-Dataset)에서 받았다. 두 사본의 파일이 바이트 단위로 같다 (sha256 `20f0e7ee…` · `126884cf…`). 원자료는 커밋하지 않고, `fetch_data.py` 가 다시 받아 해시로 검증한다.
- 웨이퍼 맵(WM-811K)은 Kaggle·MIRLab 에만 있어 받지 못했다. 합성 웨이퍼 맵으로 대신하지 않았다.

## 공통 한계

- 연구 1·2 는 그럴듯한 가정의 시뮬레이션이고 실제 팹 데이터로 보정하지 않았다. 연구 3 은 파이썬 구현과 Mosquitto 를 한 대의 루프백에서 쟀다. 실데이터는 연구 4 의 SECOM 하나다 (한 공장, 석 달, 불량 104개).
- 네 연구가 같은 4 vCPU 를 나눠 쓰며 돌았다. 결과 수치에는 영향이 없도록 seed 를 고정했지만, 연구 3 의 지연 꼬리와 각 연구의 실행 시간은 그 영향을 받았다.
