"""Stage-by-stage timing of encode and decode on one file (single thread unless noted).

    python -m bench.experiments.profile_stages FILE
"""
import sys
import time
from contextlib import contextmanager

import numpy as np

from miniraw import codec, container, entropy, io_raw, layers, noise, vst

T = {}


@contextmanager
def timed(name):
    t = time.perf_counter()
    yield
    T[name] = T.get(name, 0.0) + time.perf_counter() - t


def main(path):
    with timed("read raw (rawpy/LibRaw)"):
        raw = io_raw.read(path)
    pshape = raw.planes.shape[1:]
    k = layers.choose_k(pshape, 128)
    grid = layers.tile_grid(pshape, 512)
    qs, l1s, ds, kaps = [], [], {}, []
    for p in range(4):
        x = raw.planes[p]
        b, white = raw.black[p], raw.white
        xmax = int(max(white, int(x.max())))
        with timed("noise: block stats + fit"):
            model, (xs, vs, ws) = noise.estimate(x, b, white)
        with timed("noise: validation (+isotonic refit)"):
            Tt = vst.T_table(model, xmax)
            _, sig, _ = noise.stabilized_sigma_by_bin(x, model, white, Tt)
            if not np.all((sig >= 0.9) & (sig <= 1.1)):
                model = noise.isotonic_model(xs, vs, ws, b, xmax)
                Tt = vst.T_table(model, xmax)
                noise.stabilized_sigma_by_bin(x, model, white, Tt)
        with timed("tables + q lookup"):
            fwd = vst.forward_table(Tt, 1.0, white)
            lo = vst.lo_from_forward(fwd); n, rec = vst.tables_from_lo(lo, xmax)
            q = fwd[x]
        with timed("L1 floor-mean"):
            l1 = layers.l1_from_q(q, k)
        with timed("upsample + residual"):
            for ty, tx, y0, x0, th, tw in grid:
                up = layers.upsample(l1, k, np.arange(y0, y0 + th), np.arange(x0, x0 + tw), "bilinear")
                ds[p, ty, tx] = q[y0:y0 + th, x0:x0 + tw] - up
        with timed("L3: kappa + range coding"):
            for ty, tx, y0, x0, th, tw in grid:
                qt = q[y0:y0 + th, x0:x0 + tw]
                kaps.append(entropy.kappa_encode(x[y0:y0 + th, x0:x0 + tw].astype(np.int64) - lo[qt], n[qt]))
        with timed("JPEG XL L1 encode"):
            entropy.jxl_encode(l1, 7)
        qs.append(q)
    with timed("JPEG XL L2 encode (effort 7, 1 thread)"):
        for ty, tx, *_ in grid:
            entropy.jxl_encode(np.stack([ds[p, ty, tx] for p in range(4)], -1), 7)
    with timed("sha256 of mosaic"):
        codec.mosaic_sha256(raw.planes, raw.offsets)
    enc_total = sum(T.values()) - T["read raw (rawpy/LibRaw)"]
    print(f"ENCODE ({raw.planes.size / 1e6:.1f} MP)")
    for k_, v in sorted(T.items(), key=lambda kv: -kv[1]):
        print(f"  {v:6.2f} s  {k_}")
    print(f"  {enc_total:6.2f} s  total excluding raw read")

    # Decode stages
    data, _ = codec.encode(raw)
    T.clear()
    r = container.RangeReader(data)
    dec = codec.Decoder(r)
    with timed("JPEG XL L1+L2 decode"):
        l2 = {}
        for p in range(4):
            dec.l1(p)
        for t in grid:
            l2[t[:2]] = entropy.jxl_decode(r.section("L2", 0, t[0], t[1]))
    with timed("upsample + add"):
        qd = {}
        for t in grid:
            ty, tx, y0, x0, th, tw = t
            for p in range(4):
                qd[p, ty, tx] = l2[ty, tx][..., p] + layers.upsample(dec.l1(p), k, np.arange(y0, y0 + th),
                                                                      np.arange(x0, x0 + tw), "bilinear")
    with timed("signal: rec lookup"):
        for (p, ty, tx), q in qd.items():
            dec.tables[p][2][q]
    with timed("exact: L3 range decode + lo lookup"):
        for (p, ty, tx), q in qd.items():
            lo, n, _ = dec.tables[p]
            lo[q] + entropy.kappa_decode(r.section("L3", p, ty, tx), n[q])
    print("DECODE")
    for k_, v in sorted(T.items(), key=lambda kv: -kv[1]):
        print(f"  {v:6.2f} s  {k_}")
    for th in (1, 8):
        for eff in (1, 3, 7):
            entropy.JXL_THREADS = th
            t = time.perf_counter(); tot = 0
            for ty, tx, *_ in grid:
                tot += len(entropy.jxl_encode(np.stack([ds[p, ty, tx] for p in range(4)], -1), eff))
            te = time.perf_counter() - t
            t = time.perf_counter()
            for t_ in grid:
                pass
            print(f"JPEG XL L2 effort {eff}, {th} threads: {te:6.2f} s, {tot / 1e6:.2f} MB")


if __name__ == "__main__":
    main(sys.argv[1])
