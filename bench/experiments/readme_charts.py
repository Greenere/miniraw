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


def load(name, extra_name=None):
    """Per-image CSVs of `name` merged with the format-independent baselines of readme_<extra_name>.csv."""
    df = pd.concat([pd.read_csv(f) for f in glob.glob(str(ROOT / "results" / "per_image" / name / "*.csv"))])
    df["IDX_bytes"] = df["IDX_bytes"].fillna(0) if "IDX_bytes" in df else 0      # format 2's index thumbnail
    extra = pd.read_csv(ROOT / "results" / "experiments" / f"readme_{extra_name or name}.csv")
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
    hot = lambda st: float(np.median(df["L0_bytes"] + df["IDX_bytes"] + df["L1_bytes"] + df["L2_bytes"]  # noqa: E731
                                     if st == 1.0 else df[f"step{st}_L0L1L2_bytes"])) / 1e6
    unc = float(np.median(df["npx"] * df["bit_depth"] / 8)) / 1e6
    arw = mb("file_bytes")
    lossless = [
        ("Uncompressed (14-bit)", unc, False),
        ("ZIP / Deflate", mb("zip_bytes"), False),
        ("Sony ARW, lossless compressed", arw, False),
        ("JPEG-LS lossless", mb("jpegls_bytes"), False),
        ("JPEG XL lossless", mb("B2_bytes"), False),
        ("MiniRaw, all layers (bit-exact)", float(np.median(df["L0_bytes"] + df["IDX_bytes"] + df["L1_bytes"]
                                                           + df["L2_bytes"] + df["L3_bytes"])) / 1e6, True),
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
    hot = lambda st: (df["L0_bytes"] + df["IDX_bytes"] + df["L1_bytes"] + df["L2_bytes"]) if st == 1.0 \
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
        left = idx == 1          # worst-case panel: the right end is crowded by the curves meeting 1 sigma
        ax.text(0.01 if left else 0.99, 1.0, " sensor noise level" if left else "sensor noise level ",
                transform=ax.get_yaxis_transform(), ha="left" if left else "right", va="bottom",
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
    # Direct labels on the left panel, placed off the lines and clear of the noise-level text: MiniRaw below its
    # second-largest file, the square-root curve right of its middle point, JPEG XL right of its largest file.
    anchors = [(0, -2, (0, -16), "center"), (1, 2, (8, 4), "left"), (2, -1, (8, -3), "left")]
    for i, k, off, ha in anchors:
        name, pts = series[i]
        pts = sorted(pts)
        p = pts[k if -len(pts) <= k < len(pts) else -1]
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


def speed_chart(sp, errs, t, path, camera, ref):
    """Encode time (log x) versus median size: lossless panel and near-lossless/lossy panel."""
    med = sp.groupby(["method", "kind"])[["bytes", "seconds"]].median().reset_index()
    style_of = {"lossless": (t["ink2"], "o", "General-purpose codec"),
                "miniraw-lossless": (t["series"][0], "o", "MiniRaw"),
                "miniraw-hot": (t["series"][0], "D", "MiniRaw hot tier (every pixel within 1 sigma)"),
                "lossy": (t["series"][1], "s", "Lossy (worst pixel beyond the sensor noise)")}
    # (dx, dy) label offsets in points, chosen per method so labels clear markers and each other
    offsets = {"JPEG-LS lossless": (0, -16), "JPEG XL lossless, effort 4": (0, 9),
               "JPEG XL lossless, effort 7": (0, 9), "ZIP / Deflate": (8, -3),
               "MiniRaw, all layers, effort 4": (-6, -8), "MiniRaw, all layers, effort 7": (6, -8),
               "MiniRaw hot tier, effort 4": (-9, 3), "MiniRaw hot tier, effort 7": (9, 3),
               "JPEG XL lossy, distance 0.1": (0, -16), "Square-root curve 9-bit + JPEG XL": (0, -16)}
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.6), dpi=100, sharex=True,
                             gridspec_kw={"width_ratios": [1, 1]})
    fig.patch.set_facecolor(t["surface"])
    panels = ((axes[0], ("lossless", "miniraw-lossless"), (20, ref["unc"] * 1.06), "Lossless (exact sensor data)"),
              (axes[1], ("miniraw-hot", "lossy"), (0, 15), "Near-lossless and lossy"))
    handles = {}
    for ax, kinds, ylim, title in panels:
        style(ax, t)
        ax.set_xscale("log")
        ax.set_ylim(*ylim)
        ax.set_title(title, color=t["ink"], fontsize=10, loc="left")
        if kinds[0] == "lossless":
            for y, label in ((ref["unc"], "Uncompressed"), (ref["arw"], "Sony ARW (encoded in camera)")):
                ax.axhline(y, color=t["muted"], linewidth=1, linestyle=(0, (4, 3)), zorder=1)
                ax.text(0.99, y, label + " ", transform=ax.get_yaxis_transform(), ha="right", va="bottom",
                        fontsize=8, color=t["muted"])
        for _, r in med[med["kind"].isin(kinds)].iterrows():
            color, marker, legend = style_of[r["kind"]]
            x, y = r["seconds"], r["bytes"] / 1e6
            h = ax.scatter([x], [y], s=70, color=color, marker=marker, edgecolors=t["surface"], linewidths=1.5,
                           zorder=3)
            handles.setdefault(legend, h)
            dx, dy = offsets.get(r["method"], (6, 4))
            label = r["method"].replace("MiniRaw, all layers", "MiniRaw").replace(", effort", ", e")
            label = label.replace("JPEG XL lossless", "JPEG XL").replace("MiniRaw hot tier", "MiniRaw hot")
            label += f"\n{y:.1f} MB, {x:.2g} s"
            if r["method"] in errs:
                label += f", worst {errs[r['method']]:.1f}\u03c3"
            ax.annotate(label, (x, y), xytext=(dx, dy), textcoords="offset points", fontsize=7.5,
                        ha="left" if dx > 0 else ("right" if dx < 0 else "center"),
                        va="bottom" if dy > 0 else "top",
                        color=t["ink"] if "MiniRaw" in r["method"] else t["ink2"],
                        fontweight="bold" if "MiniRaw" in r["method"] else "normal")
        ax.set_xlim(0.04, 10)
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g} s"))
        ax.set_xlabel("Encode time per photo (log scale)", color=t["ink2"], fontsize=9)
    axes[0].set_ylabel("Median size per photo (MB)", color=t["ink2"], fontsize=9)
    leg = fig.legend(list(handles.values()), list(handles.keys()), loc="lower center", ncol=4, fontsize=8,
                     frameon=False, labelcolor=t["ink2"], bbox_to_anchor=(0.5, -0.01))
    cpu = sp["cpu"].iloc[0] if "cpu" in sp else "Apple M1"      # speed CSVs before the cpu column came from an M1
    fig.suptitle(f"Encode speed versus size: {camera}, median of {sp['file'].nunique()} files, {cpu}, 8 threads "
                 "(lower-left is better)", x=0.02, ha="left", color=t["ink"], fontsize=11, fontweight="bold")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(path, facecolor=t["surface"])
    plt.close(fig)


