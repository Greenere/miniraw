"""Markdown tables for results/fivek_benchmark.md from per-image CSVs.

    python -m bench.experiments.heldout_summary holdout [test test_pre_d26 dev ...]
The first split gets the headline and per-camera tables; every split listed gets a row in the comparison table.
Linear sources only (histogram gaps <= 5%), as in the report's gate table.
"""
import glob
import sys

import numpy as np
import pandas as pd

from bench.report import RESULTS, boot_median_ci


def load(split, linear=True):
    df = pd.concat([pd.read_csv(f) for f in glob.glob(str(RESULTS / "per_image" / split / "*.csv"))])
    df = df[df.status == "ok"].copy()
    df["lossy_source"] = df.lossy_source.astype(str) == "True"
    df["hot"] = df.L0_bytes + (df.IDX_bytes.fillna(0) if "IDX_bytes" in df else 0) + df.L1_bytes + df.L2_bytes
    df["total"] = df.hot + df.L3_bytes
    return df[~df.lossy_source] if linear else df


def ci(v):
    m, lo, hi = boot_median_ci(v)
    return f"{m:.3f} [{lo:.3f}, {hi:.3f}]"


def headline(df):
    rows = [("Hot tier (L0-L2) / camera raw file", df.hot / df.B1_bytes),
            ("Hot tier / JPEG XL lossless", df.hot / df.B2_bytes),
            ("All layers, sensor-exact / camera raw file", df.total / df.B1_bytes),
            ("All layers / JPEG XL lossless", df.total / df.B2_bytes),
            ("JPEG XL lossless / camera raw file", df.B2_bytes / df.B1_bytes)]
    out = ["| Ratio | Median [95% CI] |", "| --- | --- |"]
    out += [f"| {name} | {ci(v)} |" for name, v in rows]
    return "\n".join(out)


def comparison(splits):
    out = ["| Split | Linear images | Hot / JPEG XL | All layers / JPEG XL | Worst pixel, step 1.0 (median) | "
           "Pixels outside bound, step 1.0 | Pixels outside bound, step 2.0 | Images with lowered or isotonic model |",
           "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for s in splits:
        lin, every = load(s), load(s, linear=False)
        out.append(f"| {s} | {len(lin)} of {len(every)} | {np.median(lin.hot / lin.B2_bytes):.3f} | "
                   f"{np.median(lin.total / lin.B2_bytes):.3f} | {np.median(lin.stab_maxerr):.2f}σ | "
                   f"{int(every.bound_violations.sum()):,} | {int(every['step2.0_bound_violations'].sum()):,} | "
                   f"{int((every.fallbacks > 0).sum())} of {len(every)} |")
    return "\n".join(out)


def per_camera(df):
    g = df.groupby("camera")
    t = pd.DataFrame({"n": g.size(), "bits": g.bit_depth.first(),
                      "native": g.apply(lambda d: np.median(8 * d.B1_bytes / d.npx)),
                      "jxl": g.apply(lambda d: np.median(8 * d.B2_bytes / d.npx)),
                      "total": g.apply(lambda d: np.median(8 * d.total / d.npx)),
                      "hot": g.apply(lambda d: np.median(8 * d.hot / d.npx)),
                      "hot_ratio": g.apply(lambda d: np.median(d.hot / d.B1_bytes)),
                      "worst": g.stab_maxerr.median()}).sort_values("hot_ratio")
    out = ["| Camera | Images | Bits | Camera file | JPEG XL lossless | MiniRaw all layers | MiniRaw hot tier | "
           "Hot / camera file | Worst pixel |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for cam, r in t.iterrows():
        out.append(f"| {cam} | {int(r.n)} | {int(r.bits)} | {r.native:.2f} | {r.jxl:.2f} | {r.total:.2f} | {r.hot:.2f} | "
                   f"{r.hot_ratio:.2f} | {r.worst:.2f}σ |")
    return "\n".join(out)


def main(first, *more):
    df = load(first)
    print(f"## {first}: {len(df)} linear images, {df.camera.nunique()} cameras\n")
    print(headline(df), "\n")
    print(comparison([first, *more]), "\n")
    print("Bits per pixel, medians per camera:\n")
    print(per_camera(df))


if __name__ == "__main__":
    main(*sys.argv[1:])
