"""Aggregate per-image CSVs into results/report.md with gate verdicts (spec 2, 8.7, 12).

    python -m bench.report --split dev
"""
import argparse
import json
import platform
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from miniraw import entropy

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
N_BOOT = 10_000


def boot_median_ci(v, seed=0):
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return np.nan, np.nan, np.nan
    rng = np.random.default_rng(seed)
    meds = np.median(v[rng.integers(0, len(v), (N_BOOT, len(v)))], axis=1)
    return float(np.median(v)), float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def verdict(point, lo, hi, thr, le=True):
    ok = point <= thr if le else point >= thr
    straddle = (lo <= thr <= hi)
    if straddle:
        return "marginal (" + ("pass" if ok else "fail") + ")"
    return "PASS" if ok else "FAIL"


def bpp(df, col):
    return 8 * df[col] / df["npx"]


def matched_b5(row, steps, metric="stab_rmse"):
    """MiniRaw hot-tier bytes at each B5 point's distortion (log-log interpolation over
    the step sweep, linear extrapolation outside it). Returns (bits, B5/MiniRaw, dist, inside)."""
    pts = []
    for st in steps:
        if st == 1.0:
            b, r = row["L0_bytes"] + row["L1_bytes"] + row["L2_bytes"], row[metric]
        else:
            b, r = row.get(f"step{st}_L0L1L2_bytes"), row.get(f"step{st}_{metric}")
        if pd.notna(b) and pd.notna(r):
            pts.append((np.log(r), np.log(b)))
    pts.sort()
    lr, lb = np.array(pts).T
    slope, icpt = np.polyfit(lr, lb, 1)
    out = []
    for bits in range(4, 16):
        c = f"B5_{bits}_bytes"
        if c in row and pd.notna(row[c]):
            r = row[f"B5_{bits}_{metric}"]
            inside = lr.min() <= np.log(r) <= lr.max()
            mb = np.exp(np.interp(np.log(r), lr, lb)) if inside else np.exp(slope * np.log(r) + icpt)
            out.append((bits, row[c] / mb, r, inside))
    return out