def main(name, review=None, extra_name=None, prefix=""):
    """prefix names the output files (docs/img/<prefix>size_light.svg, ...), so format 2 charts don't
    overwrite format 1's."""
    df = load(name, extra_name)
    camera = df["camera"].mode()[0].replace("SONY ILCE-7M4", "Sony A7 IV")
    OUT.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": ["Helvetica", "Arial", "DejaVu Sans"], "svg.fonttype": "path"})
    for mode, t in THEMES.items():
        speed_csv = ROOT / "results" / "experiments" / f"speed_{name}.csv"
        sp = pd.read_csv(speed_csv) if speed_csv.exists() else None
        if sp is not None:
            errs = {"MiniRaw hot tier, effort 7": float(np.median(df["stab_maxerr"])),
                    "JPEG XL lossy, distance 0.1": float(np.median(df["B4_0.1_stab_maxerr"])),
                    "Square-root curve 9-bit + JPEG XL": float(np.median(df["B5_9_stab_maxerr"]))}
            ref = {"unc": float(np.median(sp["unc_bytes"])) / 1e6, "arw": float(np.median(sp["file_bytes"])) / 1e6}
        for ext in ("svg",) + (("png",) if review else ()):
            dst = (Path(review) if ext == "png" else OUT)
            size_chart(df, t, dst / f"{prefix}size_{mode}.{ext}", camera)
            quality_chart(df, t, dst / f"{prefix}quality_{mode}.{ext}", camera)
            if sp is not None:
                speed_chart(sp, errs, t, dst / f"{prefix}speed_{mode}.{ext}", camera, ref)
    print("wrote", sorted(p.name for p in OUT.glob(f"{prefix}*.svg")))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("review", nargs="?")
    ap.add_argument("--extra", help="readme_<EXTRA>.csv holds the baselines (default: NAME)")
    ap.add_argument("--prefix", default="", help="output file name prefix")
    a = ap.parse_args()
    main(a.name, a.review, a.extra, a.prefix)
