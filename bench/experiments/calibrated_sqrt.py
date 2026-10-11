"""Calibrated square-root baseline (Bernstein et al. 2010 style) against MiniRaw's hot tier, at matched steps.

Baseline: the per-plane affine noise fit (noise.estimate, no validation and no D26 lowering), the generalized
Anscombe transform T, a uniform quantizer q = round(T / step) (codes re-indexed to be contiguous), no white-level
or D27 bin rules. Two reconstructions: "inv" (T^-1(q * step), the textbook inverse) and "mid" (middle DN of the
bin, as MiniRaw's floor rule). Codes are JPEG XL lossless with MiniRaw's cross-plane predictor (P1), same tiles
and effort, so only quantization differs. MiniRaw is the shipped codec (format 2, rec "csafe").

Errors are reported in two noise units, since neither is ground truth: "mr" (MiniRaw's final model, as the
benchmark uses) and "aff" (the raw affine fit the baseline uses). Bound violations are each method's own bound,
|T(x) - T(rec)| <= step/2 + 1 DN, in the units that method quantized in.

    python -m bench.experiments.calibrated_sqrt data/personal_raws [N]
Writes results/experiments/calibrated_sqrt_<folder>.csv (one row per file, method and step).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from miniraw import codec, container, io_raw, noise, vst
from bench import metrics
from bench.experiments.matched_predictor import CFG, EFFORT, TILE, code
from bench.run import RAW_EXT, _sizes

ROOT = Path(__file__).resolve().parents[2]
STEPS = CFG["steps"]


def baseline_tables(T, step):
    """Contiguous code per DN and the two reconstruction tables (indexed by code)."""
    k = np.rint(T / step).astype(np.int64)
    used, fwd = np.unique(k, return_inverse=True)
    x = np.arange(len(T), dtype=np.float64)
    rec_inv = np.clip(np.rint(np.interp(used * step, T, x)), 0, len(T) - 1).astype(np.int64)
    _, rec_mid = vst.tables_from_lo(vst.lo_from_forward(fwd), len(T) - 1)
    return fwd, {"inv": rec_inv, "mid": rec_mid}


def score(raw, dec, refs):
    """Errors of decoded planes in each reference's noise units, aggregated as bench.run does."""
    row = {}
    for name, (Ts, models) in refs.items():
        rmse_sq, maxerr, bias = 0.0, 0.0, 0.0
        for p in range(4):
            r, m = metrics.stabilized_errors(raw.planes[p], dec[p], Ts[p])
            rmse_sq += r * r / 4
            maxerr = max(maxerr, m)
            bias = max(bias, metrics.bias_by_decile(raw.planes[p], dec[p], models[p]))
        row.update({f"rmse_{name}": float(np.sqrt(rmse_sq)), f"maxerr_{name}": maxerr, f"bias_{name}": bias})
    return row


def run_file(path):
    raw = io_raw.read(str(path))
    xmax = [int(max(raw.white, int(raw.planes[p].max()))) for p in range(4)]
    aff = [noise.estimate(raw.planes[p], raw.black[p], raw.white)[0] for p in range(4)]
    T_aff = [vst.T_table(aff[p], xmax[p]) for p in range(4)]
    rows, mr = [], None
    for step in STEPS:
        data, info = codec.encode(raw, step=step, tile=TILE, effort=EFFORT)
        pi = info["planes"]
        if mr is None:     # MiniRaw's model does not depend on the step
            mr = ([p["T"] for p in pi], [p["model"] for p in pi])
        refs = {"mr": mr, "aff": (T_aff, aff)}
        s = _sizes(data)
        d = codec.Decoder(container.RangeReader(data))
        dec = d.planes("signal")
        viol = sum(metrics.bound_violations(raw.planes[p], pi[p]["T"], pi[p]["fwd"], d.rec_floor[p], step,
                                            rec_hi=d.rec_floor[p] + d.rec_bits[p]) for p in range(4))
        rows.append({"key": path.stem, "method": "miniraw", "step": step,
                     "bytes": s.get("L1", 0) + s["L2"], "hot_bytes": s["L0"] + s.get("L1", 0) + s["L2"],
                     "violations_own": viol, **score(raw, dec, refs)})
        tabs = [baseline_tables(T_aff[p], step) for p in range(4)]
        q = np.stack([tabs[p][0][raw.planes[p]] for p in range(4)])
        nbytes, ok = code(q, raw.offsets, predict=True, packing="P1", check=True)
        assert ok
        for rname in ("inv", "mid"):
            dec = [tabs[p][1][rname][q[p]] for p in range(4)]
            viol = sum(metrics.bound_violations(raw.planes[p], T_aff[p], tabs[p][0], tabs[p][1][rname], step)
                       for p in range(4))
            rows.append({"key": path.stem, "method": f"calsqrt_{rname}", "step": step, "bytes": nbytes,
                         "violations_own": viol, **score(raw, dec, refs)})
    return rows


def main(folder, limit=None):
    folder = Path(folder)
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in RAW_EXT)[:limit]
    rows = []
    for f in files:
        rows += run_file(f)
        df = pd.DataFrame([r for r in rows if r["key"] == f.stem])
        print(f.stem)
        print(df.drop(columns=["key", "hot_bytes"]).assign(MB=df.bytes / 1e6).drop(columns="bytes")
              .to_string(index=False, float_format="%.3f"), flush=True)
    out = ROOT / "results" / "experiments" / f"calibrated_sqrt_{folder.name}.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None)
