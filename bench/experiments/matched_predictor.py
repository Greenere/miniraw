"""Predictor-matched lossless baseline: is MiniRaw's lossless advantage over JPEG XL only its cross-plane predictor?

Codes the raw planes (no quantization) with JPEG XL lossless, with and without MiniRaw's cross-plane predictor
(layers.cross_predict, no L1 as in format 2), in per-plane (P1) and 4-channel (P3) packings. Same 512 tiles and
effort as the benchmark. The predicted variant is decoded and checked bit-exact. MiniRaw's own lossless size
(L1+L2+L3, the G1 numerator) is encoded fresh on the same file at the same effort.

    python -m bench.experiments.matched_predictor data/personal_raws [N]
Writes results/experiments/matched_predictor_<folder>.csv
"""
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from miniraw import codec, entropy, io_raw, layers
from bench.run import RAW_EXT, _sizes

ROOT = Path(__file__).resolve().parents[2]
CFG = tomllib.loads((ROOT / "configs" / "phase1.toml").read_text())
TILE, EFFORT = CFG["tile"], CFG["effort"]


def residuals(xt, offsets):
    """Cross-plane residuals of one tile of raw planes (R, G1, G2, B), with no L1 (up = 0)."""
    up = [np.zeros_like(xt[0])] * 4
    return [xt[p] - layers.cross_predict(p, xt, up, offsets) for p in range(4)]


def invert(res, offsets):
    """Undo residuals(): G1 first, then G2 from G1, then R and B from both greens."""
    up = [np.zeros_like(res[0])] * 4
    x = [None] * 4
    for p in (1, 2, 0, 3):
        x[p] = res[p] + layers.cross_predict(p, x, up, offsets)
    return x


def code(planes, offsets, predict, packing, check=False):
    """Total JPEG XL bytes over all tiles; with check, decodes and verifies the planes exactly."""
    groups = layers.PACKINGS[packing]

    def job(t):
        _, _, y0, x0, th, tw = t
        xt = [planes[p, y0:y0 + th, x0:x0 + tw].astype(np.int64) for p in range(4)]
        pay = residuals(xt, offsets) if predict else xt
        n, ok = 0, True
        dec = [None] * 4
        for g in groups:
            img = pay[g[0]] if len(g) == 1 else np.stack([pay[p] for p in g], -1)
            buf = entropy.jxl_encode(img, EFFORT)
            n += len(buf)
            if check:
                d = entropy.jxl_decode(buf)
                for i, p in enumerate(g):
                    dec[p] = d if len(g) == 1 else d[..., i]
        if check:
            back = invert(dec, offsets) if predict else dec
            ok = all(np.array_equal(back[p], xt[p]) for p in range(4))
        return n, ok

    with ThreadPoolExecutor(codec.default_threads()) as ex:
        out = list(ex.map(job, layers.tile_grid(planes.shape[1:], TILE)))
    return sum(n for n, _ in out), all(ok for _, ok in out)


def run_file(path):
    raw = io_raw.read(str(path))
    row = {"key": path.stem, "npx": int(raw.planes[0].size * 4)}
    for predict in (False, True):
        for packing in ("P1", "P3"):
            t = time.perf_counter()
            n, ok = code(raw.planes, raw.offsets, predict, packing, check=predict)
            name = f"jxl_{'cross' if predict else 'raw'}_{packing}"
            row[f"{name}_bytes"] = n
            row[f"{name}_s"] = time.perf_counter() - t
            if predict:
                row[f"{name}_exact"] = ok
    data, _ = codec.encode(raw, step=CFG["step"], tile=TILE, effort=EFFORT)
    s = _sizes(data)
    row["miniraw_L1L2L3_bytes"] = s.get("L1", 0) + s["L2"] + s["L3"]
    row["miniraw_L2_bytes"] = s["L2"]
    row["miniraw_L3_bytes"] = s["L3"]
    return row


def main(folder, limit=None):
    folder = Path(folder)
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in RAW_EXT)[:limit]
    rows = []
    for f in files:
        rows.append(run_file(f))
        r = rows[-1]
        b2 = r["jxl_raw_P3_bytes"]
        print(f"{r['key']}: " + "  ".join(f"{k[:-6]} {v / b2:.4f}" for k, v in r.items() if k.endswith("_bytes")),
              f"exact {r['jxl_cross_P1_exact'] and r['jxl_cross_P3_exact']}", flush=True)
    out = ROOT / "results" / "experiments" / f"matched_predictor_{folder.name}.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None)
