"""Noise parameter estimation (spec 4.2).

Var(x) = a * max(x - b, 0) + c, per plane. Estimator: 8x8 blocks, robust MAD
variance of a Laplacian residual (deviation D1), 32 log-spaced intensity bins, median of the
lowest-20% variances per bin, Huber fit. Falls back to an isotonic fit when the
stabilized flat-block sigma leaves [0.9, 1.1].
"""
from functools import lru_cache

import numpy as np
from scipy.optimize import least_squares

BLOCK = 8
NBINS = 32
KEEP = 0.20
MIN_KEPT = 8          # kept blocks needed for a bin to enter the fit
MIN_KEPT_VALIDATE = 50


def _extract(plane, white):
    """Unclipped 8x8 blocks (raw integer values) and their means."""
    h, w = plane.shape
    h, w = h - h % BLOCK, w - w % BLOCK
    blk = plane[:h, :w].reshape(h // BLOCK, BLOCK, w // BLOCK, BLOCK).transpose(0, 2, 1, 3)
    blk = blk.reshape(-1, BLOCK, BLOCK)
    ok = (blk.min(axis=(1, 2)) > 0) & (blk.max(axis=(1, 2)) < 0.98 * white)
    blk = blk[ok]
    return blk, blk.mean(axis=(1, 2), dtype=np.float64)


def _block_stats(plane, white):
    blk, mean = _extract(plane, white)
    return mean, _robust_var(blk)


def _median_rows(d):
    """Row medians via one partition (equal to np.median along axis 1)."""
    m = d.shape[1]
    if m % 2:
        return np.partition(d, m // 2, axis=1)[:, m // 2]
    part = np.partition(d, [m // 2 - 1, m // 2], axis=1)
    return (part[:, m // 2 - 1] + part[:, m // 2]) / 2


def _robust_var(blk):
    """Robust per-block noise variance from the Immerkaer 3x3 Laplacian residual.

    The kernel [[1,-2,1],[-2,4,-2],[1,-2,1]] cancels planar gradients, so smooth
    texture leaks far less into the estimate than with neighbour differences.
    Its output has variance 36 * sigma^2 for white noise.
    """
    # float32 is exact here: |Laplacian| of 16-bit integers stays below 2**24
    blk = blk.astype(np.float32) if blk.dtype != np.float64 else blk
    d = (blk[:, :-2, :-2] - 2 * blk[:, :-2, 1:-1] + blk[:, :-2, 2:]
         - 2 * blk[:, 1:-1, :-2] + 4 * blk[:, 1:-1, 1:-1] - 2 * blk[:, 1:-1, 2:]
         + blk[:, 2:, :-2] - 2 * blk[:, 2:, 1:-1] + blk[:, 2:, 2:]).reshape(len(blk), -1)
    med = _median_rows(d)[:, None]
    mad = _median_rows(np.abs(d - med)).astype(np.float64)
    return (1.4826 * mad / 6.0) ** 2


@lru_cache(maxsize=None)
def selection_bias():
    """E[median of lowest 20% of block MAD-variances] / true variance, for pure noise.

    Monte Carlo with seed 0. Dividing by this undoes the low bias that comes from
    keeping only the least-varying blocks.
    """
    rng = np.random.default_rng(0)
    ratios = []
    for _ in range(20):
        blk = rng.normal(0, 100.0, (2000, BLOCK, BLOCK))
        v = np.sort(_robust_var(blk))
        ratios.append(np.median(v[: int(len(v) * KEEP)]) / 100.0 ** 2)
    return float(np.mean(ratios))


def _binned(mean, var, b, white, min_kept=MIN_KEPT):
    s = np.maximum(mean - b, 0.5)
    edges = np.geomspace(0.5, max(white - b, 2.0), NBINS + 1)
    idx = np.clip(np.searchsorted(edges, s, side="right") - 1, 0, NBINS - 1)
    xs, vs, ws = [], [], []
    for i in range(NBINS):
        sel = idx == i
        n = int(sel.sum())
        k = int(n * KEEP)
        if k < min_kept:
            continue
        order = np.argsort(var[sel])[:k]
        xs.append(float(np.median(mean[sel][order])))
        vs.append(float(np.median(var[sel][order])) / selection_bias())
        ws.append(k)
    return np.array(xs), np.array(vs), np.array(ws, float)


def fit_affine(xs, vs, ws, b):
    s = np.maximum(xs - b, 0.0)
    a0, c0 = np.polyfit(s, vs, 1, w=np.sqrt(ws))
    a0, c0 = max(a0, 1e-3), max(c0, 1e-3)
    sw = np.sqrt(ws / ws.max())

    def resid(p):
        a, c = p
        return sw * (a * s + c - vs) / np.maximum(vs, 1e-3)

    r = least_squares(resid, [a0, c0], bounds=([1e-6, 0.0], [np.inf, np.inf]),
                      loss="huber", f_scale=0.1)
    return float(r.x[0]), float(r.x[1])


def _pav(y, w):
    """Pool-adjacent-violators: weighted non-decreasing fit."""
    vals, wts, cnt = [], [], []
    for yi, wi in zip(y, w):
        vals.append(yi); wts.append(wi); cnt.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v2, w2, c2 = vals.pop(), wts.pop(), cnt.pop()
            v1, w1, c1 = vals.pop(), wts.pop(), cnt.pop()
            vals.append((v1 * w1 + v2 * w2) / (w1 + w2)); wts.append(w1 + w2); cnt.append(c1 + c2)
    return np.repeat(vals, cnt)


def variance_fn(model):
    """Vectorized Var(x) for a noise model dict (encoder side only)."""
    if model["type"] == "affine":
        a, b, c = model["a"], model["b"], model["c"]
        return lambda x: a * np.maximum(np.asarray(x, float) - b, 0) + c
    kx, kv = np.array(model["knots_x"]), np.array(model["knots_v"])
    return lambda x: np.interp(np.asarray(x, float), kx, kv)


def stabilized_sigma_by_bin(plane, model, white, table_T, blocks=None):
    """Per-bin flat-block sigma after mapping through T (float table).

    blocks: (blk, mean) from estimate(), to avoid re-extracting them.
    """
    blk, mean = blocks if blocks is not None else _extract(plane, white)
    tv = _robust_var(table_T.astype(np.float32)[blk])
    xs, vs, ws = _binned(mean, tv, model["b"], white, MIN_KEPT_VALIDATE)
    return xs, np.sqrt(vs), ws


def estimate(plane, b, white, return_blocks=False):
    """Fit the affine model for one plane. Returns a model dict plus the binned data
    (and the extracted blocks, for reuse in validation, if return_blocks)."""
    blk, mean = _extract(plane, white)
    var = _robust_var(blk)
    xs, vs, ws = _binned(mean, var, b, white)
    if len(xs) < 2:
        raise ValueError("too few flat blocks to estimate noise")
    a, c = fit_affine(xs, vs, ws, b)
    model = {"type": "affine", "a": a, "b": float(b), "c": c}
    if return_blocks:
        return model, (xs, vs, ws), (blk, mean)
    return model, (xs, vs, ws)


def isotonic_model(xs, vs, ws, b, xmax):
    v = np.maximum(_pav(vs, ws), 1.0 / 12)
    kx = np.concatenate([[0.0], xs, [float(xmax)]])
    kv = np.concatenate([[v[0]], v, [v[-1]]])
    return {"type": "isotonic", "b": float(b), "knots_x": kx.tolist(), "knots_v": kv.tolist()}


def from_noise_profile(pairs, plane_index, b, white):
    """DNG NoiseProfile (S, O) for normalized x -> (a, c) in raw DN."""
    if len(pairs) == 1:
        S, O = pairs[0]
    else:  # one pair per CFA color: R, G, B
        S, O = pairs[[0, 1, 1, 2][plane_index]]
    r = white - b
    return {"type": "affine", "a": S * r, "b": float(b), "c": O * r * r}


def disagreement(m1, m2, white):
    """Max ratio of predicted variances at three signal levels."""
    f1, f2 = variance_fn(m1), variance_fn(m2)
    b = m1["b"]
    xs = np.array([b, b + 0.05 * (white - b), b + 0.5 * (white - b)])
    r = f1(xs) / np.maximum(f2(xs), 1e-9)
    return float(np.max(np.maximum(r, 1 / r)))
