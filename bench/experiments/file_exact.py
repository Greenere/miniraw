"""File-exact sizes: encode raw files with their original container kept, and restore them byte for byte.

    python -m bench.experiments.file_exact DIR [FORMAT_VERSION] [OUT_NAME]

For every raw file: the .mraw size with everything kept (sensor-exact layers + the original's container CTR and
its large previews SRC), the hot part, and whether decoding back to the original format returns the identical
file (SHA-256). Effort 7, as in the benchmark. Writes results/experiments/file_exact_<OUT_NAME or folder>.csv.
"""
import hashlib
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from miniraw import codec, container, io_raw
from bench.run import RAW_EXT

ROOT = Path(__file__).resolve().parents[2]


def main(folder, fv=2, out_name=None):
    fv = int(fv)
    rows = []
    for path in sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in RAW_EXT):
        raw = io_raw.read(path)
        t0 = time.perf_counter()
        data, _ = codec.encode(raw, effort=7, keep_original=True, format_version=fv, source={"file": path.name})
        enc_s = time.perf_counter() - t0
        r = container.RangeReader(data)
        s = container.layer_sizes(r.header)
        hot, _ = container.strip(data)
        restored_ok = None
        if r.has_original:
            restored = codec.Decoder(r).original()
            restored_ok = hashlib.sha256(restored).hexdigest() == hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append({"file": path.name, "format": r.version, "file_bytes": path.stat().st_size, "mraw_bytes": len(data),
                     "hot_bytes": len(hot), "CTR_bytes": s["CTR"], "SRC_bytes": s["SRC"], "IDX_bytes": s["IDX"],
                     "L1_bytes": s["L1"], "L2_bytes": s["L2"], "L3_bytes": s["L3"], "restorable": r.has_original,
                     "restored_identical": restored_ok, "encode_s": enc_s})
        print(path.name, f"{len(data) / 1e6:.1f} MB", "identical" if restored_ok else restored_ok, flush=True)
    df = pd.DataFrame(rows)
    out = ROOT / "results" / "experiments" / f"file_exact_{out_name or Path(folder).name}.csv"
    df.to_csv(out, index=False)
    print(f"median: file-exact {np.median(df.mraw_bytes) / 1e6:.1f} MB against {np.median(df.file_bytes) / 1e6:.1f} MB "
          f"originals; hot with container {np.median(df.hot_bytes) / 1e6:.1f} MB; CTR {np.median(df.CTR_bytes) / 1e6:.2f} MB, "
          f"SRC {np.median(df.SRC_bytes) / 1e6:.2f} MB; identical {int(df.restored_identical.fillna(False).sum())}/{len(df)}")
    print("wrote", out)


if __name__ == "__main__":
    main(*sys.argv[1:4])
