"""Q1 — 폐루프 특성다항식, 스펙트럼 반경, 안정 경계, 시뮬레이션 확인.

플랜트 y_j = a + b·u_j, ξ = b/b̂, 지연 δ (controllers.py 의 시점 규약).

(B) 속도형:  y_{k+1} − y_k = b(u_{k+1} − u_k) = −λξ (y_{k−δ} − T)
     ⇒  χ_B(z) = z^{δ+1} − z^δ + λξ
(A) 절편형:  â_k = λ(a + (b − b̂)u_{k−δ}) + (1−λ)â_{k−1},  â_k = T − b̂u_{k+1}
     ⇒  u_{k+1} = (1−λ)u_k − λ(ξ−1)u_{k−δ} + λ(T−a)/b̂
     ⇒  χ_A(z) = z^{δ+1} − (1−λ)z^δ + λ(ξ−1)
d-EWMA (h = 예측 지평 계수, 표준형 h=1):
     χ_D(z) = z^δ (z−1+λ1)(z−1+λ2) + (ξ−1)[λ1(z−1+λ2) + h·λ2(z−1)]
"""
from __future__ import annotations

import numpy as np

from .controllers import DoubleEWMA, EWMAIntercept, EWMAVelocity
from .process import CMP, constant_plant
from . import sim


# ── 특성다항식 (최고차항부터) ──────────────────────────────────────────────
def poly_A(lam, xi, delay):
    c = np.zeros(delay + 2)
    c[0], c[1] = 1.0, -(1.0 - lam)
    c[-1] += lam * (xi - 1.0)
    return c


def poly_B(lam, xi, delay):
    c = np.zeros(delay + 2)
    c[0], c[1] = 1.0, -1.0
    c[-1] += lam * xi
    return c


def poly_D(l1, l2, xi, delay, h=1.0):
    c = np.zeros(delay + 3)
    c[:3] = np.polymul([1.0, -(1.0 - l1)], [1.0, -(1.0 - l2)])
    c[-2] += (xi - 1.0) * (l1 + h * l2)
    c[-1] += (xi - 1.0) * (l1 * (l2 - 1.0) - h * l2)
    return c


def poly(form, lam, xi, delay, lam2=0.0, h=1.0):
    if form == "A":
        return poly_A(lam, xi, delay)
    if form == "B":
        return poly_B(lam, xi, delay)
    if form == "D":
        return poly_D(lam, lam2, xi, delay, h)
    raise ValueError(form)


def spectral_radius(coeffs) -> float:
    r = np.roots(coeffs)
    return float(np.max(np.abs(r))) if r.size else 0.0


def spectral_radius_batch(C: np.ndarray) -> np.ndarray:
    """C: (n, d+1) 모닉 다항식 계수들 → 동반행렬 고윳값으로 스펙트럼 반경 (n,)."""
    n, d1 = C.shape
    d = d1 - 1
    M = np.zeros((n, d, d))
    M[:, 0, :] = -C[:, 1:] / C[:, :1]
    if d > 1:
        idx = np.arange(d - 1)
        M[:, idx + 1, idx] = 1.0
    return np.abs(np.linalg.eigvals(M)).max(axis=1)


def coeffs_batch(form, lam, xi, delay, lam2=0.0, h=1.0):
    """배열 인자로 특성다항식 계수 (n, 차수+1) 를 만든다."""
    lam, xi, lam2, h = (np.asarray(v, float).ravel() for v in np.broadcast_arrays(lam, xi, lam2, h))
    n = lam.size
    if form in ("A", "B"):
        C = np.zeros((n, delay + 2))
        C[:, 0] = 1.0
        if form == "A":
            C[:, 1] = -(1.0 - lam)
            C[:, -1] += lam * (xi - 1.0)
        else:
            C[:, 1] = -1.0
            C[:, -1] += lam * xi
        return C
    if form == "D":
        C = np.zeros((n, delay + 3))
        C[:, 0] = 1.0
        C[:, 1] = -(1.0 - lam) - (1.0 - lam2)
        C[:, 2] = (1.0 - lam) * (1.0 - lam2)
        C[:, -2] += (xi - 1.0) * (lam + h * lam2)
        C[:, -1] += (xi - 1.0) * (lam * (lam2 - 1.0) - h * lam2)
        return C
    raise ValueError(form)


def rho_batch(form, lam, xi, delay, lam2=0.0, h=1.0):
    shape = np.broadcast(np.asarray(lam), np.asarray(xi)).shape
    return spectral_radius_batch(coeffs_batch(form, lam, xi, delay, lam2, h)).reshape(shape)


def rho_grid(form, lams, xis, delay):
    """(len(xis), len(lams)) 격자의 스펙트럼 반경."""
    L, X = np.meshgrid(lams, xis)
    return rho_batch(form, L, X, delay)


