"""그림 — matplotlib Agg, 밝은 바탕 한 벌. 한글 글꼴이 없는 환경을 가정해 그림 안 글자는 영어로 둔다.

색: 범주형은 고정 순서(파랑·주황·청록·노랑·자홍·초록), 순서형은 파랑 한 색 명도 단계.
대비가 낮은 색(청록·노랑·자홍)은 표식 모양과 표(README·results.md)로 보완한다.
"""
from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

SURFACE, INK, INK2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
MARKERS = ["o", "s", "^", "D", "v", "P"]
ORDINAL = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]
REGION = "#cde2fb"
CRITICAL = "#d03b3b"
DPI = 110

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "axes.titlesize": 9.5, "axes.labelsize": 9, "font.size": 9, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "grid.linestyle": "-",
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 1.4,
    "lines.solid_capstyle": "round", "lines.solid_joinstyle": "round",
    "legend.frameon": False, "legend.fontsize": 8.5, "axes.axisbelow": True,
})


def _save(fig, path, dpi=DPI):
    fig.savefig(path, dpi=dpi, bbox_inches="tight", pil_kwargs={"optimize": True})
    plt.close(fig)
    return os.path.getsize(path)


def _ref(ax, y, label=None, axis="h", **kw):
    style = dict(color=MUTED, lw=0.9, ls=(0, (4, 3)), zorder=1)
    style.update(kw)
    if axis == "h":
        ax.axhline(y, **style)
    else:
        ax.axvline(y, **style)
    if label:
        ax.annotate(label, xy=(1.0, y), xycoords=("axes fraction", "data"), xytext=(-2, 2),
                    textcoords="offset points", ha="right", va="bottom", color=INK2, fontsize=8)


