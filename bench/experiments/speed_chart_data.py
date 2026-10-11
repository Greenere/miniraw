"""Encode time versus size for the README speed chart.

Every method starts from the same in-memory sensor data (file reading and raw decoding excluded) on the
same machine. Tiled codecs encode the four Bayer planes in 512x512 tiles on 8 threads, as MiniRaw does;
ZIP runs single-threaded like a zip tool. One warm-up file is encoded and discarded first.

    python -m bench.experiments.speed_chart_data data/personal_raws
Writes results/experiments/speed_<folder>.csv
"""
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import imagecodecs
import numpy as np
import pandas as pd

from miniraw import codec, container, io_raw, layers
from baselines import b5_sqrt
from bench.run import RAW_EXT

ROOT = Path(__file__).resolve().parents[2]
THREADS = 8


def tiled(planes, fn, packing="P1"):
    """Apply fn to every (tile, plane group) on THREADS threads; returns total bytes."""
    jobs = []
    for _, _, y0, x0, th, tw in layers.tile_grid(planes.shape[1:], 512):
        for g in layers.PACKINGS[packing]:
            t = planes[g, y0:y0 + th, x0:x0 + tw]
            jobs.append(np.ascontiguousarray(t[0] if len(g) == 1 else np.moveaxis(t, 0, -1)))
    with ThreadPoolExecutor(THREADS) as ex:
        return sum(ex.map(lambda a: len(fn(a)), jobs))


def methods(raw, fv=2):
    p = raw.planes
    mosaic = io_raw.unpack(p, raw.offsets)
    yield "ZIP / Deflate", "lossless", lambda: len(zlib.compress(mosaic.tobytes(), 6))
    yield "JPEG-LS lossless", "lossless", lambda: tiled(p, imagecodecs.jpegls_encode)
    for e in (4, 7):
        yield f"JPEG XL lossless, effort {e}", "lossless", \
            lambda e=e: tiled(p, lambda a: imagecodecs.jpegxl_encode(a, lossless=True, effort=e, numthreads=1), "P3")
    for e in (4, 7):
        yield f"MiniRaw, all layers, effort {e}", "miniraw-lossless", \
            lambda e=e: len(codec.encode(raw, effort=e, threads=THREADS, format_version=fv)[0])
        yield f"MiniRaw hot tier, effort {e}", "miniraw-hot", \
            lambda e=e: len(codec.encode(raw, effort=e, threads=THREADS, with_l3=False, format_version=fv)[0])
    yield "JPEG XL lossy, distance 0.1", "lossy", \
        lambda: tiled(p, lambda a: imagecodecs.jpegxl_encode(a, distance=0.1, effort=7, numthreads=1))
    yield "Square-root curve 9-bit + JPEG XL", "lossy", lambda: b5_sqrt_bytes(raw, 9)


def b5_sqrt_bytes(raw, bits):
    q = []
    for k in range(4):
        x = raw.planes[k]
        fwd, _ = b5_sqrt.tables(raw.black[k], raw.white, int(max(raw.white, int(x.max()))), bits)
        q.append(fwd[x].astype(np.uint16))
    q = np.stack(q)
    return tiled(q, lambda a: imagecodecs.jpegxl_encode(a, lossless=True, effort=7, numthreads=1), "P3")


def main(folder, out_name=None, fv=2):
    """out_name: CSV suffix (default: the folder name); fv: MiniRaw format version to time."""
    fv = int(fv)
    files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in RAW_EXT)
    warm = io_raw.read(files[0])
    for _, _, fn in methods(warm, fv):
        fn()
    import platform
    import subprocess
    cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                         text=True).stdout.strip() or platform.processor()
    rows = []
    for f in files:
        raw = io_raw.read(f)
        for name, kind, fn in methods(raw, fv):
            t = time.perf_counter()
            nbytes = fn()
            rows.append({"file": f.name, "method": name, "kind": kind, "bytes": nbytes,
                         "seconds": time.perf_counter() - t, "npx": raw.planes.size,
                         "file_bytes": f.stat().st_size, "unc_bytes": raw.planes.size * raw.bit_depth / 8, "cpu": cpu})
        print(f.name, flush=True)
    out = ROOT / "results" / "experiments" / f"speed_{out_name or Path(folder).name}.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    med = pd.DataFrame(rows).groupby(["method", "kind"])[["bytes", "seconds"]].median().reset_index()
    for _, r in med.sort_values("seconds").iterrows():
        print(f"  {r['method']:38s} {r['seconds']:6.2f} s   {r['bytes'] / 1e6:6.1f} MB")
    print("wrote", out)


if __name__ == "__main__":
    main(*sys.argv[1:4])