def env_block():
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                         stderr=subprocess.DEVNULL).decode().strip()
        if subprocess.check_output(["git", "status", "--porcelain", "--", "src", "bench", "configs"], cwd=ROOT,
                                   stderr=subprocess.DEVNULL).strip():
            commit += " + uncommitted changes to src/bench/configs"
    except Exception:
        commit = "uncommitted"
    from importlib.metadata import version
    import imagecodecs, numpy, rawpy, scipy
    cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                         text=True).stdout.strip() or platform.processor()
    return (f"- Commit: `{commit}`; Python {platform.python_version()}; {platform.platform()}; CPU {cpu}\n"
            f"- {entropy.jxl_version()} via imagecodecs {imagecodecs.__version__}; rawpy {rawpy.__version__}; "
            f"numpy {numpy.__version__}; scipy {scipy.__version__}; constriction {version('constriction')}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--out", default=str(RESULTS / "report.md"))
    a = ap.parse_args()
    files = sorted((RESULTS / "per_image" / a.split).glob("*.csv"))
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    skipped = df[df.status != "ok"]
    df = df[df.status == "ok"].copy()
    for c in ["lossy_source", "exact_ok", "region_exact_ok", "region_reads_ok", "crop1024_reads_ok",
              "strip_signal_ok", "sidecar_exact_ok"]:
        df[c] = df[c].astype(str) == "True"
    df["hot"] = df.L0_bytes + df.L1_bytes + df.L2_bytes
    df["r_G1"] = (df.L1_bytes + df.L2_bytes + df.L3_bytes) / df.B2_bytes
    df["r_G2"] = df.hot / df.B2_bytes
    df["r_G3"] = (df.L1_bytes + df.L2_bytes) / df.L2_direct_bytes
    df["r_G4"] = (df.L1_bytes + df.L2_bytes) / df.L1L2_untiled_bytes - 1
    g3_variants = [v for v in ["mean_bilinear", "mean_nearest", "wavelet_bilinear"] if f"L1L2_{v}_bytes" in df]
    for v in g3_variants:
        df[f"r_G3_{v}"] = df[f"L1L2_{v}_bytes"] / df.L2_direct_bytes
    df["r_L3_actual_vs_ideal"] = df.L3_bytes / df.L3_ideal_bytes
    clean = df[~df.lossy_source]
    synth_p = RESULTS / "per_image" / f"synth_{a.split}.csv"
    syn = pd.read_csv(synth_p) if synth_p.exists() else None

    L = []
    L.append(f"# MiniRaw Phase 1 report ({a.split} split)\n")
    note = {"dev": "This is the **dev** split: tuning happens here, so these are not the frozen test-split verdicts.",
            "test": "This is the **test** split re-run after the D26 noise-model fix, which was designed after its "
                    "first run exposed failures, so it is not a clean held-out result. The first, frozen run is "
                    "in `results/report_test_pre_d26.md`.",
            "holdout": "This is the **holdout** split: 60 FiveK images first seen in this run, with the "
                       "configuration frozen after D26. It is the clean held-out result."}.get(a.split, "")
    L.append(f"Generated by `python -m bench.report`; every number traces to `results/per_image/`. {note}\n")
    L.append("## 1. Environment\n")
    L.append(env_block())
    L.append(f"- Images: {len(df)} encoded, {len(skipped)} skipped; {int(df.lossy_source.sum())} flagged as "
             f"processed in camera (histogram gaps > 5%, D8), excluded from the gate table per pitfall 2.\n")

    L.append("## 2. Gates (linear-source images, n = %d)\n" % len(clean))
    L.append("Median of per-image ratios, 95% paired-bootstrap CI over images (10,000 resamples, seed 0).\n")
    L.append("| Gate | Measure | Median | 95% CI | Threshold | Verdict |\n|---|---|---|---|---|---|")
    rows = [
        ("G1", "(L1+L2+L3) / B2", clean.r_G1, 1.03, True),
        ("G2", "(L0+L1+L2) / B2", clean.r_G2, 0.50, True),
        ("G3", "(L1+L2) / L2 direct", clean.r_G3, 1.03, True),
        ("G4", "tiling overhead (tiled/untiled - 1)", clean.r_G4, 0.05, True),
    ]
    for g, name, v, thr, le in rows:
        m, lo, hi = boot_median_ci(v)
        L.append(f"| {g} | {name} | {m:.3f} | [{lo:.3f}, {hi:.3f}] | {'<=' if le else '>='} {thr} | {verdict(m, lo, hi, thr, le)} |")
    L.append("")
    L.append("**Non-size conditions**\n")
    syn_gt = boot_median_ci(syn.drop_duplicates(["key", "a", "c"]).gt_ratio) if syn is not None else (np.nan,) * 3
    checks = [
        ("G1", "exact decode SHA-256 matches, all files", df.exact_ok.all(), f"{df.exact_ok.sum()}/{len(df)}"),
        ("G1", "exact region decode, 5 regions per file", df.region_exact_ok.all(), f"{df.region_exact_ok.sum()}/{len(df)}"),
        ("G2", "error bound holds on every pixel", (df.bound_violations == 0).all(), f"{int(df.bound_violations.sum())} violating pixels"),
        ("G2", "bias: max |mean err/sigma| per decile <= 0.02", (clean.bias_max <= 0.02).all(),
         f"median {clean.bias_max.median():.3f}, max {clean.bias_max.max():.3f} "
         f"(checker rec, D17: median {clean.bias_max_checker.median():.3f}, max {clean.bias_max_checker.max():.3f}, "
         f"{int(df.bound_violations_checker.sum())} bound violations)"),
        ("G2", "stabilized RMSE <= 0.32 (theory 0.289)", (clean.stab_rmse <= 0.32).all(),
         f"median {clean.stab_rmse.median():.3f}, max {clean.stab_rmse.max():.3f}"),
        ("G2", "synthetic ground-truth ratio <= 1.05 (theory 1.041)", syn_gt[0] <= 1.05 if syn is not None else None,
         f"median {syn_gt[0]:.4f} [{syn_gt[1]:.4f}, {syn_gt[2]:.4f}]" if syn is not None else "not run for this set"),
        ("G2", "render-based checks (noise-equivalence, PSNR, Delta E)", None, "not run (deferred, see DEVIATIONS)"),
        ("G3", "neighbour overlap@10, zero-shot accuracy", None, "not run (CLIP deferred)"),
        ("G4", "1024x1024 crop reads only header, L1 and touched tiles", df.crop1024_reads_ok.all(),
         f"{df.crop1024_reads_ok.sum()}/{len(df)}; median {df.crop1024_bytes_read.median() / 1e6:.2f} MB read"),
        ("M3", "stripped file decodes in signal mode; sidecar restores exact", (df.strip_signal_ok & df.sidecar_exact_ok).all(),
         f"{(df.strip_signal_ok & df.sidecar_exact_ok).sum()}/{len(df)}"),
    ]
    L.append("| Gate | Check | Result | Detail |\n|---|---|---|---|")
    for g, name, ok, det in checks:
        L.append(f"| {g} | {name} | {'—' if ok is None else ('PASS' if ok else 'FAIL')} | {det} |")
    L.append("")

    L.append("## 3. Rate per image (bpp)\n")
    L.append("Linear sources first, then sources processed in camera (marked *). L2/L3 at step 1.0.\n")
    L.append("| Camera | bits | B0 | B1 native | B2 JXL | L0 | L1 | L2 | L3 | L3 ideal | hot (L0-L2) | total | G1 | G2 | G3 | fallbacks |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in pd.concat([clean, df[df.lossy_source]]).iterrows():
        b = lambda c: 8 * r[c] / r.npx
        L.append(f"| {r.camera}{'*' if r.lossy_source else ''} | {r.bit_depth} | {r.B0_bpp} | {b('B1_bytes'):.2f} | "
                 f"{b('B2_bytes'):.2f} | {b('L0_bytes'):.4f} | {b('L1_bytes'):.2f} | {b('L2_bytes'):.2f} | {b('L3_bytes'):.2f} | "
                 f"{b('L3_ideal_bytes'):.2f} | {b('hot'):.2f} | {8 * (r.hot + r.L3_bytes) / r.npx:.2f} | "
                 f"{r.r_G1:.3f} | {r.r_G2:.3f} | {r.r_G3:.3f} | {r.fallbacks} |")
    L.append("")
    agg = {}
    for name, sub in [("linear", clean), ("all", df)]:
        agg[name] = {c: np.median(bpp(sub, c)) for c in ["B1_bytes", "B2_bytes", "L1_bytes", "L2_bytes", "L3_bytes", "hot"]}
    L.append("| Subset | B1 | B2 | L1 | L2 | L3 | hot |\n|---|---|---|---|---|---|---|")
    for name, d in agg.items():
        L.append(f"| median, {name} | " + " | ".join(f"{v:.2f}" for v in d.values()) + " |")
    L.append("")

    L.append("## 4. Rate versus distortion: step sweep and B5 (uncalibrated sqrt curve)\n")
    L.append("Distortion is stabilized-domain RMSE (noise sigma units, using each image's fitted model), a "
             "render-free proxy for 8.3. Ratio < 1 means B5 is smaller than MiniRaw at the same RMSE.\n")
    steps = sorted({float(c.split("_")[0][4:]) for c in df.columns if c.startswith("step") and c.endswith("_L0L1L2_bytes")} | {1.0})
    L.append("| Step | median hot bpp | median stab RMSE | median max err (sigma) | bound violations |\n|---|---|---|---|---|")
    for st in steps:
        if st == 1.0:
            hb, rm, mx, bv = clean.hot, clean.stab_rmse, clean.stab_maxerr, clean.bound_violations
        else:
            hb, rm, mx, bv = (clean[f"step{st}_L0L1L2_bytes"], clean[f"step{st}_stab_rmse"],
                              clean[f"step{st}_stab_maxerr"], clean[f"step{st}_bound_violations"])
        L.append(f"| {st} | {np.median(8 * hb / clean.npx):.2f} | {rm.median():.3f} | {mx.median():.3f} | {int(bv.sum())} |")
    L.append("")
    res = {}
    for _, r in clean.iterrows():
        mx = {b: (q, ins) for b, q, _, ins in matched_b5(r.to_dict(), steps, "stab_maxerr")}
        for bits, ratio, rm, inside in matched_b5(r.to_dict(), steps):
            res.setdefault(bits, []).append((ratio, rm, inside, r[f"B5_{bits}_stab_maxerr"], *mx[bits]))
    L.append("Points outside MiniRaw's step range (0.5 to 2.0) are extrapolated; the in-range counts say how many.\n")
    L.append("| B5 bits | n | median B5 RMSE | median B5 max err (sigma) | B5/MiniRaw at matched RMSE | in range | "
             "B5/MiniRaw at matched max error | in range |")
    L.append("|---|---|---|---|---|---|---|---|")
    for bits in sorted(res, reverse=True):
        v = np.array(res[bits], dtype=object)
        m, lo, hi = boot_median_ci(v[:, 0].astype(float))
        m2, lo2, hi2 = boot_median_ci(v[:, 4].astype(float))
        L.append(f"| {bits} | {len(v)} | {np.median(v[:, 1].astype(float)):.3f} | {np.median(v[:, 3].astype(float)):.2f} | "
                 f"{m:.3f} [{lo:.3f}, {hi:.3f}] | {int(v[:, 2].astype(bool).sum())}/{len(v)} | "
                 f"{m2:.3f} [{lo2:.3f}, {hi2:.3f}] | {int(v[:, 5].astype(bool).sum())}/{len(v)} |")
    L.append("")

    L.append("## 5. Fidelity and noise model\n")
    if syn is not None:
        s = syn
        ident = s[s.c_identifiable.astype(str) == "True"]
        L.append(f"Synthetic set: {s.key.nunique()} clean scenes x {len(s.drop_duplicates(['a', 'c']))} (a, c) settings x 4 planes.\n")
        L.append(f"- a within 10%: {(s.a_relerr.abs() <= 0.10).mean():.1%} of planes (median |err| {s.a_relerr.abs().median():.1%}, max {s.a_relerr.abs().max():.1%})")
        cok = (ident.c_err.abs() <= np.maximum(0.25 * ident.c, 1.0))
        L.append(f"- c within 25% or 1 DN^2: {cok.mean():.1%} of the {len(ident)} planes where c is identifiable "
                 f"(>= 20% of variance in the darkest populated bin); {(s.c_err.abs() <= np.maximum(0.25 * s.c, 1.0)).mean():.1%} of all planes")
        L.append(f"- Affine validation passed (flat-block sigma in [0.9, 1.1] for every bin): {(s.validated.astype(str) == 'True').mean():.1%} of planes")
        L.append(f"- Exact decode on synthetic: {(s.exact_ok.dropna().astype(str) == 'True').mean():.0%}")
        L.append(f"- Ground-truth ratio RMSE(decoded, clean)/RMSE(noisy, clean): median {s.gt_ratio.median():.4f}, max {s.gt_ratio.max():.4f} (theory 1.041)\n")
    L.append(f"Real images: isotonic fallback used on {int(df.fallbacks.sum())} of {4 * len(df)} planes "
             f"({(df.fallbacks > 0).sum()} of {len(df)} images).\n")
    L.append("| Camera | per-plane estimated (a, c) | affine flat-sigma range | isotonic flat-sigma range | outliers > 6 sigma |\n|---|---|---|---|---|")
    for _, r in df.iterrows():
        nz = json.loads(r.noise)
        vr = json.loads(r.validate_sigma_range)
        ir = json.loads(r.validate_iso_range)
        L.append(f"| {r.camera}{'*' if r.lossy_source else ''} | " + ", ".join(f"({p['a']:.2f}, {p['c']:.0f})" if p else "—" for p in nz) +
                 f" | {min(v[0] for v in vr if v[1]):.2f}-{max(v[1] for v in vr):.2f} | " +
                 (f"{min(v[0] for v in ir if v[1]):.2f}-{max(v[1] for v in ir):.2f}" if any(v[1] for v in ir) else "—") +
                 f" | {r.outlier_frac:.4%} |")
    L.append("")

    L.append("## 6. Other measurements\n")
    L.append("- G3 by L1 construction, (L1+L2)/L2 direct, linear sources (dev choice of construction and upsampler):")
    for v in g3_variants:
        m, lo, hi = boot_median_ci(clean[f"r_G3_{v}"])
        L.append(f"    - {v.replace('_bilinear', '').replace('_nearest', '').replace('mean', 'A (floor-mean)').replace('wavelet', 'B (5/3 wavelet)')}"
                 f"{', nearest upsampler' if 'nearest' in v else ''}: median {m:.3f} [{lo:.3f}, {hi:.3f}]")
    L.append("- G3 against base-layer size (construction A, bilinear; k from l1_min_side = %s):"
             % "/".join(str(int(x)) for x in sorted(clean.k.unique())))
    for mult in [m for m in (1, 2, 4) if m == 1 or f"L1L2_mean_k{m}x_bytes" in clean]:
        if mult == 1:
            r3, l1 = clean.r_G3_mean_bilinear, clean.L1_bytes
        else:
            r3 = clean[f"L1L2_mean_k{mult}x_bytes"] / clean.L2_direct_bytes
            l1 = clean[f"L1_mean_k{mult}x_bytes"]
        m, lo, hi = boot_median_ci(r3)
        L.append(f"    - k x{mult}: (L1+L2)/L2 direct median {m:.3f} [{lo:.3f}, {hi:.3f}]; "
                 f"L1 median {np.median(8 * l1 / clean.npx):.3f} bpp, {np.median(l1) / 1e3:.0f} KB")
    L.append(f"- L3 actual / ideal (sum log2 n): median {df.r_L3_actual_vs_ideal.median():.5f}")
    L.append(f"- Header (L0) size: median {df.L0_bytes.median():.0f} B, max {df.L0_bytes.max():.0f} B (target 8 KB)")
    L.append(f"- Encode (single-thread, all layers): median {df.encode_s.median():.1f} s; exact decode median "
             f"{df.decode_exact_s.median():.1f} s; megapixels median {df.npx.median() / 1e6:.1f}")
    if len(skipped):
        L.append(f"- Skipped: " + ", ".join(f"{r.key} ({r.reason})" for _, r in skipped.iterrows()))
    L.append("")
    Path(a.out).write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
