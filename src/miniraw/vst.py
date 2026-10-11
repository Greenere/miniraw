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
    # (large steps), that point can miss the bound by up to ~0.02 sigma (D27). From the first such bin on, each bin
    # is trimmed at the top until it passes and the leftover DNs start the next bin (D31; halving them, as D27 did,
    # doubled the codes where a dark high-ISO frame keeps nearly all its pixels). Encoder-only: the decoder reads
    # just the bin starts. Tables with no failing bin are unchanged, as at the default step on every benchmark image.
    lo = lo_from_forward(q)
    n, rec = tables_from_lo(lo, len(q) - 1)
    bad = np.flatnonzero(bound_check(T, q, rec, step) > 1e-9)
    if len(bad):
        q = forward_from_lo(_trim_bins(T, step, lo, q[bad[0]]), len(q) - 1)
    # q never exceeds xmax, so a 16-bit table (and 16-bit q planes) suffices for 16-bit raws
    return q.astype(np.uint16 if q[-1] < 65536 else np.int64)


def _trim_bins(T, step, lo, first):
    """Bin starts with bins from index `first` on trimmed at the top until the middle DN keeps the bound."""
    slack = step / 2 + _one_dn(T) + 1e-9
    ends = np.concatenate([lo[1:], [len(T)]])
    starts, s = list(lo[:first]), int(lo[first])
    i = first
    while s < len(T):
        e = int(ends[min(i, len(ends) - 1)])    # past the last bin, the leftover runs to the end
        i += 1
        if e <= s:          # swallowed by a bin that started early with an earlier bin's leftover
            continue
        while e - s > 1 and np.any(np.abs(T[s:e] - T[s + (e - s - 1) // 2]) > slack[s:e]):
            e -= 1
        starts.append(s)
        s = e
    return np.array(starts, np.int64)


def _one_dn(T):
    """|dT| over one DN at each x, the larger of its two neighbours (the bound's slack)."""
    slope = np.abs(np.diff(T))
    return np.maximum(np.concatenate([slope, [slope[-1]]]), np.concatenate([[slope[0]], slope]))


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


def ceil_allowed_bits(T, fwd, step):
    """Per-bin flags for rec "csafe": 1 for even-sized bins whose ceiling (rec + 1) keeps every value in the bin
    within the error bound. Those bins alternate floor and ceiling on a pixel checkerboard, which cancels the
    floor offset inside each bin; all other bins use the floor (D30)."""
    lo = lo_from_forward(fwd)
    n, rec = tables_from_lo(lo, len(fwd) - 1)
    one_dn = _one_dn(T)
    q = fwd.astype(np.int64)
    ceil = rec[q] + 1
    ok_dn = np.abs(T - T[np.minimum(ceil, len(T) - 1)]) <= step / 2 + one_dn + 1e-9
    bad = np.bincount(q, weights=~ok_dn, minlength=len(lo)) > 0
    return ((n % 2 == 0) & ~bad).astype(np.uint8)


def bound_check(T, fwd, rec, step):
    """Per-DN check of |T(x) - T(rec[q(x)])| <= step/2 + one DN of slack.

    Returns the fraction of DN codes (0..xmax) that violate the bound and the
    worst excess. Applied to pixels through a histogram, so every pixel is covered.
    """
    err = np.abs(T - T[rec[fwd]])
    one_dn = _one_dn(T)
    excess = err - (step / 2 + one_dn)
    return excess
