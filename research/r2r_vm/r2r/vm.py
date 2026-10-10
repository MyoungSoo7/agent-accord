"""Q3 — FDC 요약 특징 생성과 온라인 VM(가중 릿지) + 신뢰지수(T²).

특징은 웨이퍼마다 20개. 제거율과 이어진 것은 마찰·발열 계열(전류, 패드 온도, 전력, 진동)과
Preston 입력(하강압, 정반 속도), 패드 상태(컨디셔너 토크, 패드 수명)다. 나머지는 무관하다.
ρ_k = RR_k / b0 − 1 (상대 제거율 편차).

시나리오 (참 제거율 RR 은 세 시나리오가 똑같다 — 계측만 쓰는 제어는 결과가 같다):
  normal  : 센서가 ρ 를 그대로 본다. PM 뒤에도 특징→제거율 관계가 같다.
  offset  : PM 마다 모터 계열(정반·캐리어 전류, 정반 전력) 기저선이 함께 이동한다
            (ρ 로 환산해 ±0.10~0.15). 온도 계열은 그대로라 상관 구조가 깨진다 → T² 로 보인다.
  silent  : PM 마다 새 패드가 같은 마찰에서 다른 제거율을 낸다. 센서는 ρ − c_p 를 본다,
            c_p = ±0.03~0.05. 상관 구조는 그대로라 입력만 보는 T² 로는 안 보인다.
첫 패드(p=0)는 세 시나리오 모두 이동이 없다.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from .process import MAX_PADS, Disturbance

FEATURES = [
    # (이름, 단위, 설명)
    ("platen_current", "A", "정반 모터 전류 — 마찰 토크"),
    ("carrier_current", "A", "캐리어 모터 전류"),
    ("pad_temp_mean", "°C", "패드 평균 온도 — 마찰 발열"),
    ("pad_temp_max", "°C", "패드 최고 온도"),
    ("pad_temp_rise", "°C/s", "초기 10 s 온도 상승률"),
    ("platen_power", "kW", "정반 구동 전력 = 토크×속도"),
    ("downforce", "psi", "하강압 (Preston P)"),
    ("platen_rpm", "rpm", "정반 속도 (Preston V)"),
    ("head_rpm", "rpm", "헤드 속도"),
    ("slurry_flow", "ml/min", "슬러리 유량 (무관)"),
    ("slurry_temp", "°C", "슬러리 온도 (패드 온도에만 영향)"),
    ("cond_torque", "N·m", "컨디셔너 토크 — 패드 글레이징"),
    ("pad_life", "wafer", "패드 수명 카운터"),
    ("ring_pressure", "psi", "리테이닝 링 압력 (무관)"),
    ("zone1_pressure", "psi", "헤드 1구역 압력"),
    ("zone2_pressure", "psi", "헤드 2구역 압력"),
    ("vibration_rms", "g", "진동 RMS — 약한 연관"),
    ("humidity", "%", "챔버 습도, 느린 AR(1) (무관)"),
    ("slurry_ph", "-", "슬러리 pH (무관)"),
    ("dresser_rpm", "rpm", "드레서 속도 (무관)"),
]
P_FEAT = len(FEATURES)
SCENARIOS = ("normal", "offset", "silent")
OFFSET_RANGE = (0.10, 0.15)   # offset: 모터 계열 기저선 이동 (ρ 환산 크기, 부호 무작위)
SILENT_RANGE = (0.03, 0.05)   # silent: 센서에 안 보이는 패드별 제거율 이동 c_p
VM_FORGET, VM_ALPHA, VM_MIN_COUNT = 0.97, 0.05, 20
# 마찰·발열 계열 특징: 열 번호 → (ρ 에 대한 기울기, 센서 잡음 σ). make_features 가 이 값을 쓴다.
FRICTION = {0: (6.0, 0.05), 1: (2.5, 0.04), 2: (25.0, 0.2), 3: (32.0, 0.3), 4: (0.10, 0.002), 5: (0.54, 0.005),
            16: (0.02, 0.004)}


def friction_rr_resolution():
    """마찰·발열 계열을 모두 합쳤을 때 한 웨이퍼의 상대 제거율을 읽는 분해능 (1σ, 센서 잡음만)."""
    return 1.0 / np.sqrt(sum((a / s) ** 2 for a, s in FRICTION.values()))


def relation_shifts(seeds, scenario):
    """패드별 관계 이동: (offset 시나리오의 ρ-환산 기저선 이동, silent 의 c_p). 둘 다 (n, MAX_PADS)."""
    n = len(seeds)
    off, cp = np.zeros((n, MAX_PADS)), np.zeros((n, MAX_PADS))
    for i, s in enumerate(seeds):
        r = np.random.default_rng([int(s), 2, 0])
        sign = np.where(r.random(MAX_PADS) < 0.5, -1.0, 1.0)
        mag_off = r.uniform(*OFFSET_RANGE, MAX_PADS)
        mag_cp = r.uniform(*SILENT_RANGE, MAX_PADS)
        if scenario == "offset":
            off[i] = sign * mag_off
        elif scenario == "silent":
            cp[i] = sign * mag_cp
    off[:, 0] = 0.0
    cp[:, 0] = 0.0
    return off, cp


def make_features(dist: Disturbance, b0: float, scenario: str = "normal") -> np.ndarray:
    """(n, N, 20) 특징 배열. 제어기와 무관(레시피 u 에 의존하지 않음)하므로 전략 간에 공유된다."""
    if scenario not in SCENARIOS:
        raise ValueError(scenario)
    seeds = dist.seeds
    n, N = dist.n_seeds, dist.n_wafers
    off, cp = relation_shifts(seeds, scenario)
    pads = dist.pad_idx
    rho = dist.rr / b0 - 1.0
    rho_fric = rho - cp[:, pads]               # 마찰 계열이 '보는' 제거율 편차
    mot = off[:, pads]                         # 모터 계열 기저선 이동 (ρ 환산)
    noise = np.empty((n, N, P_FEAT))
    hum = np.empty((n, N))
    for i, s in enumerate(seeds):
        r = np.random.default_rng([int(s), 1, 0])
        noise[i] = r.normal(0.0, 1.0, (N, P_FEAT))
        hum[i] = lfilter([1.0], [1.0, -0.995], r.normal(0.0, 0.2, N))  # 느린 AR(1)
    dP, dV, age = dist.dP, dist.dV, dist.age[None, :]
    ns = noise
    slurry_temp = 22.0 + 0.3 * ns[..., 10]
    X = np.empty((n, N, P_FEAT))
    F = FRICTION
    X[..., 0] = 14.0 + F[0][0] * (rho_fric + mot) + F[0][1] * ns[..., 0]
    X[..., 1] = 6.0 + F[1][0] * (rho_fric + mot) + F[1][1] * ns[..., 1]
    X[..., 2] = 42.0 + F[2][0] * rho_fric + 1.0 * (slurry_temp - 22.0) + F[2][1] * ns[..., 2]
    X[..., 3] = 48.0 + F[3][0] * rho_fric + 1.2 * (slurry_temp - 22.0) + F[3][1] * ns[..., 3]
    X[..., 4] = 0.20 + F[4][0] * rho_fric + F[4][1] * ns[..., 4]
    X[..., 5] = 1.2 + F[5][0] * (rho_fric + mot) + 1.2 * dV + F[5][1] * ns[..., 5]   # 전력 = 토크 × 속도
    X[..., 6] = 3.0 * (1.0 + dP) + 0.003 * ns[..., 6]
    X[..., 7] = 93.0 * (1.0 + dV) + 0.05 * ns[..., 7]
    X[..., 8] = 87.0 * (1.0 + 0.8 * dV) + 0.10 * ns[..., 8]
    X[..., 9] = 200.0 + 2.0 * ns[..., 9]
    X[..., 10] = slurry_temp
    X[..., 11] = 3.0 - 0.6 * (age / 500.0) + 0.05 * ns[..., 11]
    X[..., 12] = np.broadcast_to(age, (n, N))
    X[..., 13] = 4.0 + 0.04 * ns[..., 13]
    X[..., 14] = 3.0 * (1.0 + 0.9 * dP) + 0.01 * ns[..., 14]
    X[..., 15] = 3.0 * (1.0 + 0.9 * dP) + 0.01 * ns[..., 15]
    X[..., 16] = 0.05 + F[16][0] * rho_fric + F[16][1] * ns[..., 16]
    X[..., 17] = 45.0 + hum
    X[..., 18] = 10.5 + 0.05 * ns[..., 18]
    X[..., 19] = 90.0 + 0.5 * ns[..., 19]
    return X


class OnlineRidgeVM:
    """seed 배치마다 독립인 온라인 VM — 지수 망각 가중 릿지 (재귀형).

    학습: 계측 웨이퍼가 도착할 때마다 그 seed 의 모델만 다시 푼다. 과거 표본 가중치는 forget^나이
    (forget=0.97 → 유효 기억 ≈ 33 표본). 특징은 번인 구간 평균·표준편차로 고정 척도화한 뒤
    가중 평균을 빼서 쓴다. 절편은 벌점 없음.
    모델: RR̂ = c + zᵀβ,  z = (x̃ − μ_w),  β = (S_w + αI)⁻¹ s_xt   (목표 = 계측 제거율 y_meas/u, Å/s)
    예측: ŷ = RR̂ · u.
    신뢰지수: T² = zᵀ (S_w + αI)⁻¹ z  — 학습 분포 대비 입력의 새로움(정칙화 마할라노비스 거리).
    릿지 예측 분산과 같은 꼴이다.
    """

    def __init__(self, n, ref, scale, p=P_FEAT, forget=VM_FORGET, alpha=VM_ALPHA, min_count=VM_MIN_COUNT):
        self.n, self.p = n, p
        self.forget, self.alpha, self.min_count = forget, alpha, min_count
        self.ref = np.asarray(ref, float).reshape(n, p)
        self.scale = np.where(np.asarray(scale, float) > 1e-12, scale, 1.0).reshape(n, p)
        self.sw = np.zeros(n)              # Σw
        self.m1 = np.zeros((n, p))         # Σw x̃
        self.m2 = np.zeros((n, p, p))      # Σw x̃x̃ᵀ
        self.t1 = np.zeros(n)              # Σw t
        self.xt = np.zeros((n, p))         # Σw x̃ t
        self.cnt = np.zeros(n, dtype=int)
        self.mu = np.zeros((n, p))
        self.beta = np.zeros((n, p))
        self.c = np.zeros(n)
        self.Pm = np.broadcast_to(np.eye(p) / alpha, (n, p, p)).copy()
        self.ready = np.zeros(n, dtype=bool)

    def reset(self):
        """학습 기억을 모두 지운다 (PM 뒤 재학습용). 척도(ref, scale)는 유지."""
        n, p = self.n, self.p
        self.sw[:] = 0.0
        self.m1[:] = 0.0
        self.m2[:] = 0.0
        self.t1[:] = 0.0
        self.xt[:] = 0.0
        self.cnt[:] = 0
        self.mu[:] = 0.0
        self.beta[:] = 0.0
        self.c[:] = 0.0
        self.Pm[:] = np.eye(p) / self.alpha
        self.ready[:] = False

    def _tilde(self, x, idx=None):
        if idx is None:
            return (x - self.ref) / self.scale
        return (x - self.ref[idx]) / self.scale[idx]

    def add(self, mask, x, t):
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            return
        xt_new = self._tilde(x[idx], idx)
        tn = t[idx]
        f = self.forget
        self.sw[idx] = f * self.sw[idx] + 1.0
        self.m1[idx] = f * self.m1[idx] + xt_new
        self.m2[idx] = f * self.m2[idx] + xt_new[:, :, None] * xt_new[:, None, :]
        self.t1[idx] = f * self.t1[idx] + tn
        self.xt[idx] = f * self.xt[idx] + xt_new * tn[:, None]
        self.cnt[idx] += 1
        sw = self.sw[idx]
        mu = self.m1[idx] / sw[:, None]
        c = self.t1[idx] / sw
        S = self.m2[idx] / sw[:, None, None] - mu[:, :, None] * mu[:, None, :]
        sxt = self.xt[idx] / sw[:, None] - mu * c[:, None]
        Ginv = np.linalg.inv(S + self.alpha * np.eye(self.p)[None])
        self.beta[idx] = np.einsum("kpq,kq->kp", Ginv, sxt)
        self.mu[idx], self.c[idx], self.Pm[idx] = mu, c, Ginv
        self.ready[idx] = self.cnt[idx] >= self.min_count

    def predict(self, x):
        z = self._tilde(x) - self.mu
        return self.c + np.einsum("kp,kp->k", z, self.beta)

    def t2(self, x):
        z = self._tilde(x) - self.mu
        return np.einsum("kp,kp->k", z, np.matmul(self.Pm, z[:, :, None])[:, :, 0])


def burnin_scaling(X: np.ndarray, burn: int):
    """번인 구간(전수 계측)의 특징 평균·표준편차 — VM 고정 척도."""
    seg = X[:, :burn, :]
    return seg.mean(axis=1), seg.std(axis=1)
