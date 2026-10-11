"""README charts (light and dark SVG) for the held-out FiveK test split.

    python -m bench.experiments.test_split_charts [REVIEW_DIR]
Needs results/per_image/test/*.csv (bench.run --split test). Writes docs/img/fivek_test_*.svg, and PNGs to
REVIEW_DIR if given. Only linear sources are plotted, as in the report's gate table.
"""
import glob
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .readme_charts import MARKERS, OUT, ROOT, THEMES, style  # noqa: E402

STEPS = (0.5, 1.0, 1.5, 2.0)
B5_BITS = (11, 10, 9, 8, 7)   # every linear image has these; 12-bit only exists for 14-bit sources


def load():
    df = pd.concat([pd.read_csv(f) for f in glob.glob(str(ROOT / "results" / "per_image" / "test" / "*.csv"))])
    df = df[df["lossy_source"].astype(str) == "False"].copy()
    df["hot"] = df.L0_bytes + df.L1_bytes + df.L2_bytes
    df["total"] = df.hot + df.L3_bytes
    return df


def title(df):
    return f"{len(df)} linear images from {df['camera'].nunique()} cameras"


def size_chart(df, t, path):
    rows = [  # top to bottom
        ("JPEG XL lossless", df.B2_bytes / df.B1_bytes, False),
        ("MiniRaw, all layers (bit-exact)", df.total / df.B1_bytes, True),
        ("MiniRaw hot tier, step 1.0", df.hot / df.B1_bytes, True),
    ]
    fig, ax = plt.subplots(figsize=(8.6, 3.5), dpi=100)
    fig.patch.set_facecolor(t["surface"])
    style(ax, t)
    ax.grid(axis="y", visible=False)
    rng = np.random.default_rng(0)
    ys = [0, 1, 2.6]
    for y, (name, v, ours) in zip(ys, rows):
        color = t["series"][0] if ours else t["muted"]
        ax.scatter(v, y + rng.uniform(-0.2, 0.2, len(v)), s=34, color=color, edgecolors=t["surface"],
                   linewidths=1, zorder=3)
        m = float(np.median(v))
        ax.plot([m, m], [y - 0.36, y + 0.36], color=t["ink"], linewidth=2, solid_capstyle="round", zorder=4)
        ax.annotate(f"median {m:.2f}×", (m, y - 0.36), xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", fontsize=8.5, color=t["ink"] if ours else t["ink2"],
                    fontweight="bold" if ours else "normal")
    ax.set_yticks(ys, [r[0] for r in rows])
    for lbl, r in zip(ax.get_yticklabels(), rows):
        lbl.set_color(t["ink"] if r[2] else t["ink2"])
        lbl.set_fontweight("bold" if r[2] else "normal")
    ax.axvline(1.0, color=t["muted"], linewidth=1, linestyle=(0, (4, 3)), zorder=1)
    ax.text(1.0, 3.35, " camera's raw file", ha="left", va="center", fontsize=8, color=t["muted"])
    ax.axhline(1.8, color=t["axis"], linewidth=0.8, linestyle=(0, (3, 3)))
    ax.text(0.01, -0.62, "Lossless: bit-exact", ha="left", va="center", fontsize=9, color=t["muted"])
    ax.text(0.01, 2.0, "Near-lossless", ha="left", va="center", fontsize=9, color=t["muted"])
    ax.set_ylim(3.5, -0.85)
    ax.set_xlim(0, 1.2)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}×"))
    ax.set_xlabel("Size relative to the camera's raw file (one dot per image)", color=t["ink2"], fontsize=9)
    fig.suptitle(f"Held-out FiveK test: size per image, {title(df)}", x=0.02, ha="left", color=t["ink"],
                 fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, facecolor=t["surface"])
    plt.close(fig)


