"""시험: 제어기 갱신 수식, 특성다항식 ↔ 시뮬레이션 일치, 시뮬레이터 재현성, VM·지표 기본 동작.

실행: .venv/bin/python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from r2r import metrics as M, sim, stability as S, vm as V  # noqa: E402
from r2r.controllers import CtrlSpec, DoubleEWMA, EWMAIntercept, EWMAVelocity, apply_limits  # noqa: E402
from r2r.process import CMP, Disturbance, make_disturbance, pad_schedule  # noqa: E402


def drift_plant(n_wafers, b, drift, n=1):
    """잡음 없는 플랜트: RR = b 고정, 가산 외란 e_k = drift·k (선형 드리프트)."""
    z = np.zeros((n, n_wafers))
    pad_idx, age = pad_schedule(n_wafers, 0)
    e = np.tile(drift * np.arange(n_wafers, dtype=float), (n, 1))
    return Disturbance(rr=z + b, e=e, v=z.copy(), dP=z.copy(), dV=z.copy(), ima=z.copy(), age=age,
                       pad_idx=pad_idx, pm_wafers=np.array([], dtype=int), seeds=())


class TestControllerMath(unittest.TestCase):
    def test_ewma_intercept_update(self):
        c = EWMAIntercept(0.3, 50.0, 5000.0, 1)
        self.assertAlmostEqual(c.command(np.array([100.0]))[0], 100.0)
        c.update(np.array([5100.0]), np.array([100.0]))      # ε = 5100 − 50·100 = 100
        self.assertAlmostEqual(c.a[0], 30.0)
        self.assertAlmostEqual(c.command(np.array([0.0]))[0], (5000.0 - 30.0) / 50.0)
        c.update(np.array([9999.0]), np.array([100.0]), mask=np.array([False]))  # 마스크 밖은 그대로
        self.assertAlmostEqual(c.a[0], 30.0)
        c.update(np.array([4950.0]), np.array([99.0]))       # ε = 4950 − 4950 = 0
        self.assertAlmostEqual(c.a[0], 0.7 * 30.0)

    def test_ewma_velocity_update(self):
        c = EWMAVelocity(0.3, 50.0, 5000.0, 1)
        self.assertAlmostEqual(c.command(np.array([123.0]))[0], 100.0)  # 첫 명령 = 명목 레시피
        c.update(np.array([5100.0]), np.array([100.0]))
        self.assertAlmostEqual(c.command(np.array([100.0]))[0], 100.0 - 0.3 / 50.0 * 100.0)
        self.assertAlmostEqual(c.command(np.array([99.4]))[0], 99.4)    # 새 측정 없으면 유지

    def test_double_ewma_update(self):
        c = DoubleEWMA(0.4, 0.1, 50.0, 5000.0, 1)
        c.update(np.array([5100.0]), np.array([100.0]))  # ε=100: a=40, D=0.1·(100−0)=10
        self.assertAlmostEqual(c.a[0], 40.0)
        self.assertAlmostEqual(c.D[0], 10.0)
        self.assertAlmostEqual(c.command(None)[0], (5000.0 - 50.0) / 50.0)
        c.update(np.array([5100.0]), np.array([100.0]))  # a=0.4·100+0.6·40=64, D=0.1·(100−40)+0.9·10=15
        self.assertAlmostEqual(c.a[0], 64.0)
        self.assertAlmostEqual(c.D[0], 15.0)

    def test_limits_and_flags(self):
        u, rl, pin = apply_limits(np.array([130.0]), np.array([122.0]), 80.0, 125.0, 5.0)
        self.assertEqual((u[0], bool(rl[0]), bool(pin[0])), (125.0, False, True))   # 경계가 먼저 묶임
        u, rl, pin = apply_limits(np.array([110.0]), np.array([100.0]), 80.0, 125.0, 5.0)
        self.assertEqual((u[0], bool(rl[0]), bool(pin[0])), (105.0, True, False))   # 변경 한도
        u, rl, pin = apply_limits(np.array([70.0]), np.array([82.0]), 80.0, 125.0, 5.0)
        self.assertEqual((u[0], bool(rl[0]), bool(pin[0])), (80.0, False, True))
        u, rl, pin = apply_limits(np.array([101.0]), np.array([100.0]), 80.0, 125.0, 5.0)
        self.assertEqual((u[0], bool(rl[0]), bool(pin[0])), (101.0, False, False))

    def test_drift_offsets(self):
        """선형 드리프트 d/wafer, ξ=1, 지연 δ: EWMA 정상상태 오차 = d(δ + 1/λ), d-EWMA(h=1) = d·δ."""
        p = CMP()
        d = 2.0
        for delay in (0, 2, 5):
            dist = drift_plant(3000, p.b_hat, d)
            lam = 0.3
            r = sim.run(p, dist, CtrlSpec("A", lam), delay, limits=False)
            self.assertAlmostEqual(r.Y[0, -1] - p.target, d * (delay + 1.0 / lam), places=6)
            r = sim.run(p, dist, CtrlSpec("D", 0.4, 0.2), delay, limits=False)
            self.assertAlmostEqual(r.Y[0, -1] - p.target, d * delay, places=6)

    def test_pm_reset_rebaselines(self):
        """PM 리셋: 기준 레시피로 즉시 복귀(변경 한도 무관), 이전 패드의 늦은 계측은 버린다."""
        p = CMP()
        dist = make_disturbance(p, 1200, (3,))
        r = sim.run(p, dist, CtrlSpec("A", 0.3, pm_reset=True), 2)
        for k in dist.pm_wafers:
            self.assertAlmostEqual(r.U[0, k], p.u_nom)
            self.assertAlmostEqual(r.U[0, k + 1], p.u_nom)
            self.assertAlmostEqual(r.U[0, k + 2], p.u_nom)  # 새 패드 계측이 아직 없음 (δ=2)


class TestStability(unittest.TestCase):
    POINTS = [(0.3, 1.0, 2), (0.5, 2.8, 1), (0.5, 3.3, 1), (0.2, 4.0, 3), (0.7, 1.5, 3), (0.9, 2.5, 0),
              (0.4, 2.6, 2), (0.15, 0.4, 3), (0.6, 0.9, 2), (0.8, 1.2, 1)]

    def _check(self, form, pts, lam2=0.0):
        rhos = [S.spectral_radius(S.poly(form, lam, xi, d, lam2=lam2)) for lam, xi, d in pts]
        self.assertTrue(min(rhos) < 1.0 < max(rhos))  # 안정·불안정 점이 모두 들어 있다
        for lam, xi, d in pts:
            rho = S.spectral_radius(S.poly(form, lam, xi, d, lam2=lam2))
            rho_hat = S.simulate_rho(form, lam, xi, d, n_steps=1500, lam2=lam2)[0]
            with self.subTest(form=form, lam=lam, xi=xi, delay=d, rho=rho):
                self.assertGreater(abs(rho - 1.0), 0.01)  # 시험 점은 경계에서 떨어뜨려 고른다
                self.assertEqual(rho < 1.0, rho_hat < 1.0)
                self.assertLess(abs(rho_hat - rho), 0.01)

    def test_poly_vs_sim_A(self):
        self._check("A", self.POINTS)

    def test_poly_vs_sim_B(self):
        self._check("B", self.POINTS)

    def test_poly_vs_sim_dewma(self):
        self._check("D", [(0.4, 1.0, 2), (0.3, 2.0, 1), (0.5, 3.0, 2), (0.2, 0.5, 3), (0.6, 2.5, 0)], lam2=0.1)

    def test_batch_coeffs_match_scalar(self):
        rng = np.random.default_rng(5)
        for form in ("A", "B", "D"):
            for d in (0, 1, 3):
                lam, xi, l2 = rng.uniform(0.05, 1, 6), rng.uniform(0.1, 4, 6), rng.uniform(0.01, 0.5, 6)
                C = S.coeffs_batch(form, lam, xi, d, lam2=l2)
                for i in range(6):
                    np.testing.assert_allclose(C[i], S.poly(form, lam[i], xi[i], d, lam2=l2[i]), atol=1e-12)
                np.testing.assert_allclose(S.rho_batch(form, lam, xi, d, lam2=l2),
                                           [S.spectral_radius(c) for c in C], atol=1e-9)

    def test_B_boundary_formula(self):
        for d in range(0, 7):
            self.assertAlmostEqual(S.critical_gain_B(d), S.critical_gain_B_formula(d), places=9)

    def test_A_closed_forms(self):
        for lam in (0.1, 0.3, 0.5, 0.9):
            self.assertAlmostEqual(S.xi_max_A(lam, 0), 2.0 / lam, places=8)
            self.assertAlmostEqual(S.xi_max_A(lam, 1), 1.0 + 1.0 / lam, places=8)
            self.assertAlmostEqual(S.xi_min_A(lam, 2), 0.0, places=8)

    def test_A_delay_robust_below_xi2(self):
        L, X = np.meshgrid(np.linspace(0.1, 1.0, 10), np.linspace(0.02, 1.98, 25))
        for d in (0, 1, 2, 5, 10, 20):
            self.assertLess(S.rho_batch("A", L, X, d).max(), 1.0)

    def test_A_xi1_delay_never_destabilizes(self):
        for d in (0, 3, 10, 25):
            for lam in (0.1, 0.5, 1.0):
                self.assertAlmostEqual(S.spectral_radius(S.poly_A(lam, 1.0, d)), 1.0 - lam, places=6)

    def test_B_equals_A_with_latest_recipe(self):
        p = CMP()
        dist = make_disturbance(p, 300, (21, 22))
        rB = sim.run(p, dist, CtrlSpec("B", 0.3), 2, limits=False)
        c = EWMAIntercept(0.3, p.b_hat, p.target, 2)
        up = np.full(2, p.u_nom)
        U, Y = np.empty((2, 300)), np.empty((2, 300))
        for k in range(300):
            u = c.command(up)
            U[:, k], Y[:, k] = u, dist.rr[:, k] * u + dist.e[:, k]
            if k >= 2:
                c.update(Y[:, k - 2] + dist.v[:, k - 2], u)
            up = u
        np.testing.assert_allclose(rB.U, U, atol=1e-9)


class TestReproducibility(unittest.TestCase):
    def test_disturbance_same_seed(self):
        p = CMP()
        a, b = make_disturbance(p, 800, (1, 2, 3)), make_disturbance(p, 800, (1, 2, 3))
        for f in ("rr", "e", "v", "dP", "dV", "ima"):
            np.testing.assert_array_equal(getattr(a, f), getattr(b, f))
        c = make_disturbance(p, 800, (4, 5, 6))
        self.assertFalse(np.allclose(a.rr, c.rr))

    def test_seed_independent_of_batch(self):
        """seed 하나의 난수는 같이 돌린 다른 seed 나 웨이퍼 수에 영향받지 않는다."""
        p = CMP()
        a = make_disturbance(p, 1000, (7, 8))
        b = make_disturbance(p, 600, (8,))
        np.testing.assert_array_equal(a.rr[1, :600], b.rr[0])

    def test_closed_loop_reproducible(self):
        p = CMP()
        dist = make_disturbance(p, 1200, (11, 12))
        X = V.make_features(dist, p.b0, "silent")
        np.testing.assert_array_equal(X, V.make_features(dist, p.b0, "silent"))
        meas = np.zeros((2, 1200), dtype=bool)
        meas[:, :100] = True
        meas[:, ::5] = True
        outs = []
        for _ in range(2):
            ref, sc = V.burnin_scaling(X, 100)
            vmc = sim.VMConfig("vm_t2r", X, V.OnlineRidgeVM(2, ref, sc), t2_thr=60.0, res_thr=120.0)
            outs.append(sim.run(p, dist, CtrlSpec("A", 0.3, pm_reset=True), 2, measured=meas, vmc=vmc))
        np.testing.assert_array_equal(outs[0].Y, outs[1].Y)
        np.testing.assert_array_equal(outs[0].vm_used, outs[1].vm_used)


class TestVMAndMetrics(unittest.TestCase):
    def test_ridge_recovers_linear_map(self):
        rng = np.random.default_rng(0)
        n, p = 3, 5
        beta = rng.normal(size=(n, p))
        vm = V.OnlineRidgeVM(n, np.zeros((n, p)), np.ones((n, p)), p=p, forget=0.97, alpha=1e-9, min_count=10)
        for _ in range(40):
            x = rng.normal(size=(n, p))
            vm.add(np.ones(n, dtype=bool), x, 7.0 + np.einsum("kp,kp->k", x, beta))
        x = rng.normal(size=(n, p))
        np.testing.assert_allclose(vm.predict(x), 7.0 + np.einsum("kp,kp->k", x, beta), atol=1e-5)
        self.assertTrue(vm.ready.all())
        self.assertTrue((vm.t2(x * 50.0) > 10 * vm.t2(x)).all())  # 멀리 벗어난 입력은 T² 가 크다

    def test_relation_shift_invisible_to_input_novelty(self):
        """silent 시나리오의 PM 이동은 특징 공간에서 정상 변동과 같은 방향이다 (첫 패드엔 이동 없음)."""
        off, cp = V.relation_shifts((1, 2), "silent")
        self.assertTrue(np.all(cp[:, 0] == 0.0) and np.all(off == 0.0))
        self.assertTrue(np.all((np.abs(cp[:, 1:]) >= 0.03) & (np.abs(cp[:, 1:]) <= 0.05)))

    def test_metrics(self):
        Y = np.full((1, 40), 5000.0)
        Y[0, 10:13] = 5300.0      # PM(10) 뒤 3장 이탈
        rec = M.recovery_after_pm(Y, 5000.0, 150.0, np.array([10]), run_len=5, horizon=20)
        self.assertEqual(rec[0, 0], 3.0)
        mask = np.array([[0, 1, 1, 0, 1, 1, 1, 0]], dtype=bool)
        self.assertEqual(M.first_run(mask, 3)[0], 4)
        self.assertEqual(M.first_run(mask, 4)[0], -1)
        m, lo, hi, n = M.t_ci([1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(m, 2.5)
        self.assertLess(lo, m)
        self.assertGreater(hi, m)


if __name__ == "__main__":
    unittest.main()