# ── 안정 경계 ──────────────────────────────────────────────────────────────
def first_crossing(fbatch, lo, hi, n_scan=2000, tol=1e-12):
    """[lo, hi] 를 훑어 fbatch(배열→배열) 가 처음으로 1 이상이 되는 점을 이분법으로 찾는다."""
    xs = np.linspace(lo, hi, n_scan)
    vals = fbatch(xs)
    over = np.flatnonzero(vals >= 1.0)
    if over.size == 0:
        return np.inf
    i = over[0]
    if i == 0:
        return lo
    a, b = xs[i - 1], xs[i]
    while b - a > tol:
        m = 0.5 * (a + b)
        if fbatch(np.array([m]))[0] >= 1.0:
            b = m
        else:
            a = m
    return 0.5 * (a + b)


def critical_gain_B(delay):
    """(B) 의 임계 λξ — 수치 이분법 (ξ=1 로 두고 λ 를 훑는다; χ_B 는 λξ 에만 의존)."""
    return first_crossing(lambda K: rho_batch("B", K, 1.0, delay), 1e-6, 2.5)


def critical_gain_B_formula(delay):
    return 2.0 * np.sin(np.pi / (4 * delay + 2))


def xi_max_A(lam, delay, hi=60.0, n_scan=2000):
    """(A) 에서 ξ=1(안정)부터 위로 올라가며 처음 불안정해지는 ξ."""
    return first_crossing(lambda x: rho_batch("A", lam, x, delay), 1.0, hi, n_scan=n_scan)


def xi_min_A(lam, delay, lo=-5.0):
    """(A) 에서 ξ=1 부터 아래로 내려가며 처음 불안정해지는 ξ."""
    return 1.0 - first_crossing(lambda d: rho_batch("A", lam, 1.0 - d, delay), 0.0, 1.0 - lo)


def xi_max_D(l1, l2, delay, h=1.0, hi=30.0):
    return first_crossing(lambda x: rho_batch("D", l1, x, delay, l2, h), 1.0, hi)


def xi_min_D(l1, l2, delay, h=1.0):
    return 1.0 - first_crossing(lambda d: rho_batch("D", l1, 1.0 - d, delay, l2, h), 0.0, 2.0)


# ── 시뮬레이션으로 확인 ────────────────────────────────────────────────────
def growth_rate(err: np.ndarray, delay: int) -> np.ndarray:
    """오차 궤적에서 경험적 스펙트럼 반경 ρ̂ 를 추정한다.

    길이 δ+2 창의 max|e| 로그를 구해, 넘침·밑넘침 없는 창들의 뒤쪽 절반 기울기를 쓴다.
    끝까지 남은 창이 없으면: 0 으로 사라졌으면 ρ̂=0, 발산했으면 ρ̂=inf.
    """
    n, N = err.shape
    L = delay + 2
    nw = N // L
    M = np.abs(err[:, : nw * L]).reshape(n, nw, L).max(axis=2)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        logM = np.log(M)
    ok = np.isfinite(logM) & (logM > np.log(1e-250)) & (logM < np.log(1e250))
    out = np.empty(n)
    for i in range(n):
        idx = np.flatnonzero(ok[i])
        if idx.size >= 4:
            a, b = idx[idx.size // 2], idx[-1]
            if b == a:
                a = idx[0]
            out[i] = np.exp((logM[i, b] - logM[i, a]) / ((b - a) * L))
            if not np.isfinite(M[i, -1]) or M[i, -1] > 1e250:
                out[i] = max(out[i], 1.0 + 1e-9)  # 끝에서 넘쳤다면 불안정
        else:
            last = M[i, -1]
            out[i] = np.inf if (not np.isfinite(last) or last > 1.0) else 0.0
    return out


def simulate_rho(form, lam, xi, delay, n_steps=600, lam2=0.0, h=1.0, b_hat=50.0, target=0.0):
    """실제 제어기 코드(controllers.py)와 sim.run 으로 폐루프를 돌려 ρ̂ 를 잰다.

    잡음·범위 제한 없음. 편차 변수(T=0)에서 k=0 의 외란 300 Å 한 번에 대한 응답을 본다.
    """
    lam = np.atleast_1d(np.asarray(lam, float))
    xi = np.atleast_1d(np.asarray(xi, float))
    lam, xi = np.broadcast_arrays(lam, xi)
    n = lam.size
    dist = constant_plant(xi.ravel(), b_hat, n_steps)
    if form == "A":
        ctrl = EWMAIntercept(lam.ravel(), b_hat, target, n)
    elif form == "B":
        ctrl = EWMAVelocity(lam.ravel(), b_hat, target, n)
    elif form == "D":
        ctrl = DoubleEWMA(lam.ravel(), lam2, b_hat, target, n, h=h)
    else:
        raise ValueError(form)
    p = CMP(target=target, b_hat=b_hat)
    from .controllers import CtrlSpec
    with np.errstate(over="ignore", invalid="ignore"):
        res = sim.run(p, dist, CtrlSpec("A", 0.0), delay, controller=ctrl, limits=False)
        err = res.Y - target
    return growth_rate(err, delay + (1 if form == "D" else 0)).reshape(lam.shape)
