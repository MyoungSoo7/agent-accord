"""R2R 제어기. 모두 배치(벡터) 연산 — 상태 shape 은 (n,), n = seed 수 또는 파라미터 점 수.

시점 규약 (Q1·Q2·Q3 공통):
  웨이퍼 k 를 연마한 뒤, 웨이퍼 k+1 의 레시피를 정하기 전에 웨이퍼 k−δ 의 계측값이 도착한다.
  δ = 0 이면 직전 웨이퍼의 계측값이 바로 다음 레시피에 쓰인다 (지연 없음).

  (A) 절편 추정형   â ← λ(y_j − b̂·u_j) + (1−λ)â,   u_{k+1} = (T − â)/b̂     (j = k−δ, u_j = 그 웨이퍼의 실제 레시피)
  (B) 속도(적분)형  u_{k+1} = u_k − (λ/b̂)(y_j − T)                         (u_k = 직전 웨이퍼의 실제 레시피)
  d-EWMA (Butler–Stefani 1994 / Del Castillo 1999 형)
                    ε = y_j − b̂u_j,  a ← λ1·ε + (1−λ1)a,  D ← λ2(ε − a_old) + (1−λ2)D,
                    u_{k+1} = (T − a − h·D)/b̂   (h = 1 이 표준형)
측정이 없는 스텝은 상태를 그대로 둔다. 한 스텝에 측정이 둘(지연 계측 + VM) 오면 도착 순서대로 두 번 갱신한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _vec(x, n):
    return np.broadcast_to(np.asarray(x, dtype=float), (n,)).copy()


class EWMAIntercept:
    """(A) 절편 추정형 EWMA."""

    def __init__(self, lam, b_hat, target, n):
        self.n, self.target = n, float(target)
        self.lam, self.b_hat = _vec(lam, n), _vec(b_hat, n)
        self.reset()

    def reset(self, mask=None):
        if mask is None:
            self.a = np.zeros(self.n)
        else:
            self.a = np.where(mask, 0.0, self.a)

    def update(self, y, u, mask=None):
        new = self.lam * (y - self.b_hat * u) + (1.0 - self.lam) * self.a
        self.a = new if mask is None else np.where(mask, new, self.a)

    def command(self, u_prev):
        return (self.target - self.a) / self.b_hat


class EWMAVelocity:
    """(B) 속도형(적분형) EWMA. 상태는 '직전 실제 레시피'와 아직 반영 안 된 보정량뿐이다."""

    def __init__(self, lam, b_hat, target, n):
        self.n, self.target = n, float(target)
        self.lam, self.b_hat = _vec(lam, n), _vec(b_hat, n)
        self.reset()

    def reset(self, mask=None):
        if mask is None:
            self.pending = np.zeros(self.n)
            self.to_nominal = np.ones(self.n, dtype=bool)
        else:
            self.pending = np.where(mask, 0.0, self.pending)
            self.to_nominal = self.to_nominal | mask

    def update(self, y, u, mask=None):
        corr = -(self.lam / self.b_hat) * (y - self.target)
        self.pending = self.pending + (corr if mask is None else np.where(mask, corr, 0.0))

    def command(self, u_prev):
        u = np.where(self.to_nominal, self.target / self.b_hat, u_prev) + self.pending
        self.pending = np.zeros(self.n)
        self.to_nominal = np.zeros(self.n, dtype=bool)
        return u


class DoubleEWMA:
    """d-EWMA (predictor–corrector). h = 1 이 표준형."""

    def __init__(self, lam1, lam2, b_hat, target, n, h=1.0):
        self.n, self.target = n, float(target)
        self.l1, self.l2 = _vec(lam1, n), _vec(lam2, n)
        self.b_hat, self.h = _vec(b_hat, n), _vec(h, n)
        self.reset()

    def reset(self, mask=None):
        if mask is None:
            self.a, self.D = np.zeros(self.n), np.zeros(self.n)
        else:
            self.a = np.where(mask, 0.0, self.a)
            self.D = np.where(mask, 0.0, self.D)

    def update(self, y, u, mask=None):
        eps = y - self.b_hat * u
        a_new = self.l1 * eps + (1.0 - self.l1) * self.a
        D_new = self.l2 * (eps - self.a) + (1.0 - self.l2) * self.D
        if mask is None:
            self.a, self.D = a_new, D_new
        else:
            self.a, self.D = np.where(mask, a_new, self.a), np.where(mask, D_new, self.D)

    def command(self, u_prev):
        return (self.target - self.a - self.h * self.D) / self.b_hat


@dataclass(frozen=True)
class CtrlSpec:
    """제어기 설정 한 줄. kind ∈ {'A', 'B', 'D'} (D = d-EWMA)."""
    kind: str
    lam: float
    lam2: float = 0.0
    h: float = 1.0
    pm_reset: bool = False  # PM 직후 상태를 명목값으로 되돌릴지

    def build(self, n, b_hat, target):
        if self.kind == "A":
            return EWMAIntercept(self.lam, b_hat, target, n)
        if self.kind == "B":
            return EWMAVelocity(self.lam, b_hat, target, n)
        if self.kind == "D":
            return DoubleEWMA(self.lam, self.lam2, b_hat, target, n, h=self.h)
        raise ValueError(self.kind)


def apply_limits(u_cmd, u_prev, u_min, u_max, du_max):
    """사전 승인 범위 [u_min, u_max] 와 |Δu| ≤ du_max 를 강제한다.

    반환: (u, rate_limited, pinned)
      rate_limited : 변경 한도가 실제로 묶였다 (경계가 더 먼저 묶인 경우는 제외)
      pinned       : 레시피가 경계에 붙어 있고 제어기는 그 바깥을 원한다 (경계 포화)
    """
    lo, hi = u_prev - du_max, u_prev + du_max
    u = np.clip(np.clip(u_cmd, lo, hi), u_min, u_max)
    rate_limited = ((u_cmd > hi) & (hi < u_max)) | ((u_cmd < lo) & (lo > u_min))
    pinned = ((u_cmd > u_max) & (u >= u_max)) | ((u_cmd < u_min) & (u <= u_min))
    return u, rate_limited, pinned
