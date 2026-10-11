"""Dev experiment: JPEG XL plane packing and effort, applied equally to MiniRaw L2 (q) and B2 (raw).

    python -m bench.experiments.packing [--effort9]
Writes results/experiments/packing.csv.
"""
import argparse
import time
from multiprocessing import Pool
from pathlib import Path

import imagecodecs
import numpy as np
import pandas as pd

from miniraw import codec, io_raw, layers, vst
from bench.datasets import camera_of, path_for, read_split

OUT = Path(__file__).resolve().parents[2] / "results" / "experiments" / "packing.csv"
# plane order R, G1, G2, B
PACKINGS = {
    "P1": [[0], [1], [2], [3]],
    "P2": [[0, 1, 3], [2]],          # spec P2: RGB = (R, G1, B) + G2
    "P2g": [[1, 0, 3], [2]],         # G1 first so it predicts R and B
    "P3": [[0, 1, 2, 3]],            # 4-channel (RGBA)
    "PG": [[1, 2], [0], [3]],        # greens together, R and B alone
}


def jxl_bytes(planes, groups, tile, effort):
    total = 0
    for _, _, y0, x0, th, tw in layers.tile_grid(planes.shape[1:], tile):
        for g in groups:
            t = planes[g, y0:y0 + th, x0:x0 + tw]
            img = t[0] if len(g) == 1 else np.moveaxis(t, 0, -1)
            total += len(imagecodecs.jpegxl_encode(np.ascontiguousarray(img.astype(np.uint16)),
                                                   lossless=True, effort=effort, numthreads=1))
    return total


def run(args):
    key, effort9 = args
    raw = io_raw.read(path_for(key))
    q = []
    for p in range(4):
        x = raw.planes[p]
        _, T, _ = codec.fit_noise(x, raw.black[p], raw.white)
        q.append(vst.forward_table(T, 1.0, raw.white)[x])
    q = np.stack(q)
    row = {"key": key, "camera": camera_of(key), "npx": raw.planes.size,
           "lossy_source": io_raw.histogram_gaps(raw.planes, raw.black, raw.white) > io_raw.GAP_THRESHOLD}
    for name, groups in PACKINGS.items():
        row[f"L2_{name}"] = jxl_bytes(q, groups, 512, 7)
        row[f"B2_{name}"] = jxl_bytes(raw.planes, groups, 512, 7)
    if effort9:
        t = time.time(); row["L2_P1_e9"] = jxl_bytes(q, PACKINGS["P1"], 512, 9); row["L2_e9_s"] = time.time() - t
        t = time.time(); row["L2_P1_e7_s"] = (jxl_bytes(q, PACKINGS["P1"], 512, 7), time.time() - t)[1]
        row["B2_P1_e9"] = jxl_bytes(raw.planes, PACKINGS["P1"], 512, 9)
    print(key, flush=True)
    return row


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--effort9", action="store_true")
    a = ap.parse_args()
    keys = read_split("dev")
    with Pool(4) as pool:
        rows = pool.map(run, [(k, a.effort9) for k in keys])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)
    c = df[~df.lossy_source]
    print(f"\nlinear sources n={len(c)}; median ratio vs P1 (lower is better)")
    for name in PACKINGS:
        print(f"  {name:4s} L2 {np.median(c[f'L2_{name}'] / c.L2_P1):.4f}   B2 {np.median(c[f'B2_{name}'] / c.B2_P1):.4f}")
    if a.effort9:
        print(f"  e9   L2 {np.median(c.L2_P1_e9 / c.L2_P1):.4f}   B2 {np.median(c.B2_P1_e9 / c.B2_P1):.4f}   "
              f"time x{np.median(c.L2_e9_s / c.L2_P1_e7_s):.1f}")
