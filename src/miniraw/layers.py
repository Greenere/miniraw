"""L1 base pyramid (construction A), L2 and L3 tiling (spec 4.4-4.6). Integer only."""
import numpy as np


# Plane groups coded together as one JPEG XL image (plane order R, G1, G2, B).
PACKINGS = {
    "P1": [[0], [1], [2], [3]],          # four grayscale images
    "P2": [[0, 1, 3], [2]],              # RGB (R, G1, B) + G2
    "P2g": [[1, 0, 3], [2]],             # G1 first, so it can predict R and B
    "P3": [[0, 1, 2, 3]],                # one 4-channel image
    "PG": [[1, 2], [0], [3]],            # greens together, R and B alone
}


def choose_k(plane_shape, min_side=256):
    k = 1
    while min(plane_shape) // (k * 2) >= min_side:
        k *= 2
    return k


def l1_from_q(q, k):
    """Floor-mean of q over k x k blocks; partial edge blocks average what they hold."""
    h, w = q.shape
    hb, wb = -(-h // k), -(-w // k)
    if (h, w) != (hb * k, wb * k):
        pad = np.zeros((hb * k, wb * k), q.dtype)
        pad[:h, :w] = q
    else:
        pad = q
    s = pad.reshape(hb, k, wb, k).sum(axis=(1, 3), dtype=np.int64)
    rows = np.minimum(k, h - np.arange(hb) * k)
    cols = np.minimum(k, w - np.arange(wb) * k)
    return s // (rows[:, None] * cols[None, :])


def _bilinear_axis(idx, k, n):
    """Integer source indices and weights (denominator 2k) for output positions idx."""
    num = 2 * idx + 1 - k
    j0 = np.floor_divide(num, 2 * k)
    f = num - j0 * 2 * k
    return np.clip(j0, 0, n - 1), np.clip(j0 + 1, 0, n - 1), 2 * k - f, f


def upsample(l1, k, rows, cols, method):
    """up(L1) evaluated at plane rows x cols (index arrays)."""
    rows, cols = np.asarray(rows), np.asarray(cols)
    if method == "nearest":
        return l1[(rows // k)[:, None], (cols // k)[None, :]]
    if k == 1:
        return l1[rows[:, None], cols[None, :]]
    y0, y1, wy0, wy1 = _bilinear_axis(rows, k, l1.shape[0])
    x0, x1, wx0, wx1 = _bilinear_axis(cols, k, l1.shape[1])
    # Separable: interpolate the few L1 rows needed horizontally, then vertically.
    # Integer arithmetic, so this equals the 2-D weighted sum exactly.
    r0, r1 = min(y0.min(), y1.min()), max(y0.max(), y1.max()) + 1
    src = l1[r0:r1]
    hrow = src[:, x0] * wx0 + src[:, x1] * wx1
    acc = hrow[y0 - r0] * wy0[:, None] + hrow[y1 - r0] * wy1[:, None]
    shift = 2 * (2 * k).bit_length() - 2           # log2((2k)^2)
    return (acc + (1 << (shift - 1))) >> shift


# --- Construction B: reversible integer 5/3 lifting (JPEG 2000), per tile -------------

def _lift_idx(ns, nd):
    """Symmetric-extension neighbour indices for 5/3 lifting."""
    nxt = np.minimum(np.arange(nd) + 1, ns - 1)           # s[i+1] for each d[i]
    prv = np.maximum(np.arange(ns) - 1, 0)                # d[i-1] for each s[i]
    cur = np.minimum(np.arange(ns), nd - 1)               # d[i]   for each s[i]
    return nxt, prv, cur


def _fwd53(x, axis):
    """One level of reversible 5/3 lifting along axis. Returns (low, high)."""
    x = np.moveaxis(x, axis, 0)
    s, d = x[0::2].copy(), x[1::2].copy()
    if len(d):
        nxt, prv, cur = _lift_idx(len(s), len(d))
        d -= (s[:len(d)] + s[nxt]) >> 1
        s += (d[prv] + d[cur] + 2) >> 2
    return np.moveaxis(s, 0, axis), np.moveaxis(d, 0, axis)


def _inv53(s, d, axis):
    s, d = np.moveaxis(s, axis, 0).copy(), np.moveaxis(d, axis, 0).copy()
    if len(d):
        nxt, prv, cur = _lift_idx(len(s), len(d))
        s -= (d[prv] + d[cur] + 2) >> 2
        d += (s[:len(d)] + s[nxt]) >> 1
    x = np.empty((len(s) + len(d),) + s.shape[1:], s.dtype)
    x[0::2], x[1::2] = s, d
    return np.moveaxis(x, 0, axis)


def dwt53(tile, levels):
    """Mallat-layout coefficients (same shape as tile) and the LL band's shape."""
    c = tile.astype(np.int64).copy()
    h, w = c.shape
    for _ in range(levels):
        lo, hi = _fwd53(c[:h, :w], 1)
        c[:h, :w] = np.concatenate([lo, hi], 1)
        lo, hi = _fwd53(c[:h, :w], 0)
        c[:h, :w] = np.concatenate([lo, hi], 0)
        h, w = -(-h // 2), -(-w // 2)
    return c, (h, w)


def idwt53(c, levels):
    c = c.copy()
    shapes = [c.shape]
    for _ in range(levels):
        shapes.append((-(-shapes[-1][0] // 2), -(-shapes[-1][1] // 2)))
    for lev in range(levels, 0, -1):
        h, w = shapes[lev - 1]
        hl = shapes[lev][0]
        c[:h, :w] = _inv53(c[:hl, :w], c[hl:h, :w], 0)
        wl = shapes[lev][1]
        c[:h, :w] = _inv53(c[:h, :wl], c[:h, wl:w], 1)
    return c


# --- Cross-plane prediction for L2 (integer only, inside one tile) -------------------------
# Planes are coded in the order G1, G2, R, B. G1 is predicted by up(L1); G2 by the mean of its four
# diagonal G1 neighbours plus the low-pass G2-G1 offset from L1; R and B by the surrounding green
# plus the low-pass colour difference from L1. Neighbours are clamped to the tile, so tiles stay
# independent. Averaging several noisy neighbours keeps the prediction's own noise low.

CROSS_ORDER = (1, 2, 0, 3)          # G1, G2, R, B in plane indices


def _shift(a, dy, dx):
    h, w = a.shape
    return a[np.clip(np.arange(h) + dy, 0, h - 1)][:, np.clip(np.arange(w) + dx, 0, w - 1)]


def _neighbour_sum(src, src_off, dst_off):
    """Sum and count of the src-plane samples adjacent to each dst-plane site."""
    (sy, sx), (dy, dx) = src_off, dst_off
    acc, n = 0, 0
    for oy in ((-1, 1) if sy != dy else (0,)):
        for ox in ((-1, 1) if sx != dx else (0,)):
            acc = acc + _shift(src, (dy + oy - sy) // 2, (dx + ox - sx) // 2)
            n += 1
    return acc, n


def cross_predict(p, q, up, offsets):
    """Integer prediction of plane p for one tile from already-decoded planes q and up(L1)."""
    G1, G2 = 1, 2
    if p == G1:
        return up[G1]
    if p == G2:
        s, n = _neighbour_sum(q[G1], offsets[G1], offsets[G2])
        return (s + n * (up[G2] - up[G1]) + n // 2) // n
    s1, n1 = _neighbour_sum(q[G1], offsets[G1], offsets[p])
    s2, n2 = _neighbour_sum(q[G2], offsets[G2], offsets[p])
    den = 2 * n1 * n2
    return (s1 * n2 + s2 * n1 + den * up[p] - n1 * n2 * (up[G1] + up[G2]) + den // 2) // den


def tile_grid(shape, tile):
    h, w = shape
    if tile is None:
        return [(0, 0, 0, 0, h, w)]
    return [(ty, tx, ty * tile, tx * tile, min(tile, h - ty * tile), min(tile, w - tx * tile))
            for ty in range(-(-h // tile)) for tx in range(-(-w // tile))]


def tiles_touching(shape, tile, y0, x0, y1, x1):
    """Tiles that intersect plane rectangle [y0,y1) x [x0,x1), in row-major order.

    Enumerates only the touched tiles (O(K)), after clipping the rectangle to the plane.
    """
    h, w = shape
    y0, x0, y1, x1 = max(y0, 0), max(x0, 0), min(y1, h), min(x1, w)
    if y1 <= y0 or x1 <= x0:
        return []
    if tile is None:
        return [(0, 0, 0, 0, h, w)]
    return [(ty, tx, ty * tile, tx * tile, min(tile, h - ty * tile), min(tile, w - tx * tile))
            for ty in range(y0 // tile, (y1 - 1) // tile + 1) for tx in range(x0 // tile, (x1 - 1) // tile + 1)]
