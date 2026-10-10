"""실험 공통 설정과 도우미. 숫자는 전부 여기서 고정한다."""
from __future__ import annotations

import numpy as np

from .process import CMP

P = CMP()                     # 기본 공정·제어 설정 (process.py 참고)
N_WAFERS = 2000               # Q2·Q3 한 run 의 웨이퍼 수 (PM: 500, 1000, 1500)
BURN = 100                    # 지표에서 뺄 초기 웨이퍼 수 (Q3 는 이 구간을 전수 계측)
DELAY = 2                     # 기준 계측 지연 δ (wafer)
EVAL_SEEDS = tuple(range(1000, 1100))   # 평가 100 seed
TUNE_SEEDS = tuple(range(5000, 5050))   # 튜닝 50 seed (평가와 겹치지 않음)

# Q2
LAM_GRID = (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7)
D_L1 = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
D_L2 = (0.01, 0.02, 0.05, 0.1, 0.2)
SENS_DELAY = (0, 1, 2, 5, 10)
SENS_WEAR = (0.0, 1e-4, 2e-4, 3e-4, 4e-4, 6e-4)
SAT_WEAR = (0.0, 1e-4, 2e-4, 3e-4, 4e-4, 6e-4, 8e-4)
SAT_N = 1500                  # 포화 연구: PM 없이 한 패드로 1500 장
SAT_K = 5                     # '지속 포화' = 연속 5장 경계 고정
REC_RUN = 5                   # PM 회복 = 연속 5장 규격 안이 시작될 때까지
POST_PM_WIN = 30              # PM 뒤 규격 이탈을 세는 창

# Q3
Q3_M = (1, 2, 5, 10, 25)
Q3_LAM = (0.1, 0.2, 0.3, 0.5, 0.7)
Q3_STRATS = ("meas", "meas_boost", "vm_all", "vm_t2", "vm_t2r", "vm_refresh")
K_BOOST = 25                  # PM 뒤 전수 계측 웨이퍼 수 (meas_boost, vm_refresh)
T2_Q = 0.99                   # T² 문턱 = 정상 운전 비계측 웨이퍼 T² 의 99 % 분위
RES_K = 3.0                   # 사후 검증 문턱 = 3 × 정상 운전 VM 잔차 RMS
EQ_TOL = 1.10                 # '동등 품질' = MSE ≤ 1.10 × 전수 계측(m=1) MSE
CAL_LAM = 0.3                 # 문턱 보정용 기준 λ


def sampling_mask(seeds, n_wafers, m, burn=BURN):
    """1/m 표본 계측. 위상은 seed 마다 무작위(고정 난수), 번인 구간은 전수 계측."""
    meas = np.zeros((len(seeds), n_wafers), dtype=bool)
    k = np.arange(n_wafers)
    for i, s in enumerate(seeds):
        ph = int(np.random.default_rng([int(s), 3, int(m)]).integers(m)) if m > 1 else 0
        meas[i] = ((k + ph) % m) == 0
    meas[:, :burn] = True
    return meas


def boost_mask(meas, pm_wafers, k_boost):
    """PM 직후 k_boost 장을 전수 계측으로 바꾼 마스크."""
    out = meas.copy()
    for k in pm_wafers:
        out[:, k : k + k_boost] = True
    return out


def steady_mask(n_wafers, pm_wafers, burn=BURN, after_pm=50):
    """정상 운전 구간: 번인과 PM 직후 after_pm 장을 뺀 웨이퍼."""
    st = np.ones(n_wafers, dtype=bool)
    st[:burn] = False
    for k in pm_wafers:
        st[k : k + after_pm] = False
    return st
