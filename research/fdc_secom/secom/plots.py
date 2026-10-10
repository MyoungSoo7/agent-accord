"""Figures (matplotlib Agg, static PNG for the Markdown report; English labels
because the default font has no Hangul glyphs).

Colour follows the entity: each model keeps one categorical slot everywhere
(reference palette, slots 1-5 in fixed order, validated for adjacent-pair CVD
separation). Fail markers use the reserved 'critical' status colour together
with a distinct marker shape and a legend label.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
CRITICAL = "#d03b3b"
SEQ_DARK = "#1c5cab"
SEQ_LIGHT = "#cde2fb"

MODEL_COLORS = {
    "mspc": "#2a78d6",
    "iforest": "#eb6834",
    "l1lr": "#1baf7a",
    "rf": "#eda100",
    "hgb": "#e87ba4",
}
MODEL_MARKERS = {"mspc": "o", "iforest": "s", "l1lr": "D", "rf": "^", "hgb": "v"}
MODEL_SHORT = {
    "mspc": "MSPC",
    "iforest": "Isolation Forest",
    "l1lr": "L1 logistic",
    "rf": "Random forest",
    "hgb": "HistGB",
    "time_knn": "time only",
}
CHRONO = "#2a78d6"
RANDOM = "#eb6834"
DPI = 110


def _style():
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": AXIS,
            "axes.labelcolor": INK2,
            "axes.titlecolor": INK,
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "axes.labelsize": 9,
            "axes.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "grid.linestyle": "-",
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelcolor": INK2,
            "ytick.labelcolor": INK2,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "legend.frameon": False,
            "font.size": 9,
            "lines.linewidth": 2.0,
            "lines.solid_capstyle": "round",
            "lines.solid_joinstyle": "round",
        }
    )


def _save(fig, path):
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------- Q1
def missingness(path, feature_missing_frac, X, ts):
    _style()
    fig, (a, b) = plt.subplots(1, 2, figsize=(10, 3.4), gridspec_kw={"width_ratios": [1, 1.5]})
    mf = np.sort(feature_missing_frac)[::-1]
    a.plot(np.arange(1, mf.size + 1), mf, color=SEQ_DARK, lw=2)
    a.axhline(0.5, color=MUTED, lw=1, ls="--")
    a.text(mf.size * 0.98, 0.52, "drop threshold 0.5 (train-fitted)", ha="right", va="bottom", color=INK2, fontsize=8)
    a.set_xscale("log")
    a.set_xlabel("feature rank (log)")
    a.set_ylabel("missing fraction")
    a.set_title("Missing fraction per feature (590)")
    a.set_ylim(0, 1)

    high = np.flatnonzero(feature_missing_frac > 0.05)
    high = high[np.argsort(-feature_missing_frac[high])]
    M = np.isnan(X[:, high]).T.astype(float)
    b.imshow(
        M,
        aspect="auto",
        interpolation="nearest",
        cmap=ListedColormap([SURFACE, SEQ_DARK]),
        vmin=0,
        vmax=1,
    )
    b.grid(False)
    n = X.shape[0]
    days = ts.astype("datetime64[D]")
    ticks = np.linspace(0, n - 1, 5).astype(int)
    b.set_xticks(ticks, [str(days[i])[5:] for i in ticks])
    b.set_xlabel("entity (time order, 2008 month-day)")
    b.set_ylabel(f"{high.size} features with >5% missing")
    b.set_yticks([])
    b.set_title("Where values are missing (dark = missing)")
    _save(fig, path)


def fail_time(path, weekly, clustering, ts, block_starts, holdout_start):
    _style()
    fig, (a, b) = plt.subplots(1, 2, figsize=(10.5, 3.5), gridspec_kw={"width_ratios": [1.6, 1]})
    starts = np.array([np.datetime64(w["start"]) for w in weekly])
    rate = np.array([w["rate"] for w in weekly])
    lo = np.array([w["ci"][0] for w in weekly])
    hi = np.array([w["ci"][1] for w in weekly])
    centers = starts + np.timedelta64(3, "D") + np.timedelta64(12, "h")
    a.bar(centers, rate, width=5.0, color=SEQ_DARK, zorder=2)
    a.errorbar(centers, rate, yerr=[rate - lo, hi - rate], fmt="none", ecolor=MUTED, elinewidth=1, capsize=0, zorder=3)
    overall = sum(w["fails"] for w in weekly) / sum(w["n"] for w in weekly)
    a.axhline(overall, color=INK2, lw=1, label=f"overall {overall:.1%}")
    edges = [ts[s] for s in block_starts] + [ts[-1]]
    for i, s in enumerate(block_starts):
        a.axvline(ts[s], color=AXIS, lw=1, zorder=1)
        mid = edges[i] + (edges[i + 1] - edges[i]) / 2
        a.text(mid, 0.445, f"B{i + 1}", ha="center", va="top", color=INK2, fontsize=8)
    a.axvline(ts[holdout_start], color=INK2, lw=1, ls="--", zorder=1, label="70/30 cut")
    a.set_ylim(0, 0.45)
    a.set_ylabel("fail rate (Wilson 95% CI)")
    a.set_title("Weekly fail rate; B1-B6 = expanding-window blocks")
    a.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%m-%d"))
    a.legend(loc="upper right", bbox_to_anchor=(1.0, 0.93))

    lags = np.arange(1, len(clustering["acf"]) + 1)
    b.fill_between(lags, clustering["acf_band_lo"], clustering["acf_band_hi"], color=GRID, step="mid", label="95% permutation band")
    b.bar(lags, clustering["acf"], width=0.5, color=SEQ_DARK, zorder=2, label="observed")
    b.axhline(0, color=AXIS, lw=0.8)
    b.set_xlabel("lag (entities)")
    b.set_ylabel("autocorrelation")
    b.set_title("Autocorrelation of the fail indicator")
    b.legend(loc="upper right")
    _save(fig, path)


# --------------------------------------------------------------------- Q2
def eval_honesty(path, summaries, keys, prevalence_lines):
    _style()
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.9), sharey=True)
    schemes = [
        ("random_kfold", "random stratified 5-fold (x2)", RANDOM, "o", True),
        ("random_holdout", "random 70/30 (x5)", RANDOM, "o", False),
        ("chrono_holdout", "chronological 70/30", CHRONO, "s", False),
        ("expanding", "expanding window (5 folds)", CHRONO, "s", True),
    ]
    offsets = np.linspace(0.27, -0.27, len(schemes))
    ypos = np.arange(len(keys))[::-1]
    for ax, metric, title in zip(axes, ("roc_auc", "pr_auc"), ("ROC-AUC (fold mean, 95% CI)", "PR-AUC (fold mean, 95% CI)")):
        for (sch, label, color, marker, filled), off in zip(schemes, offsets):
            for y0, key in zip(ypos, keys):
                s = summaries[sch][key]
                m = s["mean"][metric]
                lo, hi = s["mean_ci"][metric]
                ax.plot([lo, hi], [y0 + off] * 2, color=color, lw=1.5, alpha=0.9)
                ax.plot(
                    m,
                    y0 + off,
                    marker=marker,
                    ms=6,
                    color=color,
                    mfc=color if filled else SURFACE,
                    mec=color,
                    mew=1.5,
                    ls="none",
                    label=label if key == keys[0] else None,
                )
        if metric == "roc_auc":
            ax.axvline(0.5, color=INK2, lw=1)
            ax.text(0.5, ypos[0] + 0.55, " chance", color=INK2, fontsize=8, va="bottom")
        else:
            for sch, val in prevalence_lines.items():
                ax.axvline(val, color=CHRONO if sch == "chrono" else RANDOM, lw=1, ls="--")
            ax.text(prevalence_lines["chrono"], ypos[0] + 0.55, " random-ranking AP", color=INK2, fontsize=8, va="bottom")
        ax.set_title(title)
        ax.set_ylim(-0.6, len(keys) - 0.2)
    axes[0].set_yticks(ypos, [MODEL_SHORT[k] for k in keys])
    axes[0].grid(axis="y", visible=False)
    axes[1].grid(axis="y", visible=False)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    _save(fig, path)


def folds_auc(path, summary_expanding, keys, fold_info):
    _style()
    fig, axes = plt.subplots(1, len(keys), figsize=(11, 2.9), sharey=True)
    x = np.arange(1, len(fold_info) + 1)
    xt = [f"{i}\n({fi['test_fails']})" for i, fi in zip(x, fold_info)]
    for ax, key in zip(axes, keys):
        pf = summary_expanding[key]["per_fold"]
        m = np.array([f["roc_auc"] for f in pf])
        lo = np.array([f["ci"]["roc_auc"][0] for f in pf])
        hi = np.array([f["ci"]["roc_auc"][1] for f in pf])
        ax.vlines(x, lo, hi, color=MODEL_COLORS[key], lw=1.5)
        ax.plot(x, m, marker=MODEL_MARKERS[key], ms=6, color=MODEL_COLORS[key], ls="none", mec=SURFACE, mew=1)
        ax.axhline(0.5, color=INK2, lw=1)
        ax.set_title(MODEL_SHORT[key])
        ax.set_xticks(x, xt)
        ax.set_ylim(0.2, 1.0)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("ROC-AUC (95% CI)")
    fig.supxlabel("expanding-window fold (test fails)", fontsize=9, color=INK2)
    fig.tight_layout()
    _save(fig, path)


# --------------------------------------------------------------------- Q3
def roc_pr(path, y, scores, keys):
    _style()
    fig, (a, b) = plt.subplots(1, 2, figsize=(9.5, 3.8))
    y = np.asarray(y).astype(bool)
    for key in keys:
        s = scores[key]
        order = np.argsort(-s, kind="mergesort")
        ss = s[order]
        ends = np.r_[np.flatnonzero(np.diff(ss) != 0), len(ss) - 1]
        tp = np.cumsum(y[order])[ends]
        fp = np.cumsum(~y[order])[ends]
        tpr = np.r_[0, tp / y.sum()]
        fpr = np.r_[0, fp / (~y).sum()]
        prec = tp / (tp + fp)
        a.plot(fpr, tpr, color=MODEL_COLORS[key], lw=1.8, label=MODEL_SHORT[key], drawstyle="steps-post")
        b.plot(tp / y.sum(), prec, color=MODEL_COLORS[key], lw=1.8, label=MODEL_SHORT[key], drawstyle="steps-post")
    a.plot([0, 1], [0, 1], color=INK2, lw=1)
    for t in (0.05, 0.10):
        a.axvline(t, color=AXIS, lw=1, ls="--")
    a.set_xlabel("false alarm rate (FPR)")
    a.set_ylabel("recall (TPR)")
    a.set_title("ROC, chronological 70/30 test")
    b.axhline(y.mean(), color=INK2, lw=1, label=f"prevalence {y.mean():.3f}")
    b.legend(handles=[b.get_lines()[-1]], loc="upper right")
    b.set_xlabel("recall")
    b.set_ylabel("precision")
    b.set_title("Precision-recall, same test")
    b.set_ylim(0, 0.35)
    a.legend(loc="lower right")
    _save(fig, path)


def mspc_chart(path, fold, ts):
    _style()
    te = fold["test_idx"]
    y = fold["y_test"].astype(bool)
    info = fold["info"]["mspc"]
    t = ts[te]
    fig, axes = plt.subplots(2, 1, figsize=(10, 4.6), sharex=True)
    for ax, key, lim, name in (
        (axes[0], "mspc_t2", info["t2_limit_99"], "Hotelling T²"),
        (axes[1], "mspc_spe", info["spe_limit_99"], "SPE (Q)"),
    ):
        v = fold["scores"][key]
        top = max(lim * 30.0, float(np.percentile(v, 99)) * 2.0)
        clip = v > top
        vv = np.minimum(v, top)
        ax.scatter(t[~y & ~clip], vv[~y & ~clip], s=8, color=MUTED, alpha=0.6, lw=0, label="pass")
        ax.scatter(t[y], vv[y], s=36, color=CRITICAL, marker="^", lw=0.6, edgecolors=SURFACE, label="fail", zorder=3)
        if (clip & ~y).any():
            ax.scatter(
                t[clip & ~y],
                vv[clip & ~y],
                s=30,
                marker="$\\uparrow$",
                color=INK2,
                lw=0,
                label=f"pass beyond axis ({int((clip & ~y).sum())})",
                zorder=3,
            )
        ax.axhline(lim, color=INK, lw=1, ls="--")
        far = np.mean(v[~y] > lim)
        ax.text(t[0], lim * 1.15, f"99% limit (test passes above: {far:.0%})", ha="left", va="bottom", color=INK, fontsize=8)
        ax.set_yscale("log")
        ax.set_ylim(max(float(np.min(vv[vv > 0])) / 1.5, 1e-6), top * 1.6)
        ax.set_ylabel(name)
    axes[0].set_title(
        f"MSPC on the chronological test period (k = {info['n_components']} PCs, fitted on {info['n_noc']} pass entities)"
    )
    axes[0].legend(loc="upper right", ncol=3)
    axes[1].xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%m-%d"))
    fig.tight_layout()
    _save(fig, path)


# --------------------------------------------------------------------- Q5
def alarm_budget(path, budget, keys):
    _style()
    fig, ax = plt.subplots(figsize=(7.2, 3.9))
    bs = [int(b) for b in budget["budgets"]]
    x = np.arange(len(bs))
    nf = budget["n_fail"]
    rnd = np.array([budget["budgets"][str(b)]["random"]["expected"] for b in bs]) / nf
    lo = np.array([budget["budgets"][str(b)]["random"]["lo"] for b in bs]) / nf
    hi = np.array([budget["budgets"][str(b)]["random"]["hi"] for b in bs]) / nf
    ax.fill_between(x, lo, hi, color=GRID, label="random inspection 95% range")
    ax.plot(x, rnd, color=INK2, lw=1.5, label="random inspection (expected)")
    for key in keys:
        r = [budget["budgets"][str(b)]["models"][key]["recall"] for b in bs]
        ax.plot(x, r, color=MODEL_COLORS[key], marker=MODEL_MARKERS[key], ms=6, mec=SURFACE, mew=1, label=MODEL_SHORT[key])
    ax.set_xticks(x, [str(b) for b in bs])
    ax.set_xlabel("alarm budget (inspections per 100 consecutive entities)")
    ax.set_ylabel(f"share of the {nf} fails caught")
    ax.set_ylim(0, 0.5)
    ax.set_title("Fixed alarm budget on chronological test blocks B2-B6")
    ax.legend(loc="upper left", ncol=2)
    _save(fig, path)


# --------------------------------------------------------------------- Q4
def stability(path, stab, keys, halves):
    _style()
    fig, (a, b) = plt.subplots(1, 2, figsize=(10.5, 3.7), gridspec_kw={"width_ratios": [1, 1.1]})
    y0 = np.arange(len(keys))[::-1]
    for yy, key in zip(y0, keys):
        st = stab[key]["top_k"]["10"]
        a.plot(st["jaccard"]["mean"], yy + 0.12, marker=MODEL_MARKERS[key], ms=7, color=MODEL_COLORS[key], ls="none", mec=SURFACE)
        a.plot(st["cluster_jaccard"]["mean"], yy - 0.12, marker=MODEL_MARKERS[key], ms=7, mfc=SURFACE, mec=MODEL_COLORS[key], mew=1.5, ls="none")
        a.plot([st["jaccard"]["min"], st["jaccard"]["max"]], [yy + 0.12] * 2, color=MODEL_COLORS[key], lw=1.2)
    nul = stab[keys[0]]["top_k"]["10"]["null"]
    a.axvspan(0, nul["p95"], color=GRID, zorder=0)
    a.text(nul["p95"], y0[-1] - 0.45, " random top-10 (95th pct)", color=INK2, fontsize=8, va="center")
    a.set_yticks(y0, [MODEL_SHORT[k] for k in keys])
    a.grid(axis="y", visible=False)
    a.set_xlim(0, 0.5)
    a.set_ylim(-0.7, len(keys) - 0.4)
    a.set_xlabel("pairwise Jaccard of top-10 across 5 folds (filled: features, hollow: corr. clusters)")
    a.set_title("Top-10 permutation-importance overlap across folds")

    a1, a2 = halves["_auc_first"], halves["_auc_second"]
    ok = np.isfinite(a1) & np.isfinite(a2)
    b.scatter(a1[ok], a2[ok], s=9, color=SEQ_DARK, alpha=0.55, lw=0)
    b.axhline(0.5, color=AXIS, lw=1)
    b.axvline(0.5, color=AXIS, lw=1)
    b.plot([0.2, 0.8], [0.2, 0.8], color=INK2, lw=1)
    b.set_xlim(0.2, 0.8)
    b.set_ylim(0.2, 0.8)
    b.set_xlabel("univariate AUC, first half of time")
    b.set_ylabel("univariate AUC, second half")
    b.set_title(f"Per-feature AUC, first vs second half (Spearman {halves['spearman_rho']:.2f})")
    _save(fig, path)