# ── Q1 ─────────────────────────────────────────────────────────────────────
def q1_regions(arrays, lam_fine, xi_fine, path, clear=0.01):
    from .stability import critical_gain_B_formula
    fig, axes = plt.subplots(2, 4, figsize=(12.5, 6.0), sharex=True, sharey=True)
    for i, form in enumerate(("A", "B")):
        for j, d in enumerate((0, 1, 2, 3)):
            ax = axes[i, j]
            rho = arrays["rho"][(form, d)]
            ax.contourf(lam_fine, xi_fine, (rho < 1.0).astype(float), levels=[0.5, 1.5], colors=[REGION])
            ax.contour(lam_fine, xi_fine, rho, levels=[1.0], colors=[SERIES[0]], linewidths=1.6)
            Ls, Xs, r, rh = arrays["sim"][(form, d)]
            st = rh < 1.0
            ax.scatter(Ls[st], Xs[st], s=5, c=INK2, marker="o", linewidths=0, zorder=3)
            ax.scatter(Ls[~st], Xs[~st], s=9, c=MUTED, marker="x", linewidths=0.6, zorder=3)
            bad = ((r < 1.0) != st) & (np.abs(r - 1.0) > clear)
            if bad.any():
                ax.scatter(Ls[bad], Xs[bad], s=40, facecolors="none", edgecolors=CRITICAL, linewidths=1.2, zorder=4)
            if form == "B":
                lam_c = np.linspace(0.02, 1.0, 200)
                ax.plot(lam_c, critical_gain_B_formula(d) / lam_c, color=INK, lw=0.9, ls=(0, (3, 2)), zorder=5)
            else:
                ax.axhline(2.0, color=INK, lw=0.9, ls=(0, (3, 2)), zorder=5)
            name = "(A) intercept form" if form == "A" else "(B) velocity form"
            ax.set_title(f"{name}, δ = {d}  (mismatch: {int(bad.sum())})")
            ax.set_xlim(0, 1.0)
            ax.set_ylim(0, 5.0)
            if i == 1:
                ax.set_xlabel("EWMA weight λ")
            if j == 0:
                ax.set_ylabel("gain ratio ξ = b / b̂")
    handles = [Patch(color=REGION, label="stable: spectral radius < 1"),
               Line2D([], [], color=SERIES[0], lw=1.6, label="ρ = 1 (polynomial roots)"),
               Line2D([], [], color=INK2, marker="o", lw=0, ms=3, label="closed-loop sim: decays"),
               Line2D([], [], color=MUTED, marker="x", lw=0, ms=4, label="closed-loop sim: grows"),
               Line2D([], [], color=INK, lw=0.9, ls=(0, (3, 2)), label="(A) ξ = 2 · (B) λξ = 2 sin(π/(4δ+2))")]
    fig.legend(handles=handles, loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    return _save(fig, path)


def q1_delay(arrays, path):
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    dd, kf, kn = arrays["B_curve"]
    ax = axes[0]
    ax.plot(dd, kf, color=SERIES[0], lw=1.6, label="2 sin(π/(4δ+2))")
    ax.plot(dd, kn, ls="none", marker="o", ms=4, color=INK2, label="numeric (bisection on ρ = 1)")
    ax.set_yscale("log")
    ax.set_yticks([0.05, 0.1, 0.2, 0.5, 1.0, 2.0])
    ax.set_yticklabels(["0.05", "0.1", "0.2", "0.5", "1", "2"])
    ax.minorticks_off()
    ax.set_xlabel("metrology delay δ (wafers)")
    ax.set_ylabel("largest stable λξ")
    ax.set_title("(B) velocity form: stability limit on λξ")
    ax.legend()
    ax = axes[1]
    for c, (lam, xm) in enumerate(arrays["A_curve"].items()):
        ax.plot(dd, xm, color=SERIES[c], marker=MARKERS[c], ms=3.5, lw=1.4, label=f"λ = {lam:g}")
    ax.axhline(2.0, color=INK, lw=0.9, ls=(0, (3, 2)))
    ax.annotate("ξ < 2: stable for every δ", xy=(25, 2.0), xytext=(0, -12), textcoords="offset points",
                ha="right", color=INK2, fontsize=8)
    ax.set_yscale("log")
    ax.set_ylim(1.6, 24)
    ax.set_yticks([2, 3, 4, 6, 10, 20])
    ax.set_yticklabels(["2", "3", "4", "6", "10", "20"])
    ax.minorticks_off()
    ax.set_xlabel("metrology delay δ (wafers)")
    ax.set_ylabel("largest stable ξ")
    ax.set_title("(A) intercept form: upper ξ limit")
    ax.legend(ncol=2)
    fig.tight_layout()
    return _save(fig, path)


# ── Q2 ─────────────────────────────────────────────────────────────────────
Q2_COLORS = {"EWMA-A": SERIES[0], "EWMA-B": SERIES[1], "d-EWMA": SERIES[2], "EWMA-A+PMreset": SERIES[3],
             "d-EWMA+PMreset": SERIES[4]}
Q2_MARK = {"EWMA-A": "o", "EWMA-B": "s", "d-EWMA": "^", "EWMA-A+PMreset": "D", "d-EWMA+PMreset": "v"}


def q2_pm_recovery(profiles, spec, path, before=10):
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
    groups = (("EWMA-A", "EWMA-B", "d-EWMA"), ("EWMA-A+PMreset", "d-EWMA+PMreset"))
    titles = ("no reset at PM", "controller reset at PM (re-baselined recipe)")
    for ax, fams, title in zip(axes, groups, titles):
        for fam in fams:
            prof = profiles[fam]
            x = np.arange(prof.size) - before
            ax.plot(x, prof, color=Q2_COLORS[fam], lw=1.6, marker=Q2_MARK[fam], ms=3, markevery=5, label=fam)
        for s in (spec, -spec):
            _ref(ax, s)
        ax.annotate("spec ±150 Å", xy=(1.0, spec), xycoords=("axes fraction", "data"), xytext=(-2, 2),
                    textcoords="offset points", ha="right", color=INK2, fontsize=8)
        ax.axvline(0, color=AXIS, lw=0.8)
        ax.set_xlabel("wafers since PM (pad change)")
        ax.set_title(title)
        ax.legend(loc="upper right", bbox_to_anchor=(1.0, 0.9))
    axes[0].set_ylabel("mean error y − T (Å)")
    fig.tight_layout()
    return _save(fig, path)


def q2_example(example, pm_wafers, p, path):
    fig, axes = plt.subplots(2, 1, figsize=(10, 4.8), sharex=True)
    for c, (fam, (e, u)) in enumerate(example.items()):
        col = Q2_COLORS[fam]
        x = np.arange(e.size)
        axes[0].plot(x, e, color=col, lw=0.6, alpha=0.85, label=fam)
        axes[1].plot(x, u, color=col, lw=1.0, label=fam)
    for s in (p.spec, -p.spec):
        _ref(axes[0], s)
    for b in (p.u_min, p.u_max):
        _ref(axes[1], b)
    axes[1].annotate("pre-approved recipe bounds", xy=(1.0, p.u_max), xycoords=("axes fraction", "data"),
                     xytext=(-2, -11), textcoords="offset points", ha="right", color=INK2, fontsize=8)
    for k in pm_wafers:
        for ax in axes:
            ax.axvline(k, color=AXIS, lw=0.8)
    axes[0].set_ylabel("y − T (Å)")
    axes[1].set_ylabel("polish time u (s)")
    axes[1].set_xlabel("wafer index (vertical lines = PM)")
    axes[0].set_title("one seed, main scenario (wear 2e-4/wafer, PM every 500 wafers, delay 2)")
    axes[0].legend(loc="upper left", ncol=2)
    fig.tight_layout()
    return _save(fig, path)


def q2_saturation(sat_rows, sat_profiles, p, path):
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
    ax = axes[0]
    for c, fam in enumerate(("EWMA-A", "d-EWMA")):
        rows = [r for r in sat_rows if r["family"] == fam and r["wear"] > 0 and r["runs_touch_pct"] >= 100.0]
        w = np.array([r["wear"] for r in rows]) * 1e4
        m = np.array([r["first_touch"]["mean"] for r in rows])
        lo = np.array([r["first_touch"]["lo"] for r in rows])
        hi = np.array([r["first_touch"]["hi"] for r in rows])
        off = (-0.06 if c == 0 else 0.06)
        ax.errorbar(w + off, m, yerr=[m - lo, hi - m], color=Q2_COLORS[fam], marker=Q2_MARK[fam], ms=4.5,
                    lw=0, elinewidth=1.2, capsize=0, label=f"{fam}: first wafer at bound (mean, 95% CI)")
        if c == 0:
            pred = np.array([r["pred_n_star"]["mean"] for r in rows])
            ax.plot(w, pred, color=INK, lw=0.9, ls=(0, (3, 2)), label="deterministic: (1 − T/(u_max·b_pad))/w")
    ax.set_xlabel("pad-wear rate w (×1e-4 per wafer)")
    ax.set_ylabel("wafers until first saturation")
    ax.set_title("time to hit the pre-approved bound (no PM)")
    ax.legend(fontsize=7.5)
    ax = axes[1]
    wears = [w for w in sorted(sat_profiles) if w >= 2e-4]
    for c, wv in enumerate(wears):
        prof = sat_profiles[wv]
        x = np.arange(prof.size) - 50
        ax.plot(x, prof, color=ORDINAL[min(c, len(ORDINAL) - 1)], lw=1.5, label=f"w = {wv * 1e4:g}e-4")
    _ref(ax, -p.spec)
    ax.annotate("lower spec −150 Å", xy=(0.02, -p.spec), xycoords=("axes fraction", "data"), xytext=(0, 3),
                textcoords="offset points", ha="left", color=INK2, fontsize=8)
    ax.axvline(0, color=AXIS, lw=0.8)
    ax.set_xlabel("wafers since sustained saturation (5 in a row at bound)")
    ax.set_ylabel("mean error y − T (Å), EWMA-A")
    ax.set_title("quality loss once pinned at u_max")
    ax.legend(ncol=2, loc="lower left")
    fig.tight_layout()
    return _save(fig, path)


# ── Q3 ─────────────────────────────────────────────────────────────────────
Q3_COLORS = {"meas": SERIES[0], "meas_boost": SERIES[1], "vm_all": SERIES[2], "vm_t2": SERIES[3],
             "vm_t2r": SERIES[4], "vm_refresh": SERIES[5]}
Q3_MARK = dict(zip(Q3_COLORS, MARKERS))
Q3_LABEL = {"meas": "(i) measured only", "meas_boost": "(i') measured + post-PM full sampling",
            "vm_all": "(ii) VM on all unmeasured", "vm_t2": "(iii) VM gated by T²",
            "vm_t2r": "(iv) T² + post-PM residual check", "vm_refresh": "(v) post-PM full sampling + VM retrain"}


def q3_ratio(rows, path, tol):
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.0), sharey=True)
    for ax, sc in zip(axes, ("normal", "offset", "silent")):
        base = [r for r in rows if r["scenario"] == sc and r["m"] == 1][0]
        for strat in Q3_COLORS:
            rs = sorted([r for r in rows if r["scenario"] == sc and r["strategy"] == strat and r["m"] > 1],
                        key=lambda r: r["m"])
            x = [base["meas_frac"]] + [r["meas_frac"] for r in rs]
            y = [1.0] + [r["ratio_vs_full"]["mean"] for r in rs]
            ax.plot(x, y, color=Q3_COLORS[strat], marker=Q3_MARK[strat], ms=4.5, lw=1.4, label=Q3_LABEL[strat])
        ax.axhline(tol, color=INK, lw=0.9, ls=(0, (3, 2)))
        ax.annotate(f"equal-quality limit (MSE ×{tol:.2f})", xy=(0.02, tol), xycoords=("axes fraction", "data"),
                    xytext=(0, 3), textcoords="offset points", ha="left", color=INK2, fontsize=8)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("fraction of wafers measured")
        title = {"normal": "VM relation stable across PM", "offset": "PM shifts motor baselines (T²-visible)",
                 "silent": "PM shifts RR at same friction (T²-blind)"}[sc]
        ax.set_title(title)
        ax.invert_xaxis()
    axes[0].set_ylabel("MSE / MSE(full metrology)")
    yt = [1.0, 1.1, 1.5, 2.0, 3.0, 4.0]
    axes[0].set_yticks(yt)
    axes[0].set_yticklabels([f"{v:g}" for v in yt])
    axes[0].minorticks_off()
    for ax in axes:
        ax.set_xticks([1.0, 0.5, 0.2, 0.1, 0.04])
        ax.set_xticklabels(["1", "1/2", "1/5", "1/10", "1/25"])
        ax.minorticks_off()
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    return _save(fig, path)


