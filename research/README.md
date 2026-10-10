# 반도체 공정 × IoT 연구

이 리포의 클라우드 작업 환경(4 vCPU, RAM 15 GiB, GPU 없음, 네트워크 제한)에서 돌린 연구 네 편이다. 편마다 디렉터리 하나에 코드·시험·결과·보고서(README)가 같이 있고, 명령 하나로 결과를 처음부터 다시 만든다.

| # | 주제 | 디렉터리 | 상태 |
| --- | --- | --- | --- |
| 1 | ACCORD × 팹 레시피 변경 승인 — 에이전트는 제안만, 승인은 엔지니어만 | [`fab_recipe/`](fab_recipe/README.md) | 완료 |
| 2 | R2R(Run-to-Run) 제어 + 가상 계측 — 지연·이득 불일치 안정성, 계측 표본화와 VM | [`r2r_vm/`](r2r_vm/README.md) | 완료 |
| 3 | 장비 연동 프로토콜 — SECS/GEM · OPC UA · MQTT(Sparkplug B) | `equipment_protocols/` | 진행 중 |
| 4 | FDC 이상 탐지 — UCI SECOM 실데이터 | `fdc_secom/` | 진행 중 |

## 환경과 데이터

- 연구 1 은 표준 라이브러리만 쓴다 (`accord.py` 와 같은 원칙). 2–4 는 편마다 `.venv` 와 고정된 `requirements.txt` 를 둔다.
- 이 환경은 PyPI·npm·GitHub 만 열려 있다. UCI·Kaggle·arXiv·Hugging Face 는 막혀 있다.
- SECOM 은 공개 GitHub 사본 두 곳(MarwanAhmedHelmy/secom-defect-prediction, LakshmiNakshatra/Predictive-Models-for-Equipment-Fault-Detection---Secom-Dataset)에서 받았다. 두 사본의 파일이 바이트 단위로 같다 (sha256 `20f0e7ee…` · `126884cf…`). 원자료는 커밋하지 않는다.
- 웨이퍼 맵(WM-811K)은 Kaggle·MIRLab 에만 있어 받지 못했다. 합성 웨이퍼 맵으로 대신하지 않았다.
