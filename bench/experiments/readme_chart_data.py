"""Extra comparison points for the README charts, on a folder of raw files.

Adds ZIP/Deflate, JPEG-LS lossless and JPEG XL lossy (distance sweep) to the
per-image benchmark CSVs. Errors are in noise-sigma units using each image's
fitted noise model, like the rest of the benchmark.

    python -m bench.experiments.readme_chart_data data/personal_raws
Writes results/experiments/readme_<folder>.csv
"""
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import imagecodecs
import numpy as np
import pandas as pd

from miniraw import codec, io_raw, layers
from bench import metrics
from bench.run import RAW_EXT

DISTANCES = (0.05, 0.1, 0.2, 0.3, 0.5, 1.0)
ROOT = Path(__file__).resolve().parents[2]


def jxl_lossy(planes, distance, tile=512, effort=7):
    """Lossy JPEG XL on each raw plane (16-bit grayscale tiles); returns (bytes, decoded planes)."""
    out = np.empty_like(planes)
    jobs = [(p, t) for p in range(4) for t in layers.tile_grid(planes.shape[1:], tile)]

    def run(job):
        p, (_, _, y0, x0, th, tw) = job
        b = imagecodecs.jpegxl_encode(np.ascontiguousarray(planes[p, y0:y0 + th, x0:x0 + tw]),
                                      distance=distance, effort=effort, numthreads=1)
        out[p, y0:y0 + th, x0:x0 + tw] = imagecodecs.jpegxl_decode(b, numthreads=1)
        return len(b)

    with ThreadPoolExecutor(8) as ex:
        return sum(ex.map(run, jobs)), out


def main(folder):
    folder = Path(folder)
    rows = []
    for path in sorted(p for p in folder.iterdir() if p.suffix.lower() in RAW_EXT):
        raw = io_raw.read(path)
        T = [codec.fit_noise(raw.planes[p], raw.black[p], raw.white, diagnostics=False)[1] for p in range(4)]
        mosaic = io_raw.unpack(raw.planes, raw.offsets)
        row = {"key": path.stem, "npx": mosaic.size, "file_bytes": path.stat().st_size,
               "zip_bytes": len(zlib.compress(mosaic.astype("<u2").tobytes(), 6)),
               "jpegls_bytes": sum(len(imagecodecs.jpegls_encode(np.ascontiguousarray(p))) for p in raw.planes)}
        for d in DISTANCES:
            nb, dec = jxl_lossy(raw.planes, d)
            # lossy JPEG XL can overshoot the 14-bit range slightly; raw pipelines clip at the white level
            dec = np.minimum(dec, max(raw.white, int(raw.planes.max())))
            e = [metrics.stabilized_errors(raw.planes[p], dec[p], T[p]) for p in range(4)]
            row[f"B4_{d}_bytes"] = nb
            row[f"B4_{d}_stab_rmse"] = float(np.sqrt(np.mean([x[0] ** 2 for x in e])))
            row[f"B4_{d}_stab_maxerr"] = max(x[1] for x in e)
        rows.append(row)
        print(path.name, flush=True)
    out = ROOT / "results" / "experiments" / f"readme_{folder.name}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1])
