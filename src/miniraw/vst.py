"""Variance-stabilizing transform and quantization tables (spec 4.3).

The encoder evaluates T in float to build the forward table. Only the integer
`lo` table is stored; the decoder derives n and rec from it with integer ops.
"""
import numpy as np

from . import noise


def T_table(model, xmax):
    """T(x) for every integer x in 0..xmax (float64)."""
    x = np.arange(xmax + 1, dtype=np.float64)
    if model["type"] == "affine":
        a, b, c = model["a"], model["b"], model["c"]
        base = 3.0 / 8.0 * a * a + c
        s = np.maximum(x - b, 0.0)
        t = 2.0 / a * np.sqrt(a * s + base)
        slope_b = 1.0 / np.sqrt(base)            # dT/dx at x = b
        below = x < b
        t[below] = 2.0 / a * np.sqrt(base) - slope_b * (b - x[below])
        return t
    # isotonic: integrate 1/sigma numerically (midpoint rule per DN)
    var = noise.variance_fn(model)(x[:-1] + 0.5)
    inc = 1.0 / np.sqrt(np.maximum(var, 1e-6))
    return np.concatenate([[0.0], np.cumsum(inc)])


def forward_table(T, step, white=None):
    """q(x) for x in 0..xmax: rises by 0 or 1 per DN.

    Values at or above the white level get one bin each: clipped pixels carry
    no noise, so they decode exactly at no rate cost (pitfall 6, deviation D20).
    """
    inc = np.minimum(1.0, np.diff(T) / step)
    if white is not None and white < len(T):
        inc[white - 1:] = 1.0
    v = np.concatenate([[0.0], np.cumsum(inc)])
    q = np.floor(v + 0.5).astype(np.int64)
    # The decoder reconstructs at the bin's middle DN, not its T-midpoint. Where T curves enough inside a wide bin
    # (large steps), that point can miss the bound by up to ~0.01 sigma; split those bins (D27). Encoder-only:
    # the decoder reads just the bin starts. No bin is split at the default step on any benchmark image.
    for _ in range(32):
        lo = lo_from_forward(q)
        n, rec = tables_from_lo(lo, len(q) - 1)
        bad = np.unique(q[bound_check(T, q, rec, step) > 1e-9])
        bad = bad[n[bad] > 1]
        if not len(bad):
            break
        q = forward_from_lo(np.sort(np.concatenate([lo, lo[bad] + n[bad] // 2])), len(q) - 1)
    # q never exceeds xmax, so a 16-bit table (and 16-bit q planes) suffices for 16-bit raws
    return q.astype(np.uint16 if q[-1] < 65536 else np.int64)


def lo_from_forward(q):
    """Smallest x of each bin."""
    starts = np.flatnonzero(np.diff(q)) + 1
    return np.concatenate([[0], starts]).astype(np.int64)


def tables_from_lo(lo, xmax):
    """Decoder-side tables: n (bin size) and rec (bin centre), integers only."""
    lo = np.asarray(lo, dtype=np.int64)
    n = np.diff(np.concatenate([lo, [xmax + 1]]))
    rec = lo + (n - 1) // 2
    return n, rec


def forward_from_lo(lo, xmax):
    q = np.zeros(xmax + 1, np.int64)
    q[np.asarray(lo[1:], np.int64)] = 1
    return np.cumsum(q)


def encode_lo(lo):
    """Delta-code lo (first value is always 0)."""
    return np.diff(np.asarray(lo, np.int64)).astype(np.uint32)


def decode_lo(deltas):
    return np.concatenate([[0], np.cumsum(np.asarray(deltas, np.int64))])


def bound_check(T, fwd, rec, step):
    """Per-DN check of |T(x) - T(rec[q(x)])| <= step/2 + one DN of slack.

    Returns the fraction of DN codes (0..xmax) that violate the bound and the
    worst excess. Applied to pixels through a histogram, so every pixel is covered.
    """
    err = np.abs(T - T[rec[fwd]])
    slope = np.abs(np.diff(T))
    one_dn = np.maximum(np.concatenate([slope, [slope[-1]]]), np.concatenate([[slope[0]], slope]))
    excess = err - (step / 2 + one_dn)
    return excess
