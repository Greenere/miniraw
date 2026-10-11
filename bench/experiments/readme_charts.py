"""README charts (light and dark SVG) from measured results on a folder of raw files.

    python -m bench.experiments.readme_charts personal_raws
Needs results/per_image/<set>/*.csv (bench.run) and results/experiments/readme_<set>.csv
(bench.experiments.readme_chart_data). Writes docs/img/*.svg.
"""
import glob
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "img"

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781", grid="#e1e0d9",
                  axis="#c3c2b7", neutral="#c3c2b7", series=["#2a78d6", "#eb6834", "#1baf7a"]),
    "dark": dict(surface="#1a1a19", ink="#f0efec", ink2="#c3c2b7", muted="#898781", grid="#2c2c2a",
                 axis="#383835", neutral="#52514e", series=["#3987e5", "#d95926", "#199e70"]),
}
MARKERS = ["o", "s", "^"]


def load(name):
    df = pd.concat([pd.read_csv(f) for f in glob.glob(str(ROOT / "results" / "per_image" / name / "*.csv"))])
    extra = pd.read_csv(ROOT / "results" / "experiments" / f"readme_{name}.csv")
    return df.merge(extra, on=["key", "npx"])


def style(ax, t):
    ax.set_facecolor(t["surface"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(t["axis"])
    ax.tick_params(colors=t["muted"], labelcolor=t["ink2"], labelsize=9)
    ax.grid(color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)


def size_chart(df, t, path, camera):
    mb = lambda col: float(np.median(df[col])) / 1e6  # noqa: E731
    hot = lambda st: float(np.median(df["L0_bytes"] + df["L1_bytes"] + df["L2_bytes"]  # noqa: E731
                                     if st == 1.0 else df[f"step{st}_L0L1L2_bytes"])) / 1e6
    unc = float(np.median(df["npx"] * df["bit_depth"] / 8)) / 1e6
    arw = mb("file_bytes")
    lossless = [
        ("Uncompressed (14-bit)", unc, False),
        ("ZIP / Deflate", mb("zip_bytes"), False),
        ("Sony ARW, lossless compressed", arw, False),
        ("JPEG-LS lossless", mb("jpegls_bytes"), False),
        ("JPEG XL lossless", mb("B2_bytes"), False),
        ("MiniRaw, all layers (bit-exact)", float(np.median(df["L0_bytes"] + df["L1_bytes"] + df["L2_bytes"]
                                                           + df["L3_bytes"])) / 1e6, True),
    ]
    near = [
        ("MiniRaw hot tier, step 1.0", hot(1.0), True),
        ("MiniRaw hot tier, step 1.5", hot(1.5), True),
    ]
    rows = lossless + [None] + near
    fig, ax = plt.subplots(figsize=(8.6, 4.6), dpi=100)
    fig.patch.set_facecolor(t["surface"])
    style(ax, t)
    ax.grid(axis="y", visible=False)
    ys, labels = [], []
    y = 0
    for r in rows:
        if r is None:
            y += 0.6
            continue
        name, v, ours = r
        ax.barh(y, v, height=0.62, color=t["series"][0] if ours else t["neutral"], zorder=2)
        pct = (v / arw - 1) * 100
        note = "" if name.startswith("Sony") else f"  ({pct:+.0f}% vs ARW)"
        ax.text(v + unc * 0.01, y, f"{v:.1f} MB{note}", va="center", ha="left", fontsize=9,
                color=t["ink"] if ours else t["ink2"], fontweight="bold" if ours else "normal")
        ys.append(y)
        labels.append(name)
        y += 1
    ax.set_yticks(ys, labels)
    for lbl, r in zip(ax.get_yticklabels(), [r for r in rows if r]):
        lbl.set_color(t["ink"] if r[2] else t["ink2"])
        lbl.set_fontweight("bold" if r[2] else "normal")
    ax.set_ylim(y - 0.4, -1.25)
    ax.set_xlim(0, unc * 1.32)
    ax.set_xlabel("Median file size per photo (MB)", color=t["ink2"], fontsize=9)
    sep = len(lossless) - 0.2
    ax.axhline(sep, color=t["axis"], linewidth=0.8, linestyle=(0, (3, 3)))
    ax.text(unc * 1.31, -0.85, "Lossless: bit-exact", ha="right", va="center", fontsize=9, color=t["muted"])
    ax.text(unc * 1.31, sep + 0.35, "Near-lossless: error within the sensor noise", ha="right", va="center",
            fontsize=9, color=t["muted"])
    fig.suptitle(f"Size per photo: {camera}, {df['npx'].median() / 1e6:.0f} MP, median of {len(df)} files",
                 x=0.02, ha="left", color=t["ink"], fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, facecolor=t["surface"])
    plt.close(fig)


def quality_chart(df, t, path, camera):
    hot = lambda st: (df["L0_bytes"] + df["L1_bytes"] + df["L2_bytes"]) if st == 1.0 \
        else df[f"step{st}_L0L1L2_bytes"]  # noqa: E731
    pre = lambda st, m: df[m] if st == 1.0 else df[f"step{st}_{m}"]  # noqa: E731
    series = [
        ("MiniRaw (noise-calibrated)", [(np.median(hot(s)), np.median(pre(s, "stab_maxerr")),
                                         np.median(pre(s, "stab_rmse"))) for s in (0.5, 1.0, 1.5, 2.0)]),
        ("Fixed square-root curve\n(in-camera lossy raw style)",
         [(np.median(df[f"B5_{b}_bytes"]), np.median(df[f"B5_{b}_stab_maxerr"]), np.median(df[f"B5_{b}_stab_rmse"]))
          for b in (12, 11, 10, 9, 8) if f"B5_{b}_bytes" in df]),
        ("JPEG XL lossy\n(lossy DNG 1.7 style)",
         [(np.median(df[f"B4_{d}_bytes"]), np.median(df[f"B4_{d}_stab_maxerr"]), np.median(df[f"B4_{d}_stab_rmse"]))
          for d in (0.05, 0.1, 0.2, 0.3, 0.5, 1.0)]),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.2), dpi=100, sharex=True)
    fig.patch.set_facecolor(t["surface"])
    for ax, idx, title in ((axes[0], 1, "Worst-case error (any pixel)"), (axes[1], 2, "Average (RMS) error")):
        style(ax, t)
        ax.set_yscale("log")
        ax.axhline(1.0, color=t["muted"], linewidth=1, linestyle=(0, (4, 3)), zorder=1)
        ax.text(0.99, 1.0, "sensor noise level ", transform=ax.get_yaxis_transform(), ha="right", va="bottom",
                fontsize=8, color=t["muted"])
        for i, (name, pts) in enumerate(series):
            pts = sorted(pts)
            x = [p[0] / 1e6 for p in pts]
            y = [p[idx] for p in pts]
            ax.plot(x, y, color=t["series"][i], linewidth=2, marker=MARKERS[i], markersize=7,
                    markeredgecolor=t["surface"], markeredgewidth=1.5, label=name, zorder=3)
        ax.set_title(title, color=t["ink"], fontsize=10, loc="left")
        ax.set_xlabel("Median size per photo (MB)", color=t["ink2"], fontsize=9)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}σ"))
    axes[0].set_ylabel("Error, in units of the sensor's own noise", color=t["ink2"], fontsize=9)
    # Direct labels on the left panel, placed off the lines: MiniRaw left of its smallest file,
    # the square-root curve above its largest-file point, JPEG XL to the right of its last point.
    anchors = [(0, min, (-10, -14), "right"), (1, max, (0, 10), "right"), (2, max, (8, -3), "left")]
    for i, pick, off, ha in anchors:
        name, pts = series[i]
        p = pick(pts)
        axes[0].annotate(name.split("\n")[0], (p[0] / 1e6, p[1]), xytext=off, textcoords="offset points",
                         fontsize=8.5, color=t["ink"], ha=ha, fontweight="bold" if i == 0 else "normal")
    leg = axes[1].legend(loc="lower left", fontsize=8, frameon=False,
                         labelcolor=t["ink2"])
    for line in leg.get_lines():
        line.set_linewidth(2)
    fig.suptitle(f"Size versus error: {camera}, median of {len(df)} files (lower-left is better)",
                 x=0.02, ha="left", color=t["ink"], fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, facecolor=t["surface"])
    plt.close(fig)


def main(name, review=None):
    df = load(name)
    camera = df["camera"].mode()[0].replace("SONY ILCE-7M4", "Sony A7 IV")
    OUT.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": ["Helvetica", "Arial", "DejaVu Sans"], "svg.fonttype": "path"})
    for mode, t in THEMES.items():
        for ext in ("svg",) + (("png",) if review else ()):
            dst = (Path(review) if ext == "png" else OUT)
            size_chart(df, t, dst / f"size_{mode}.{ext}", camera)
            quality_chart(df, t, dst / f"quality_{mode}.{ext}", camera)
    print("wrote", sorted(p.name for p in OUT.glob("*.svg")))


if __name__ == "__main__":
    main(*sys.argv[1:3])
