"""Format 2 against format 1 on the same images (paired), from per-image CSVs.

    python -m bench.experiments.v2_compare V1_DIR V2_DIR [V1_DIR V2_DIR ...]

Each pair names two directories under results/per_image run on the same split (e.g. holdout2_v1 holdout2_v2).
Prints markdown: per-image ratios v2 / v1 with 95% paired-bootstrap intervals (linear sources), and per-format
medians of the checks.
"""
import sys

import numpy as np

from bench.experiments.heldout_summary import load
from bench.report import boot_median_ci


def ci(v, fmt="{:.4f}"):
    m, lo, hi = boot_median_ci(v)
    return f"{fmt.format(m)} [{fmt.format(lo)}, {fmt.format(hi)}]"


def compare(d1, d2):
    a, b = load(d1).set_index("key"), load(d2).set_index("key")
    keys = a.index.intersection(b.index)
    a, b = a.loc[keys], b.loc[keys]
    allb, alla = load(d2, linear=False), load(d1, linear=False)
    out = [f"### {d1} against {d2}: {len(keys)} linear images, {a.camera.nunique()} cameras\n",
           "| Measure (v2 / v1, per image) | Median [95% CI] |", "| --- | --- |",
           f"| Hot tier (L0 + IDX + L1 + L2) | {ci(b.hot / a.hot)} |",
           f"| L2 alone | {ci(b.L2_bytes / a.L2_bytes)} |",
           f"| All layers (L1 + L2 + L3) | {ci((b.L1_bytes + b.L2_bytes + b.L3_bytes) / (a.L1_bytes + a.L2_bytes + a.L3_bytes))} |",
           "", "| Check | v1 | v2 |", "| --- | --- | --- |",
           f"| Hot tier / JPEG XL lossless (median) | {np.median(a.hot / a.B2_bytes):.3f} | {np.median(b.hot / b.B2_bytes):.3f} |",
           f"| All layers / JPEG XL lossless (median) | {np.median((a.L1_bytes + a.L2_bytes + a.L3_bytes) / a.B2_bytes):.3f} | "
           f"{np.median((b.L1_bytes + b.L2_bytes + b.L3_bytes) / b.B2_bytes):.3f} |",
           f"| Hot tier / camera file (median) | {np.median(a.hot / a.B1_bytes):.3f} | {np.median(b.hot / b.B1_bytes):.3f} |",
           f"| L1 (median KB) | {np.median(a.L1_bytes) / 1e3:.0f} | {np.median(b.L1_bytes) / 1e3:.0f} |",
           f"| IDX (median KB) | — | {np.median(b.IDX_bytes) / 1e3:.0f} |",
           f"| Exact decodes matching the hash (all images) | {int((alla.exact_ok.astype(str) == 'True').sum())}/{len(alla)} | "
           f"{int((allb.exact_ok.astype(str) == 'True').sum())}/{len(allb)} |",
           f"| Pixels outside the bound, step 1.0 (all images) | {int(alla.bound_violations.sum()):,} | {int(allb.bound_violations.sum()):,} |",
           f"| Pixels outside the bound, step 2.0 (all images) | {int(alla['step2.0_bound_violations'].sum()):,} | "
           f"{int(allb['step2.0_bound_violations'].sum()):,} |",
           f"| Worst pixel, step 1.0 (median) | {np.median(a.stab_maxerr):.3f}σ | {np.median(b.stab_maxerr):.3f}σ |",
           f"| Decile bias (median of max) | {np.median(a.bias_max):.3f} | {np.median(b.bias_max):.3f} |",
           f"| Stabilized RMSE (median / max) | {np.median(a.stab_rmse):.3f} / {a.stab_rmse.max():.3f} | "
           f"{np.median(b.stab_rmse):.3f} / {b.stab_rmse.max():.3f} |",
           f"| Encode time, all layers (median s) | {np.median(a.encode_s):.2f} | {np.median(b.encode_s):.2f} |", ""]
    return "\n".join(out)


if __name__ == "__main__":
    args = sys.argv[1:]
    for i in range(0, len(args), 2):
        print(compare(args[i], args[i + 1]))
