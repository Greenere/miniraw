"""Noise resynthesis feasibility (v3 route 3): code a denoised estimate, regenerate the noise on decode.

Per file, in MiniRaw's noise-stabilized domain (T, unit noise sigma), on a 2x2 grid of 512x512 crops per plane:
  rate      JPEG XL lossless bits/pixel of the current L2 codes (step 1.0) and of the denoised estimate quantized
            at 0.5 and 1.0 sigma. Same per-crop coding on both sides (no cross-plane prediction).
  truth     Error of the denoised estimate against a known clean scene (the 4x box-downsampled G1 plane, noise
            added from this plane's affine fit, as in noise_texture.py), by texture quartile.
  realism   The real residual (y - denoised) in the flattest quartile, against a control with ideal model noise
            on the same scene estimate through the same denoiser: spread, lag-1 correlation, row/column banding.
            Ratios near 1 mean the noise model would resynthesize what is there; above 1, the real noise has
            structure (or the model is too low) that synthesis would miss.
The denoiser is sliding-window 8x8 DCT hard thresholding (2.7 sigma, stride 2), a simple classical baseline.

    python -m bench.experiments.noise_resynthesis data/personal_raws dev [N]
Writes results/experiments/noise_resynthesis.csv
"""
import sys
import warnings
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.fft import dctn, idctn
from scipy.ndimage import uniform_filter

from miniraw import codec, entropy, io_raw, noise, stats, vst
from bench.datasets import path_for, read_split
from bench.run import RAW_EXT

ROOT = Path(__file__).resolve().parents[2]
P, CROP, EFFORT = 8, 512, 7


