"""산화막 CMP 공정 모델.

웨이퍼 k 한 장 = 한 run.
    y_k  = RR_k · u_k + e_k                    제거량 (Å)
    RR_k = (b_pad · (1 − w·n_k) + W_k) · (1 + ΔP_k + ΔV_k)   제거율 (Å/s)
      b_pad : 패드(=PM 주기)마다 새로 뽑는 초기 제거율, b0·(1+s), s ~ N(0, pad_sd²)
      w     : 패드 마모에 따른 선형 감소율 (상대값 / wafer), n_k = 마지막 PM 이후 웨이퍼 수
      W_k   : IMA(1,1) 외란, W_k = W_{k−1} + a_k − θ·a_{k−1}, a ~ N(0, ima_sd²)
      ΔP,ΔV : 하강압·정반 속도의 웨이퍼 단위 상대 요동 (Preston: RR ∝ P·V)
      e_k   : FDC 로 보이지 않는 백색 잡음 (입고 막두께 등)
계측값은 y_k + v_k (v = 계측 잡음). 품질 지표는 참값 y_k 로만 잰다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

import numpy as np

MAX_PADS = 64  # 패드 오프셋 난수 길이를 고정 — 설정이 바뀌어도 같은 seed 면 같은 난수


@dataclass(frozen=True)
class CMP:
    target: float = 5000.0   # T, 목표 제거량 (Å)
    b0: float = 50.0         # 명목 제거율 (Å/s) = 3000 Å/min
    b_hat: float = 50.0      # 제어기 모델 이득 b̂ (Å/s)
    spec: float = 150.0      # 규격 T ± spec (Å), ±3 %
    u_min: float = 80.0      # 사전 승인 레시피 하한 (s)
    u_max: float = 125.0     # 사전 승인 레시피 상한 (s) — RR 40 Å/s 까지 보상
    du_max: float = 5.0      # 웨이퍼당 최대 레시피 변경 |Δu| (s)
    wear: float = 2e-4       # 패드 마모: RR 상대 감소 / wafer (500장에 10 %)
    pm_interval: int = 500   # PM(패드 교체) 주기, 0 이면 PM 없음
    pad_sd: float = 0.03     # 패드 간 초기 RR 상대 편차
    ima_theta: float = 0.85  # IMA(1,1) θ
    ima_sd: float = 0.3      # IMA 혁신 a_k 표준편차 (Å/s)
    p_sd: float = 0.004      # 하강압 상대 요동
    v_sd: float = 0.003      # 정반 속도 상대 요동
    e_sd: float = 25.0       # 관측 불가 백색 잡음 (Å)
    meas_sd: float = 10.0    # 계측 잡음 (Å)

    @property
    def u_nom(self) -> float:
        return self.target / self.b_hat

    def with_(self, **kw) -> "CMP":
        return replace(self, **kw)


@dataclass
class Disturbance:
    rr: np.ndarray        # (n_seeds, N) 참 제거율
    e: np.ndarray         # (n_seeds, N) 백색 잡음 (Å)
    v: np.ndarray         # (n_seeds, N) 계측 잡음 (Å)
    dP: np.ndarray        # (n_seeds, N)
    dV: np.ndarray        # (n_seeds, N)
    ima: np.ndarray       # (n_seeds, N) W_k
    age: np.ndarray       # (N,) 마지막 PM 이후 웨이퍼 수
    pad_idx: np.ndarray   # (N,)
    pm_wafers: np.ndarray # PM 직후 첫 웨이퍼 인덱스들 (k=0 제외)
    seeds: tuple
    b_pad: np.ndarray | None = None  # (n_seeds, MAX_PADS) 패드별 초기 제거율

    @property
    def n_seeds(self) -> int:
        return self.rr.shape[0]

    @property
    def n_wafers(self) -> int:
        return self.rr.shape[1]


def pad_schedule(n_wafers: int, pm_interval: int):
    k = np.arange(n_wafers)
    if pm_interval and pm_interval > 0:
        return k // pm_interval, k % pm_interval
    return np.zeros(n_wafers, dtype=int), k.copy()


def ima11(a: np.ndarray, theta: float) -> np.ndarray:
    """W_k = W_{k−1} + a_k − θ a_{k−1}, W_{−1}=a_{−1}=0 (마지막 축이 시간)."""
    a_prev = np.zeros_like(a)
    a_prev[..., 1:] = a[..., :-1]
    return np.cumsum(a - theta * a_prev, axis=-1)


def make_disturbance(p: CMP, n_wafers: int, seeds: Sequence[int]) -> Disturbance:
    """seed 마다 독립 난수 흐름. 성분별 하위 흐름을 따로 둬서 길이·설정과 무관하게 재현된다."""
    seeds = tuple(int(s) for s in seeds)
    n = len(seeds)
    pad_idx, age = pad_schedule(n_wafers, p.pm_interval)
    if pad_idx[-1] >= MAX_PADS:
        raise ValueError("패드 수가 MAX_PADS 를 넘는다")
    shape = (n, n_wafers)
    pad_s, a, dP, dV, e, v = (np.empty((n, MAX_PADS)), *(np.empty(shape) for _ in range(5)))
    for i, s in enumerate(seeds):
        pad_s[i] = np.random.default_rng([s, 0, 0]).normal(0.0, 1.0, MAX_PADS)
        a[i] = np.random.default_rng([s, 0, 1]).normal(0.0, 1.0, n_wafers)
        dP[i] = np.random.default_rng([s, 0, 2]).normal(0.0, 1.0, n_wafers)
        dV[i] = np.random.default_rng([s, 0, 3]).normal(0.0, 1.0, n_wafers)
        e[i] = np.random.default_rng([s, 0, 4]).normal(0.0, 1.0, n_wafers)
        v[i] = np.random.default_rng([s, 0, 5]).normal(0.0, 1.0, n_wafers)
    b_pad = p.b0 * (1.0 + p.pad_sd * pad_s)                 # (n, MAX_PADS)
    W = ima11(p.ima_sd * a, p.ima_theta)
    dP *= p.p_sd
    dV *= p.v_sd
    base = b_pad[:, pad_idx] * (1.0 - p.wear * age)[None, :]
    rr = (base + W) * (1.0 + dP + dV)
    pm_wafers = np.flatnonzero((age == 0) & (np.arange(n_wafers) > 0))
    return Disturbance(rr=rr, e=p.e_sd * e, v=p.meas_sd * v, dP=dP, dV=dV, ima=W,
                       age=age, pad_idx=pad_idx, pm_wafers=pm_wafers, seeds=seeds, b_pad=b_pad)


def constant_plant(xi: np.ndarray, b_hat: float, n_wafers: int, impulse: float = 300.0) -> Disturbance:
    """Q1 용 결정론적 플랜트(편차 변수): RR = ξ·b̂ 고정, 잡음 없음, k=0 에만 외란 impulse(Å).

    목표 T=0 과 함께 쓴다. 절대값(≈5000 Å)에서 오차를 빼면 부동소수 상쇄로 1e−9 Å 근처에
    바닥이 생겨 감쇠율을 잴 수 없다. 편차 변수에서는 감쇠가 순수한 곱셈이라 바닥이 없다.
    """
    xi = np.atleast_1d(np.asarray(xi, dtype=float))
    n = xi.size
    rr = np.repeat((xi * b_hat)[:, None], n_wafers, axis=1)
    z = np.zeros((n, n_wafers))
    e = z.copy()
    e[:, 0] = impulse
    pad_idx, age = pad_schedule(n_wafers, 0)
    return Disturbance(rr=rr, e=e, v=z.copy(), dP=z.copy(), dV=z.copy(), ima=z.copy(),
                       age=age, pad_idx=pad_idx, pm_wafers=np.array([], dtype=int), seeds=())
