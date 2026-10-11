"""Are the header's technical stats (D28) comparable across cameras? (results/index_benchmark.md, H4)

    python -m bench.experiments.header_stats [SPLIT]

For every image: the noise model as the encoder fits it, the raw-DN slope `a` stored before D28, and
miniraw.stats.technical. Compares how well each tracks ISO when cameras are pooled, and how far camera medians
spread at the same ISO. Linear sources only for the noise figures.
"""
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from bench.datasets import camera_of, load_meta, path_for, read_split
from miniraw import codec, io_raw, stats

ROOT = Path(__file__).resolve().parents[2]


def one(key):
    try:
        raw = io_raw.read(path_for(key))
    except io_raw.NotBayer:
        return None
    models = [codec.fit_noise(raw.planes[p], raw.black[p], raw.white, raw.noise_profile, p, False)[0]
              for p in range(4)]
    aff = [m["a"] for m in models if m["type"] == "affine"]
    meta = load_meta()[key]["categories"]
    return {"key": key, "camera": camera_of(key), "iso": raw.exif.get("ISO"), "bit_depth": raw.bit_depth,
            "linear": io_raw.histogram_gaps(raw.planes, raw.black, raw.white) <= io_raw.GAP_THRESHOLD,
            "a_dn": float(np.mean(aff)) if aff else np.nan, "time": meta.get("time"),
            **{k: v for k, v in stats.technical(raw, models).items() if not k.endswith("_planes")}}


def spread(df, col, iso):
    g = df[df.iso == iso].groupby("camera")[col].median().dropna()
    return (g.max() / g.min(), len(g)) if len(g) >= 3 else (np.nan, len(g))


def main(split="index500"):
    with Pool(8) as pool:
        rows = [r for r in pool.map(one, read_split(split)) if r]
    df = pd.DataFrame(rows)
    out = ROOT / "results" / "experiments" / f"header_stats_{split}.csv"
    df.to_csv(out, index=False)
    lin = df[df.linear & df.iso.notna()].copy()
    lin["noise_rel"] = 1 / lin.snr18                      # noise at 18% grey, as a fraction of the signal
    print(f"{len(df)} images, {len(lin)} linear with ISO, {lin.camera.nunique()} cameras -> {out}")
    print("\nPooled across cameras, Spearman with ISO (higher = tracks ISO better):")
    for col, name in (("a_dn", "stored before: noise slope a, raw DN"), ("noise_rel", "noise at 18% grey / signal")):
        print(f"  {name:45s} {spearmanr(lin.iso, lin[col], nan_policy='omit')[0]:+.2f}")
    print("\nWithin camera, median Spearman with ISO (cameras with >= 6 images at >= 3 ISOs):")
    for col in ("a_dn", "noise_rel"):
        rs = [spearmanr(g.iso, g[col], nan_policy="omit")[0] for _, g in lin.groupby("camera")
              if len(g) >= 6 and g.iso.nunique() >= 3]
        print(f"  {col:10s} {np.nanmedian(rs):+.2f} over {len(rs)} cameras")
    print("\nSpread of camera medians at the same ISO (max / min):")
    for iso in (100, 200, 400):
        g = lin[lin.iso == iso].groupby("camera")
        if g.ngroups < 3:
            continue
        a, nr = (g[c].median() for c in ("a_dn", "noise_rel"))
        print(f"  ISO {iso}: {g.ngroups} cameras; a {a.max() / a.min():.1f}x; noise at 18% grey "
              f"{nr.max() / nr.min():.1f}x")
    print("\nExposure: AUC of darkness for FiveK 'night' (log-mean level vs plain mean level):")
    k = df[df.time.isin(["day", "dusk", "night"])]
    pos = (k.time == "night").to_numpy()
    for col in ("log_mean", "mean_level"):
        s = pd.Series(-k[col].to_numpy()).rank().to_numpy()
        print(f"  {col:10s} {(s[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * (~pos).sum()):.3f}")
    print(f"\nClipped share: median {df.clipped_frac.median():.4f}, images with more than 1% clipped "
          f"{int((df.clipped_frac > 0.01).sum())} of {len(df)}")


if __name__ == "__main__":
    main(*sys.argv[1:2])
