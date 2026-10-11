"""Dev experiment: ideal L3 cost with a context model vs the uniform model.

Context = where the 3x3 local mean of signal-mode reconstructions (known to the
decoder from L2 alone) falls relative to the pixel's bin. Reports empirical
conditional entropy (adaptive tables, overhead ignored) vs sum log2 n.
"""
from multiprocessing import Pool

import numpy as np
from scipy.ndimage import uniform_filter

from miniraw import codec, io_raw, vst
from bench.datasets import camera_of, path_for, read_split

CELLS = 16


def cond_entropy(sym, ctx):
    """sum -log2 p(sym | ctx) with empirical p."""
    key = ctx.astype(np.int64) * 1_000_003 + sym
    _, joint = np.unique(key, return_counts=True)
    _, marg = np.unique(ctx, return_counts=True)
    return float((joint * np.log2(joint)).sum() * -1 + (marg * np.log2(marg)).sum())


def run(key):
    raw = io_raw.read(path_for(key))
    if io_raw.histogram_gaps(raw.planes, raw.black, raw.white) > io_raw.GAP_THRESHOLD:
        return None
    uni = ctx_bits = ctx5_bits = 0.0
    fb = 0
    for p in range(4):
        x = raw.planes[p].astype(np.int64)
        model, T, info = codec.fit_noise(raw.planes[p], raw.black[p], raw.white)
        fb += model["type"] == "isotonic"
        fwd = vst.forward_table(T, 1.0, raw.white)
        lo = vst.lo_from_forward(fwd); n, rec = vst.tables_from_lo(lo, len(fwd) - 1)
        q = fwd[x]; nn = n[q]; kap = x - lo[q]
        r = rec[q].astype(np.float64)
        sel = nn > 1
        uni += np.log2(nn[sel]).sum()
        for size, acc in ((3, "c3"), (5, "c5")):
            mu = uniform_filter(r, size, mode="nearest")
            pos = np.clip(np.floor((mu - lo[q]) / nn * 4), -6, 9).astype(np.int64)   # 16 classes
            big = nn > 32
            sym = np.where(big, kap * CELLS // nn, kap)
            nkey = np.where(big, 1000 + np.minimum(nn // 8, 50), nn)               # n bucket for big bins
            ctx = (nkey * 32 + pos + 6)[sel]
            bits = cond_entropy(sym[sel], ctx)
            bits += (np.log2(nn[sel & big] / CELLS)).sum()                       # uniform within a cell
            if acc == "c3":
                ctx_bits += bits
            else:
                ctx5_bits += bits
    npx = raw.planes.size
    return camera_of(key), fb, 8 * 0 + uni / npx, ctx_bits / npx, ctx5_bits / npx


if __name__ == "__main__":
    with Pool(4) as pool:
        rows = [r for r in pool.map(run, read_split("dev")) if r]
    print(f"{'camera':24s} fallbacks  L3 uniform  ctx3x3   ctx5x5   (bpp)")
    for cam, fb, u, c3, c5 in rows:
        print(f"{cam:24s} {fb:5d}      {u:6.3f}    {c3:6.3f}   {c5:6.3f}   saving {u - min(c3, c5):.3f}")
    s = np.array([[r[2], r[3], r[4]] for r in rows])
    print(f"median saving: ctx3 {np.median(s[:, 0] - s[:, 1]):.3f} bpp, ctx5 {np.median(s[:, 0] - s[:, 2]):.3f} bpp")
