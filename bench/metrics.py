"""Section 8 metrics that need no rendering: rate, error bound, bias, stabilized RMSE,
exactness, region reads, outliers."""
import numpy as np
from scipy.ndimage import maximum_filter, minimum_filter

from miniraw import noise, vst


def bpp(nbytes, npx):
    return 8.0 * nbytes / npx


def bound_violations(x, T, fwd, rec, step, checker=False):
    """Pixels violating |T(x) - T(rec[q(x)])| <= step/2 + 1 DN slack (every pixel checked).

    With checker=True, also checks rec + 1 for even-sized bins, which checker
    mode emits on half the pixels.
    """
    excess = vst.bound_check(T, fwd, rec, step)
    if checker:
        lo = vst.lo_from_forward(fwd)
        n, _ = vst.tables_from_lo(lo, len(fwd) - 1)
        excess = np.maximum(excess, vst.bound_check(T, fwd, rec + ((n - 1) & 1), step))
    bad = excess > 1e-9
    return int(np.bincount(x.ravel(), minlength=len(T))[: len(T)][bad].sum())


def stabilized_errors(orig, dec, T):
    d = T[dec.astype(np.int64)] - T[orig.astype(np.int64)]
    return float(np.sqrt(np.mean(d * d))), float(np.max(np.abs(d)))


def bias_by_decile(orig, dec, model):
    """Mean of (decoded - original)/sigma per intensity decile; returns max |bias|."""
    o = orig.ravel().astype(np.float64)
    e = dec.ravel().astype(np.float64) - o
    s = np.sqrt(np.maximum(noise.variance_fn(model)(o), 1e-9))
    edges = np.unique(np.percentile(o, np.linspace(0, 100, 11)))
    idx = np.clip(np.searchsorted(edges, o, side="right") - 1, 0, len(edges) - 2)
    z = e / s
    sums = np.bincount(idx, weights=z, minlength=len(edges) - 1)
    cnt = np.bincount(idx, minlength=len(edges) - 1)
    means = sums[cnt > 0] / cnt[cnt > 0]
    return float(np.max(np.abs(means)))


def outlier_count(plane, model, k=6.0):
    """Hot/cold pixels (pitfall 7): more than k sigma beyond every one of the 8 neighbours.

    A median-difference test flags edges in textured scenes; requiring the pixel
    to clear the neighbourhood max (or min) isolates single-pixel spikes.
    """
    x = plane.astype(np.float64)
    fp = np.ones((3, 3), bool)
    fp[1, 1] = False
    nmax = maximum_filter(x, footprint=fp, mode="nearest")
    nmin = minimum_filter(x, footprint=fp, mode="nearest")
    s = np.sqrt(np.maximum(noise.variance_fn(model)(x), 1e-9))
    return int(((x - nmax > k * s) | (nmin - x > k * s)).sum())


def l3_ideal_bits(n, q):
    return float(np.log2(n[q].astype(np.float64)).sum())


def region_read_check(reader, layout, pshape, region):
    """True if the reader log holds only the header read, L1 sections and the touched tiles."""
    from miniraw import container, layers
    x, y, w, h = region
    py0, px0, py1, px1 = y // 2, x // 2, -(-(y + h) // 2), -(-(x + w) // 2)
    touched = {(t[0], t[1]) for t in layers.tiles_touching(pshape, layout["tile"], py0, px0, py1, px1)}
    allowed = {(0, container.FIRST_READ)}
    for (layer, p, ty, tx), (off, length, _) in reader.sections.items():
        if layer == "L1" or (layer in ("L2", "L3") and (ty, tx) in touched):
            allowed.add((off, length))
    log = [e for e in reader.log if e[0] != "sidecar"]
    return all((o, l) in allowed for o, l in log[1:]) and log[0] == (0, container.FIRST_READ)
