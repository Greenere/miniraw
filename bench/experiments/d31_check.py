"""D31 verification: bins trimmed instead of halved where the middle DN misses the error bound.

tables   Old (D27 halving, kept below as the reference) against new (vst.forward_table) quantization tables on every
         plane and step: planes changed, code entropy change (a rate proxy), pixels outside the bound. Fast, no
         JPEG XL. Steps 0.5 and 1.0 should be unchanged everywhere.
compare  Two bench.run result folders (e.g. holdout_v2 from D27, holdout_v2_d31): hot-tier size per step, images
         changed, bound violations, and images whose hot tier grows from step 1.5 to 2.0.

    python -m bench.experiments.d31_check tables dev test holdout holdout2 data/personal_raws
    python -m bench.experiments.d31_check compare holdout_v2 holdout_v2_d31
Writes results/experiments/d31_tables.csv (tables mode).
"""
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from miniraw import codec, io_raw, vst
from bench.datasets import path_for, read_split
from bench.run import RAW_EXT

ROOT = Path(__file__).resolve().parents[2]
STEPS = (0.5, 1.0, 1.5, 2.0)


def forward_table_d27(T, step, white=None):
    """vst.forward_table as of v2.0.0: failing bins halved, repeatedly."""
    inc = np.minimum(1.0, np.diff(T) / step)
    if white is not None and white < len(T):
        inc[white - 1:] = 1.0
    q = np.floor(np.concatenate([[0.0], np.cumsum(inc)]) + 0.5).astype(np.int64)
    for _ in range(32):
        lo = vst.lo_from_forward(q)
        n, rec = vst.tables_from_lo(lo, len(q) - 1)
        bad = np.unique(q[vst.bound_check(T, q, rec, step) > 1e-9])
        bad = bad[n[bad] > 1]
        if not len(bad):
            break
        q = vst.forward_from_lo(np.sort(np.concatenate([lo, lo[bad] + n[bad] // 2])), len(q) - 1)
    return q


def entropy(q, hist):
    c = np.bincount(q, weights=hist)
    c = c[c > 0] / c.sum()
    return float(-(c * np.log2(c)).sum())


def table_rows(item):
    group, path = item
    try:
        raw = io_raw.read(str(path))
    except Exception as e:   # noqa: BLE001 - report unreadable files, keep going
        return [{"group": group, "key": Path(path).stem, "error": f"{type(e).__name__}: {e}"[:120]}]
    rows = []
    for p in range(4):
        x = raw.planes[p]
        _, T, _ = codec.fit_noise(x, raw.black[p], raw.white, raw.noise_profile, p, False)
        hist = np.bincount(x.ravel(), minlength=len(T))
        for step in STEPS:
            old = forward_table_d27(T, step, raw.white)
            new = vst.forward_table(T, step, raw.white).astype(np.int64)
            _, rec = vst.tables_from_lo(vst.lo_from_forward(new), len(T) - 1)
            rows.append({"group": group, "key": Path(path).stem, "plane": p, "step": step,
                         "identical": bool(np.array_equal(old, new)), "bins_old": int(old[-1]) + 1,
                         "bins_new": int(new[-1]) + 1, "dH_bits": entropy(new, hist) - entropy(old, hist),
                         "violations_new": int(hist[vst.bound_check(T, new, rec, step) > 1e-9].sum())})
    return rows


def tables(groups):
    items = []
    for g in groups:
        if Path(g).is_dir():
            items += [(Path(g).name, p) for p in sorted(Path(g).iterdir()) if p.suffix.lower() in RAW_EXT]
        else:
            keys = read_split(g)
            missing = [k for k in keys if not path_for(k).exists()]
            if missing:
                print(f"{g}: {len(missing)} of {len(keys)} files missing; run python -m bench.datasets --split {g}")
            items += [(g, path_for(k)) for k in keys if path_for(k).exists()]
    print(f"{len(items)} images", flush=True)
    with ProcessPoolExecutor(6) as ex:
        df = pd.DataFrame([r for rows in ex.map(table_rows, items) for r in rows])
    out = ROOT / "results" / "experiments" / "d31_tables.csv"
    df.to_csv(out, index=False)
    if "error" in df:
        bad = df[df.error.notna()]
        df = df[df.error.isna()]
        for _, r in bad.iterrows():
            print(f"unreadable: {r.group}/{r.key}: {r.error}")
    print(f"{df.key.nunique()} images, {df.groupby(['key', 'plane']).ngroups} planes")
    for step, g in df.groupby("step"):
        ch = g[~g.identical]
        line = (f"step {step}: planes changed {len(ch)}/{len(g)} in {ch.key.nunique()} images; "
                f"bins {int(ch.bins_old.sum())} -> {int(ch.bins_new.sum())}; violations {int(g.violations_new.sum())}")
        if len(ch):
            line += f"; entropy change median {ch.dH_bits.median():+.3f}, range [{ch.dH_bits.min():+.3f}, {ch.dH_bits.max():+.3f}] bits/px"
        print(line)
    print("wrote", out)


def compare(old_name, new_name):
    def load(name):
        files = sorted((ROOT / "results" / "per_image" / name).glob("*.csv"))
        df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
        df = df[df.status == "ok"].set_index("key")
        df["step1.0_L0L1L2_bytes"] = df.L0_bytes + df.L1_bytes + df.L2_bytes
        df["step1.0_bound_violations"] = df.bound_violations
        return df

    a, b = load(old_name), load(new_name)
    keys = a.index.intersection(b.index)
    print(f"{old_name}: {len(a)} images, {new_name}: {len(b)} images, {len(keys)} in both")
    a, b = a.loc[keys], b.loc[keys]
    for s in STEPS:
        col = f"step{s}_L0L1L2_bytes"
        r = b[col] / a[col]
        vcol = f"step{s}_bound_violations"
        print(f"step {s}: hot tier new/old median {r.median():.4f}, range [{r.min():.4f}, {r.max():.4f}]; "
              f"images changed {int((b[col] != a[col]).sum())}; violations {int(a[vcol].sum())} -> {int(b[vcol].sum())}")
    for name, d in ((old_name, a), (new_name, b)):
        up = d[d["step2.0_L0L1L2_bytes"] > d["step1.5_L0L1L2_bytes"]].index.tolist()
        print(f"{name}: hot tier larger at step 2.0 than 1.5 in {len(up)} images {up}")


if __name__ == "__main__":
    mode, args = sys.argv[1], sys.argv[2:]
    if mode == "tables":
        tables(args)
    elif mode == "compare":
        compare(*args)
    else:
        sys.exit(__doc__)