def q3_profiles(profiles, path, m, before=10):
    strats = ("meas", "vm_all", "vm_t2", "vm_t2r", "vm_refresh")
    fig, axes = plt.subplots(2, 3, figsize=(12, 5.2), sharex=True, sharey="row",
                             gridspec_kw={"height_ratios": [1.6, 1.0]})
    for j, sc in enumerate(("normal", "offset", "silent")):
        for strat in strats:
            prof, use = profiles[(sc, strat)]
            x = np.arange(prof.size) - before
            axes[0, j].plot(x, prof, color=Q3_COLORS[strat], lw=1.4, marker=Q3_MARK[strat], ms=3, markevery=10,
                            label=Q3_LABEL[strat])
            if use is not None:
                axes[1, j].plot(x, use, color=Q3_COLORS[strat], lw=1.4, marker=Q3_MARK[strat], ms=3, markevery=10)
        for ax in axes[:, j]:
            ax.axvline(0, color=AXIS, lw=0.8)
        axes[0, j].axhline(0, color=AXIS, lw=0.8)
        title = {"normal": "stable relation", "offset": "motor-baseline shift (T²-visible)",
                 "silent": "RR shift at same friction (T²-blind)"}[sc]
        axes[0, j].set_title(f"{title}, m = {m}")
        axes[1, j].set_xlabel("wafers since PM")
    axes[0, 0].set_ylabel("mean error, signed toward\nstale-VM push (Å)")
    axes[1, 0].set_ylabel("share of unmeasured\nwafers fed by VM")
    h, l = axes[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.05))
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    return _save(fig, path, dpi=95)