def quality_chart(df, t, path):
    bpp = lambda b: float(np.median(8 * b / df.npx))  # noqa: E731
    col = lambda st, m: df[m] if st == 1.0 else df[f"step{st}_{m}"]  # noqa: E731
    series = [
        ("MiniRaw (noise-calibrated)",
         [(bpp(df.hot if st == 1.0 else df[f"step{st}_L0L1L2_bytes"]), float(np.median(col(st, "stab_maxerr"))),
           float(np.median(col(st, "stab_rmse"))), st) for st in STEPS]),
        ("Fixed square-root curve\n(in-camera lossy raw style)",
         [(bpp(df[f"B5_{b}_bytes"]), float(np.median(df[f"B5_{b}_stab_maxerr"])),
           float(np.median(df[f"B5_{b}_stab_rmse"])), b) for b in B5_BITS]),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.2), dpi=100, sharex=True)
    fig.patch.set_facecolor(t["surface"])
    for ax, idx, sub in ((axes[0], 1, "Worst-case error (any pixel)"), (axes[1], 2, "Average (RMS) error")):
        style(ax, t)
        ax.set_yscale("log")
        ax.axhline(1.0, color=t["muted"], linewidth=1, linestyle=(0, (4, 3)), zorder=1)
        ax.text(0.99, 1.0, "sensor noise level ", transform=ax.get_yaxis_transform(), ha="right", va="bottom",
                fontsize=8, color=t["muted"])
        for i, (name, pts) in enumerate(series):
            pts = sorted(pts)
            ax.plot([p[0] for p in pts], [p[idx] for p in pts], color=t["series"][i], linewidth=2,
                    marker=MARKERS[i], markersize=7, markeredgecolor=t["surface"], markeredgewidth=1.5,
                    label=name, zorder=3)
        ax.set_title(sub, color=t["ink"], fontsize=10, loc="left")
        ax.set_xlabel("Median bits per pixel", color=t["ink2"], fontsize=9)
        ax.yaxis.set_major_locator(matplotlib.ticker.LogLocator(subs=(1, 2, 5)))
        ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}σ"))
    axes[0].set_ylabel("Error, in units of the sensor's own noise", color=t["ink2"], fontsize=9)
    # Direct labels on the left panel: the default step, and each series at one end.
    d = next(p for p in series[0][1] if p[3] == 1.0)
    axes[0].annotate(f"default, step 1.0\n{d[0]:.2f} bpp, worst {d[1]:.2f}σ", (d[0], d[1]), xytext=(-12, -40),
                     textcoords="offset points", fontsize=8, color=t["ink"], ha="right",
                     arrowprops=dict(arrowstyle="-", color=t["muted"], linewidth=0.8))
    lo = min(series[0][1])
    axes[0].annotate("MiniRaw", (lo[0], lo[1]), xytext=(-8, 8), textcoords="offset points", fontsize=8.5,
                     color=t["ink"], ha="right", fontweight="bold")
    top = min(series[1][1])
    axes[0].annotate("Square-root curve", (top[0], top[1]), xytext=(10, 0), textcoords="offset points",
                     fontsize=8.5, color=t["ink"], ha="left", va="center")
    leg = axes[1].legend(loc="lower left", fontsize=8, frameon=False, labelcolor=t["ink2"])
    for line in leg.get_lines():
        line.set_linewidth(2)
    fig.suptitle(f"Held-out FiveK test: size versus error, median of {title(df)} (lower-left is better)",
                 x=0.02, ha="left", color=t["ink"], fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, facecolor=t["surface"])
    plt.close(fig)


def main(review=None):
    df = load()
    OUT.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": ["Helvetica", "Arial", "DejaVu Sans"], "svg.fonttype": "path"})
    for mode, t in THEMES.items():
        for ext in ("svg",) + (("png",) if review else ()):
            dst = Path(review) if ext == "png" else OUT
            size_chart(df, t, dst / f"fivek_test_size_{mode}.{ext}")
            quality_chart(df, t, dst / f"fivek_test_quality_{mode}.{ext}")
    print("wrote", sorted(p.name for p in OUT.glob("fivek_test_*.svg")))


if __name__ == "__main__":
    main(*sys.argv[1:2])
