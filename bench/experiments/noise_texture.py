"""Noise-model accuracy on textured synthetic planes (dev images only).

bench/synth.py blurs its clean scenes, so they have almost no texture and the flat-block estimator has an easy
job. Here the clean scene is a 4x4 box-downsampled dev plane with no blur, so natural texture survives (and is
denser per pixel than at full resolution). Known Poisson-Gaussian noise is added in the source's own DN units,
at k = 1, 2 and 4 times the source's estimated slope. The residual source noise (1/16 of the source variance) is
added to the truth from the source's own fit; it is at most about 6% of the truth.

The quantity that matters is the final model's sigma against the truth over the populated range:
over-estimating discards real signal in the hot tier; under-estimating only costs bits.

    python -m bench.experiments.noise_texture [OUT_CSV]
"""
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

from bench.datasets import camera_of, path_for, read_split
from miniraw import codec, io_raw, noise

ROOT = Path(__file__).resolve().parents[2]
K_VALUES = (1.0, 2.0, 4.0)
C_VALUES = (4.0, 25.0)
BOX = 4


def clean_textured(plane, black):
    h, w = plane.shape
    p = plane[: h - h % BOX, : w - w % BOX].astype(np.float64) - black
    return np.maximum(p.reshape(h // BOX, BOX, w // BOX, BOX).mean(axis=(1, 3)), 0)


def noisy(clean, a, c, b, white, rng):
    x = b + a * rng.poisson(clean / a) + rng.normal(0, np.sqrt(c), clean.shape)
    return np.clip(np.rint(x), 0, white).astype(np.uint16)


def evaluate(model, true_var, b, clean):
    lo, hi = np.percentile(clean, [5, 99.5])
    v = np.linspace(max(lo, 1.0), hi, 64)
    r = np.sqrt(noise.variance_fn(model)(v + b) / true_var(v))
    return float(r.max()), float(r.min()), float(np.median(r))


def run_key(key):
    raw = io_raw.read(path_for(key))
    if io_raw.histogram_gaps(raw.planes, raw.black, raw.white) > io_raw.GAP_THRESHOLD:
        return []
    rows = []
    for p in range(4):
        b, white = raw.black[p], raw.white
        try:
            src, _ = noise.estimate(raw.planes[p], b, white)
        except noise.TooFewBlocks:
            continue
        clean = clean_textured(raw.planes[p], b)
        resid = lambda v: noise.variance_fn(src)(np.asarray(v) + b) / BOX ** 2  # noqa: E731
        for k in K_VALUES:
            a = k * src["a"]
            for c in C_VALUES:
                true_var = lambda v: a * np.asarray(v) + c + resid(v)  # noqa: E731
                x = noisy(clean, a, c, b, white, np.random.default_rng([p, int(k), int(c)]))
                model, _, info = codec.fit_noise(x, b, white, None, p, False)
                over, under, med = evaluate(model, true_var, b, clean)
                est = info.get("estimated") or {}
                rows.append(dict(key=key, camera=camera_of(key), plane=p, k=k, a=a, c=c, model=model["type"],
                                 a_est=est.get("a"), c_est=est.get("c"), over=over, under=under, median=med,
                                 resid_frac=float(np.median(resid(clean) / true_var(clean)))))
    return rows


def main(out=None):
    out = Path(out or ROOT / "results" / "experiments" / "noise_texture.csv")
    with Pool(8) as pool:
        rows = [r for rs in pool.map(run_key, read_split("dev")) for r in rs]
    df = pd.DataFrame(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    df["a_rel"] = df.a_est / df.a - 1
    print(f"{len(df)} planes x settings from {df.key.nunique()} dev images; median residual-noise share "
          f"{df.resid_frac.median():.1%}")
    print(f"fallback (isotonic) rate {np.mean(df.model == 'isotonic'):.1%}")
    print(f"model sigma / true sigma: worst over-estimate median {df.over.median():.3f}, "
          f"p90 {df.over.quantile(0.9):.3f}, max {df.over.max():.2f}; share with over > 1.1: {np.mean(df.over > 1.1):.1%}")
    print(f"median ratio {df['median'].median():.3f}; affine a_est/a - 1: median {df.a_rel.median():+.3f}")
    print(df.groupby("k").agg(fallback=("model", lambda m: np.mean(m == "isotonic")), over_med=("over", "median"),
                              over_p90=("over", lambda v: v.quantile(0.9)), share_over=("over", lambda v: np.mean(v > 1.1)),
                              a_rel=("a_rel", "median")).round(3).to_string())


if __name__ == "__main__":
    main(*sys.argv[1:2])
