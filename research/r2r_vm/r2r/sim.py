"""폐루프 시뮬레이터 — Q1 확인·Q2·Q3 가 모두 이 루프 하나를 쓴다.

한 스텝 k:
  1) (PM 리셋이면) PM 웨이퍼에서 제어기 상태·레시피를 명목값으로, 이전 패드 웨이퍼의 늦은 계측은 버림
  2) 제어기 명령 → 사전 승인 범위·변경 한도 적용 → 웨이퍼 k 연마 (y_k = RR_k·u_k + e_k)
  3) VM 이 있으면 웨이퍼 k 의 FDC 로 예측 ŷ_k 와 신뢰지수(T²)를 계산 (지연 0)
  4) 웨이퍼 j = k−δ 가 계측 대상이면 그 계측값이 도착 → 제어기 갱신, VM 재학습, (선택) VM 사후 검증
  5) 웨이퍼 k 가 비계측이고 전략이 허락하면 ŷ_k 로 제어기 갱신
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .controllers import CtrlSpec, apply_limits
from .process import CMP, Disturbance


@dataclass
class VMConfig:
    """Q3 VM 사용 방식.

    meas   : 계측 웨이퍼만 피드백
    vm_all : 비계측 웨이퍼 전부에 VM
    vm_t2  : T² ≤ 문턱일 때만 VM
    vm_t2r : T² 문턱 + 사후 검증(가장 최근 PM 이후 계측 웨이퍼의 VM 잔차 ≤ 문턱)
    vm_refresh : PM 마다 VM 학습 기억을 지우고 PM 이후 계측 웨이퍼로만 다시 학습, 사용은 T² 문턱
                 (PM 뒤 전수 계측 구간은 measured 마스크로 준다)
    """
    strategy: str
    X: np.ndarray                 # (n, N, p) FDC 요약 특징
    vm: object                    # vm.OnlineRidgeVM
    t2_thr: float = np.inf        # T² 문턱 (vm_t2, vm_t2r)
    res_thr: float = np.inf       # 사후 검증 잔차 문턱 (vm_t2r), Å
    pm_aware: bool = True         # vm_t2r: PM 직후 '미검증' 상태로 되돌림


@dataclass
class SimResult:
    U: np.ndarray
    Y: np.ndarray
    pinned: np.ndarray
    rate_limited: np.ndarray
    measured: np.ndarray
    vm_used: np.ndarray | None = None
    vm_pred: np.ndarray | None = None
    t2: np.ndarray | None = None
    vm_valid: np.ndarray | None = None


def run(p: CMP, dist: Disturbance, spec: CtrlSpec, delay: int, measured: np.ndarray | None = None,
        vmc: VMConfig | None = None, b_hat=None, limits: bool = True, controller=None) -> SimResult:
    n, N = dist.n_seeds, dist.n_wafers
    b_hat = p.b_hat if b_hat is None else b_hat
    ctrl = controller if controller is not None else spec.build(n, b_hat, p.target)
    if measured is None:
        measured = np.ones((n, N), dtype=bool)
    u_min, u_max, du_max = (p.u_min, p.u_max, p.du_max) if limits else (-np.inf, np.inf, np.inf)
    pm_set = set(int(k) for k in dist.pm_wafers) if spec.pm_reset else set()

    U = np.empty((n, N))
    Y = np.empty((n, N))
    pinned = np.zeros((n, N), dtype=bool)
    rlim = np.zeros((n, N), dtype=bool)
    u_prev = np.broadcast_to(np.asarray(p.target / np.asarray(b_hat, float)), (n,)).astype(float)

    use_vm = vmc is not None
    if use_vm:
        vm, X, strat = vmc.vm, vmc.X, vmc.strategy
        vm_used = np.zeros((n, N), dtype=bool)
        vm_pred = np.full((n, N), np.nan)
        t2 = np.full((n, N), np.nan)
        vm_valid_hist = np.ones((n, N), dtype=bool)
        ready_at = np.zeros((n, N), dtype=bool)  # 예측 시점에 모델이 준비돼 있었나
        valid = np.ones(n, dtype=bool)
        pm_all = set(int(k) for k in dist.pm_wafers)
        last_pm = 0

    u_base = np.broadcast_to(np.asarray(p.target / np.asarray(b_hat, float)), (n,)).astype(float)
    ctx_start = 0  # PM 리셋을 쓰면 문맥(패드)이 바뀐다 — 이전 패드 웨이퍼의 늦은 계측은 버린다
    for k in range(N):
        if k in pm_set:  # PM 리셋 = 승인된 기준 레시피로 재기준화. R2R 변경 한도(Δmax)의 대상이 아니다
            ctrl.reset()
            u_prev = u_base.copy()
            ctx_start = k
        if use_vm and k in pm_all:
            last_pm = k
            if strat == "vm_t2r" and vmc.pm_aware:  # 정비 뒤 VM 은 계측으로 다시 확인될 때까지 불신
                valid = np.zeros(n, dtype=bool)
            if strat == "vm_refresh":  # 정비 뒤 VM 을 새 패드 데이터로만 다시 학습
                vm.reset()
        u_cmd = ctrl.command(u_prev)
        u, rl, pin = apply_limits(u_cmd, u_prev, u_min, u_max, du_max)
        y = dist.rr[:, k] * u + dist.e[:, k]
        U[:, k], Y[:, k], pinned[:, k], rlim[:, k] = u, y, pin, rl

        if use_vm:  # 3) 웨이퍼 k 의 VM 예측 (계측 도착 전 모델 = 표본 외 예측)
            xk = X[:, k, :]
            vm_pred[:, k] = vm.predict(xk) * u
            t2[:, k] = vm.t2(xk)
            ready_at[:, k] = vm.ready

        j = k - delay  # 4) 지연 계측 도착
        if j >= 0:
            mm = measured[:, j]
            if mm.any():
                ym = Y[:, j] + dist.v[:, j]
                if j >= ctx_start:
                    ctrl.update(ym, U[:, j], mm)
                if use_vm:
                    if strat == "vm_t2r" and j >= last_pm:  # 사후 검증: 계측 웨이퍼의 표본 외 VM 잔차
                        ok = np.abs(ym - vm_pred[:, j]) <= vmc.res_thr
                        valid = np.where(mm & ready_at[:, j], ok, valid)
                    if strat != "vm_refresh" or j >= last_pm:
                        vm.add(mm, X[:, j, :], ym / U[:, j])

        if use_vm and strat != "meas":  # 5) 비계측 웨이퍼에 VM 사용
            use = ~measured[:, k] & vm.ready
            if strat in ("vm_t2", "vm_t2r", "vm_refresh"):
                use &= t2[:, k] <= vmc.t2_thr
            if strat == "vm_t2r":
                use &= valid
            if use.any():
                ctrl.update(vm_pred[:, k], u, use)
            vm_used[:, k] = use
            vm_valid_hist[:, k] = valid
        u_prev = u

    res = SimResult(U=U, Y=Y, pinned=pinned, rate_limited=rlim, measured=measured)
    if use_vm:
        res.vm_used, res.vm_pred, res.t2, res.vm_valid = vm_used, vm_pred, t2, vm_valid_hist
    return res