def denoise(y, thr=2.7, stride=2):
    """Sliding 8x8 DCT hard thresholding at noise sigma 1, DC kept, uniform aggregation."""
    h0, w0 = y.shape
    y = np.pad(y, ((0, P + (-h0) % P), (0, P + (-w0) % P)), mode="reflect")
    h, w = y.shape
    out, wt = np.zeros_like(y), np.zeros_like(y)
    for oy in range(0, P, stride):
        for ox in range(0, P, stride):
            hh, ww = (h - oy) // P * P, (w - ox) // P * P
            blk = y[oy:oy + hh, ox:ox + ww].reshape(hh // P, P, ww // P, P).transpose(0, 2, 1, 3)
            c = dctn(blk, axes=(2, 3), norm="ortho")
            dc = c[..., 0, 0].copy()
            c[np.abs(c) < thr] = 0
            c[..., 0, 0] = dc
            out[oy:oy + hh, ox:ox + ww] += idctn(c, axes=(2, 3), norm="ortho").transpose(0, 2, 1, 3).reshape(hh, ww)
            wt[oy:oy + hh, ox:ox + ww] += 1
    return (out / np.maximum(wt, 1))[:h0, :w0]


def bpp(img):
    return 8 * len(entropy.jxl_encode(img.astype(np.int64), EFFORT)) / img.size


def quartiles(img):
    m = uniform_filter(img, 7)
    v = uniform_filter(img * img, 7) - m * m
    return np.digitize(v, np.percentile(v, [25, 50, 75]))


def realism(r, flat):
    """Spread, lag-1 correlation (h, v) and row/column banding of residual r over the flat mask."""
    warnings.simplefilter("ignore", RuntimeWarning)     # rows or columns with no flat pixel
    rf = np.where(flat, r, np.nan)
    m = np.nanmean(rf)
    d = rf - m
    corr = lambda a, b: np.nanmean(a * b) / np.nanvar(rf)  # noqa: E731
    band = lambda ax: np.nanvar(np.nanmean(d, axis=ax)) * np.sum(flat, axis=ax).mean() / np.nanvar(rf)  # noqa: E731
    return {"std": float(np.nanstd(rf)), "corr_h": float(corr(d[:, 1:], d[:, :-1])),
            "corr_v": float(corr(d[1:], d[:-1])), "band_rows": float(band(1)), "band_cols": float(band(0))}


def crops(shape):
    h, w = shape
    s = min(CROP, h // 2, w // 2) // P * P
    return [(int(cy - s // 2), int(cx - s // 2), s) for cy in (h / 4, 3 * h / 4) for cx in (w / 4, 3 * w / 4)]


def run_file(item):
    group, path = item
    raw = io_raw.read(str(path))
    rng = np.random.default_rng(0)
    models, acc = [], {k: [] for k in ("cur", "d05", "d10", "rstd")}
    real, ctrl = [], []
    for p in range(4):
        x = raw.planes[p]
        model, T, _ = codec.fit_noise(x, raw.black[p], raw.white, raw.noise_profile, p, False)
        models.append(model)
        fwd = vst.forward_table(T, 1.0, raw.white)
        for y0, x0, s in crops(x.shape):
            xc = x[y0:y0 + s, x0:x0 + s]
            y = T[xc]
            yd = denoise(y)
            acc["cur"].append(bpp(fwd[xc]))
            acc["d05"].append(bpp(np.rint(yd / 0.5)))
            acc["d10"].append(bpp(np.rint(yd)))
            flat = quartiles(yd) == 0
            real.append(realism(y - yd, flat))
            ys = yd + rng.normal(0, 1, yd.shape)
            ctrl.append(realism(ys - denoise(ys), flat))
    row = {"group": group, "key": Path(path).stem, "camera": f"{raw.exif.get('Make', '')} {raw.exif.get('Model', '')}",
           "iso": raw.exif.get("ISO"), "snr18": stats.technical(raw, models)["snr18"]}
    row.update({f"{k}_bpp" if k != "rstd" else k: float(np.mean(v)) for k, v in acc.items() if v})
    for k in real[0]:
        row[f"real_{k}"] = float(np.mean([r[k] for r in real]))
        row[f"ctrl_{k}"] = float(np.mean([c[k] for c in ctrl]))
    # Ground truth: the whole 4x-downsampled G1 plane as the clean scene, noise from the plane's affine fit.
    x, b = raw.planes[1], raw.black[1]
    try:
        aff = noise.estimate(x, b, raw.white)[0]
    except noise.TooFewBlocks:
        return row
    h, w = x.shape[0] // 4 * 4, x.shape[1] // 4 * 4
    clean = np.maximum(x[:h, :w].astype(float).reshape(h // 4, 4, w // 4, 4).mean(axis=(1, 3)) - b, 0)
    xn = b + aff["a"] * rng.poisson(clean / aff["a"]) + rng.normal(0, np.sqrt(max(aff["c"], 1e-9)), clean.shape)
    xn = np.clip(np.rint(xn), 0, raw.white).astype(np.int64)
    Tt = vst.T_table(aff, raw.white)
    truth = np.interp(clean + b, np.arange(len(Tt)), Tt)
    err = denoise(Tt[xn]) - truth
    tq = quartiles(truth)
    for k in range(4):
        row[f"truth_rmse_q{k}"] = float(np.sqrt(np.mean(err[tq == k] ** 2)))
    row["truth_rmse"] = float(np.sqrt(np.mean(err ** 2)))
    return row


def main(args):
    limit = int(args[-1]) if args[-1].isdigit() else None
    items = []
    for g in (a for a in args if not a.isdigit()):
        if Path(g).is_dir():
            items += [(Path(g).name, p) for p in sorted(Path(g).iterdir()) if p.suffix.lower() in RAW_EXT][:limit]
        else:
            items += [(g, path_for(k)) for k in read_split(g) if path_for(k).exists()][:limit]
    with Pool(4) as pool:
        df = pd.DataFrame(pool.map(run_file, items))
    df["saving_05"] = 1 - df.d05_bpp / df.cur_bpp
    df["saving_10"] = 1 - df.d10_bpp / df.cur_bpp
    df = df.sort_values("snr18")
    out = ROOT / "results" / "experiments" / "noise_resynthesis.csv"
    df.to_csv(out, index=False)
    cols = ["key", "camera", "iso", "snr18", "cur_bpp", "d05_bpp", "saving_05", "saving_10", "truth_rmse_q0",
            "truth_rmse_q3", "real_std", "ctrl_std", "real_corr_h", "ctrl_corr_h", "real_band_rows", "ctrl_band_rows"]
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(df[cols].to_string(index=False, float_format="%.2f"))
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1:])
