"""Chart for results/index_benchmark.md: neighbour overlap with the full render against L1's size.

    python -m bench.experiments.index_charts [SPLIT] [REVIEW_DIR]
Needs results/experiments/index_<split>_images.csv and _metrics.csv. Writes docs/img/index_overlap_{light,dark}.svg.
"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .readme_charts import MARKERS, OUT, ROOT, THEMES, style  # noqa: E402

LABELS = {"clip-b32": "CLIP ViT-B/32 (224 px)", "siglip-b16-256": "SigLIP ViT-B/16 (256 px)"}


def chart(img, met, t, path):
    fig, ax = plt.subplots(figsize=(8.6, 4.4), dpi=100)
    fig.patch.set_facecolor(t["surface"])
    style(ax, t)
    ax.set_xscale("log")
    kb = {a: float(np.median(img[f"{a}_bytes"])) / 1e3 for a in ("L1-64", "L1-128", "L1-256")}
    ax.axhline(0.80, color=t["muted"], linewidth=1, linestyle=(0, (4, 3)), zorder=1)
    ax.text(0.99, 0.80, "spec threshold 0.80 ", transform=ax.get_yaxis_transform(), ha="right", va="bottom",
            fontsize=8, color=t["muted"])
    for i, (model, label) in enumerate(LABELS.items()):
        m = met[met.model == model].set_index("arm")
        xs = [kb[a] for a in ("L1-64", "L1-128", "L1-256")]
        ys = [m.loc[f"{a}-half", "overlap@10"] for a in ("L1-64", "L1-128", "L1-256")]
        ax.plot(xs, ys, color=t["series"][i], linewidth=2, marker=MARKERS[i], markersize=7,
                markeredgecolor=t["surface"], markeredgewidth=1.5, label=f"MiniRaw L1, {label}", zorder=3)
        e = m.loc["E", "overlap@10"]
        ax.axhline(e, color=t["series"][i], linewidth=1, linestyle=(0, (1, 2)), zorder=2)
        above = e >= met[(met.arm == "E") & (met.model != model)]["overlap@10"].iloc[0]
        ax.annotate(f"camera thumbnail (E), {label.split(' (')[0]}: {e:.2f}", (kb["L1-256"] * 1.55, e),
                    xytext=(0, 3 if above else -3), textcoords="offset points", ha="right",
                    va="bottom" if above else "top", fontsize=7.5, color=t["ink2"])
    for a, x in kb.items():
        ax.annotate(f"{a.split('-')[1]} px  ({x:.0f} KB)", (x, 0.45), ha="center", fontsize=8, color=t["ink2"])
    ax.set_ylim(0.42, 1.0)
    ax.set_xlim(kb["L1-64"] * 0.55, kb["L1-256"] * 1.6)
    ax.xaxis.set_major_locator(matplotlib.ticker.FixedLocator([20, 50, 100, 200]))
    ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g} KB"))
    ax.set_xlabel("L1 size per image (median; l1_min_side below)", color=t["ink2"], fontsize=9)
    ax.set_ylabel("Neighbour overlap@10 with the full render", color=t["ink2"], fontsize=9)
    leg = ax.legend(loc="upper left", fontsize=8, frameon=False, labelcolor=t["ink2"])
    for line in leg.get_lines():
        line.set_linewidth(2)
    fig.suptitle(f"Search index quality versus base-layer size, {len(img)} FiveK images (higher is better)",
                 x=0.02, ha="left", color=t["ink"], fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, facecolor=t["surface"])
    plt.close(fig)


def main(split="index500", review=None):
    img = pd.read_csv(ROOT / "results" / "experiments" / f"index_{split}_images.csv")
    img = img[img.status == "ok"]
    met = pd.read_csv(ROOT / "results" / "experiments" / f"index_{split}_metrics.csv")
    plt.rcParams.update({"font.family": ["Helvetica", "Arial", "DejaVu Sans"], "svg.fonttype": "path"})
    for mode, t in THEMES.items():
        for ext in ("svg",) + (("png",) if review else ()):
            dst = Path(review) if ext == "png" else OUT
            chart(img, met, t, dst / f"index_overlap_{mode}.{ext}")
    print("wrote", sorted(p.name for p in OUT.glob("index_overlap_*.svg")))


if __name__ == "__main__":
    main(*sys.argv[1:3])
