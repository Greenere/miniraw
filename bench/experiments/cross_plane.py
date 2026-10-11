"""Cross-plane prediction for L2, measured in real JPEG XL bytes.

Per 512x512 tile (plane coordinates), integer-only and using only in-tile data plus L1:
  G1: q - up(L1)                         (as today)
  G2: q - round(mean of the 4 diagonal G1 neighbours + low-pass G2/G1 offset from L1)
  R, B: q - round(local green mean + low-pass colour difference from L1)
Compared with today's L2 (q - up(L1) on all planes), both coded with JPEG XL effort 7.

    python -m bench.experiments.cross_plane data/personal_raws [N]
"""
import sys
from pathlib import Path

import numpy as np

from miniraw import codec, entropy, io_raw, layers, vst
from bench.run import RAW_EXT


def clamp_shift(a, dy, dx):
    """a[r+dy, c+dx] with edge replication (keeps prediction inside the tile)."""
    h, w = a.shape
    r = np.clip(np.arange(h) + dy, 0, h - 1)
    c = np.clip(np.arange(w) + dx, 0, w - 1)
    return a[r][:, c]


def neighbour_mean(src, src_off, dst_off):
    """Mean (x4, integer) of the src-plane samples adjacent (diagonal or orthogonal) to dst sites."""
    sy, sx = src_off
    dy, dx = dst_off
    acc, n = 0, 0
    for oy in (-1, 1) if sy != dy else (0,):
        for ox in (-1, 1) if sx != dx else (0,):
            # mosaic neighbour (2r+dy+oy, 2c+dx+ox) lives at src plane index ((2r+dy+oy-sy)/2, ...)
            acc = acc + clamp_shift(src, (dy + oy - sy) // 2, (dx + ox - sx) // 2)
            n += 1
    return acc, n


def tile_residuals(qt, up, offsets):
    """qt, up: dicts plane -> tile arrays (int64). Returns residual tiles (int64)."""
    G1, G2, R, B = 1, 2, 0, 3
    res = {G1: qt[G1] - up[G1]}
    s, n = neighbour_mean(qt[G1], offsets[G1], offsets[G2])
    # low-pass G2-G1 offset from L1 at G2 sites, in units of n (integer arithmetic throughout)
    res[G2] = qt[G2] - ((s + n * (up[G2] - up[G1]) + n // 2) // n)
    for P in (R, B):
        s1, n1 = neighbour_mean(qt[G1], offsets[G1], offsets[P])
        s2, n2 = neighbour_mean(qt[G2], offsets[G2], offsets[P])
        num = s1 * n2 + s2 * n1                      # green mean x (2 n1 n2)
        den = 2 * n1 * n2
        gl = (up[G1] + up[G2])                       # low-pass green x 2
        res[P] = qt[P] - ((num + den * up[P] - n1 * n2 * gl + den // 2) // den)
    return res


def main(folder, n=4):
    files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in RAW_EXT)[:int(n)]
    ratios = {"P1": [], "P3": []}
    for f in files:
        raw = io_raw.read(f)
        q = []
        for k in range(4):
            _, T, _ = codec.fit_noise(raw.planes[k], raw.black[k], raw.white, diagnostics=False)
            q.append(vst.forward_table(T, 1.0, raw.white)[raw.planes[k]].astype(np.int64))
        pshape = q[0].shape
        k = layers.choose_k(pshape, 128)
        l1 = [layers.l1_from_q(qq, k) for qq in q]
        size = {("base", "P1"): 0, ("base", "P3"): 0, ("cross", "P1"): 0, ("cross", "P3"): 0}
        for ty, tx, y0, x0, th, tw in layers.tile_grid(pshape, 512):
            rows, cols = np.arange(y0, y0 + th), np.arange(x0, x0 + tw)
            qt = {p: q[p][y0:y0 + th, x0:x0 + tw] for p in range(4)}
            up = {p: layers.upsample(l1[p], k, rows, cols, "bilinear") for p in range(4)}
            base = {p: qt[p] - up[p] for p in range(4)}
            cross = tile_residuals(qt, up, raw.offsets)
            for name, r in (("base", base), ("cross", cross)):
                size[name, "P1"] += sum(len(entropy.jxl_encode(r[p], 7)) for p in range(4))
                size[name, "P3"] += len(entropy.jxl_encode(np.stack([r[p] for p in range(4)], -1), 7))
        npx = raw.planes.size
        for pk in ("P1", "P3"):
            ratios[pk].append(size["cross", pk] / size["base", pk])
        print(f"{f.name}: L2 today P3 {8 * size['base', 'P3'] / npx:.3f} bpp | cross-plane P1 "
              f"{8 * size['cross', 'P1'] / npx:.3f} ({(size['cross', 'P1'] / size['base', 'P3'] - 1) * 100:+.1f}%) "
              f"P3 {8 * size['cross', 'P3'] / npx:.3f} ({(size['cross', 'P3'] / size['base', 'P3'] - 1) * 100:+.1f}%)",
              flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
