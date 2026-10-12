# Prior-art baselines (v3 P0) and D31 verification

October 11, 2026. Code at `f9e7178` (v2.0.0 plus D31). Apple M4 Pro, Python 3.13, libjxl 0.12.0. This report answers
the two P0 questions of [docs/v3_novelty_guidance.md](../docs/v3_novelty_guidance.md) (§4, §7) and verifies D31
([DEVIATIONS.md](DEVIATIONS.md)).

## Summary

| Question | Finding |
| --- | --- |
| **Is MiniRaw's lossless edge over JPEG XL its cross-plane predictor?** (§4B) | **No.** Giving JPEG XL the same predictor changes it by +0.1% (A7 IV) and +1.1% (FiveK). MiniRaw's lossless total is 2.5% (A7 IV) and 1.0% (FiveK) smaller than the best JPEG XL variant. |
| **Does MiniRaw beat calibrated noise-scaled quantization?** (§4A) | **Not on size or average error.** A calibrated square-root quantizer with the same predictor, coder and tiles is 0.1-0.5% smaller at every step, with the same average error. |
| **Worst-case error against that baseline** | **Unresolved.** Equal when measured with the baseline's noise model. Much better for MiniRaw when measured with MiniRaw's model, on one dark high-ISO frame. Which model is right needs an independent noise reference. |
| **Reconstruction bias** | **MiniRaw is lower in both noise units:** 0.017 against 0.068 (A7 IV) and 0.063 against 0.093 (FiveK) at step 1.0, lower on 10 of 10 and 29 of 37 files. |
| **D31** | **Verified.** Steps 0.5 and 1.0 are byte-identical on all 210 images. At steps 1.5 and 2.0, 14 images change (19 image-steps), all smaller (up to 31%), with no bound violations. No image's hot tier grows from step 1.5 to 2.0 any more. |

**Reading against the guidance's go/no-go criteria (§9).** Once calibration and prediction are matched, MiniRaw's
quantization brings no size advantage, which is §9's "reassess the claim" case for an *algorithmic* narrative. What
remains measurable:
- **Lower bias**, without breaking the error bound.
- **A lossless total slightly under JPEG XL's** that the predictor doesn't explain.
- **The system capabilities**: layering, region access, indexing.

That points to the "practical layered system" or "bias-controlled bounded-error reconstruction" directions (§5),
not to a new compression algorithm.

## 1. Calibrated square-root baseline (P0-A)

**Baseline** (`bench/experiments/calibrated_sqrt.py`, after Bernstein et al. 2010):
- **Noise model:** a per-plane affine noise fit (MiniRaw's estimator, without its validation and D26 lowering).
- **Quantizer:** the generalized Anscombe transform and a uniform quantizer `q = round(T / step)`.
- **Two reconstructions:** the textbook inverse, and the bin's middle DN.
- **Coding:** codes are JPEG XL lossless with MiniRaw's cross-plane predictor, the same 512 px tiles and effort 7. So
  only quantization and reconstruction differ.

**MiniRaw** is the shipped codec: format 2, `csafe` reconstruction, D31.

**Sizes** are L1 + L2 (the quantized codes), at the same steps. Errors are reported in two noise units, because
neither is ground truth:
- **"MiniRaw model":** MiniRaw's final noise model, as the benchmark uses.
- **"Affine fit":** the baseline's own model.

### Sony A7 IV (10 files, the tuning set)

| Step | Size, √ / MiniRaw [95% CI] | RMSE, MiniRaw / √ | Worst pixel, median (max): MiniRaw model | Worst pixel, median (max): affine fit | Bias, MiniRaw / √ mid / √ inv | √ pixels outside its own bound |
| --- | --- | --- | --- | --- | --- | --- |
| 0.5 | 0.9970 [0.9943, 0.9989] | 0.147 / 0.149 | 0.43 (0.44) / 0.45 (6.93) | 0.43 (0.43) / 0.42 (0.49) | 0.004 / 0.058 / 0.032 | 0 |
| 1.0 | 0.9964 [0.9928, 0.9986] | 0.290 / 0.292 | 0.69 (0.88) / 0.80 (10.39) | 0.69 (0.87) / 0.68 (0.89) | 0.017 / 0.068 / 0.050 | 0 |
| 1.5 | 0.9961 [0.9917, 0.9985] | 0.434 / 0.437 | 0.92 (1.16) / 1.11 (17.32) | 0.92 (1.13) / 0.86 (1.16) | 0.034 / 0.070 / 0.064 | 7,326 (1 file) |
| 2.0 | 0.9953 [0.9914, 0.9984] | 0.578 / 0.582 | 1.15 (1.24) / 1.16 (24.25) | 1.14 (1.20) / 1.12 (1.17) | 0.072 / 0.077 / 0.064 | 239,387 (1 file) |

### FiveK `holdout2` (37 linear images, 14 cameras)

| Step | Size, √ / MiniRaw [95% CI] | RMSE, MiniRaw / √ | Worst pixel, median (max): MiniRaw model | Worst pixel, median (max): affine fit | Bias, MiniRaw / √ mid / √ inv | √ pixels outside its own bound |
| --- | --- | --- | --- | --- | --- | --- |
| 0.5 | 0.9991 [0.9978, 0.9998] | 0.147 / 0.148 | 0.33 (0.48) / 0.38 (0.69) | 0.32 (0.48) / 0.32 (0.48) | 0.019 / 0.076 / 0.053 | 0 |
| 1.0 | 0.9988 [0.9968, 0.9998] | 0.289 / 0.292 | 0.58 (0.85) / 0.61 (1.60) | 0.58 (0.85) / 0.57 (0.87) | 0.063 / 0.093 / 0.103 | 0 |
| 1.5 | 0.9986 [0.9973, 0.9998] | 0.434 / 0.434 | 0.84 (1.17) / 0.84 (2.29) | 0.82 (1.16) / 0.81 (1.11) | 0.119 / 0.165 / 0.152 | 9 (1 file) |
| 2.0 | 0.9988 [0.9962, 0.9997] | 0.578 / 0.579 | 1.09 (1.47) / 1.11 (3.31) | 1.09 (1.47) / 1.06 (1.76) | 0.183 / 0.249 / 0.214 | 112,234 (2 files) |

MiniRaw had no pixel outside its own bound at any step, on either set.

### What the tables show

- **Size.** The calibrated baseline is 0.1-0.5% smaller everywhere, with tight intervals. MiniRaw's extra bits go to
  its bin rules and per-bin flags:
  - single-DN bins at the white level (D20)
  - bins repaired so they keep the bound (D27, D31)
  - the `csafe` flags

  Its quantizer is not more efficient.
- **Average error is the same.**
- **Worst-case error depends on the reference.**
  - Under the affine fit, the two are equal at every step (A7 IV step 1.0: maximum 0.87σ against 0.89σ).
  - Under MiniRaw's model, the baseline reaches 10-24σ on one file, DSC03393, a dark high-ISO frame. MiniRaw's
    corrected model says that frame's noise is far lower than the affine fit says.
  - If MiniRaw's model is right, the baseline discards real detail there; if the affine fit is right, MiniRaw spends
    bits on noise.
  - Only an independent noise reference (repeated or dark-frame captures) can settle this (guidance §3.1, P1).
- **Bias** is lower for MiniRaw in both units and at nearly every step, from `csafe`:
  - The exception is A7 IV step 2.0, where the baseline's inverse reconstruction is lower (0.064 against 0.072).
  - The baseline's inverse reconstruction is less biased than its middle-DN one, but still 2-3x MiniRaw's at the
    default step.
- **The baseline breaks its own bound at steps 1.5 and 2.0,** on 1-2 files per set. That is because it has no
  bin-repair rule like D27/D31, which a baseline could add. It is not evidence of a deeper advantage.

## 2. Matched predictor (P0-B)

`bench/experiments/matched_predictor.py`:
- **The comparison.** JPEG XL lossless on the unquantized raw planes, with and without MiniRaw's cross-plane
  predictor (no L1, as in format 2), in per-plane (P1) and 4-channel (P3) packings.
- **Same tiles and effort.** The predicted variants decode bit-exact on every file.
- **MiniRaw's side** is its lossless total (L1 + L2 + L3), encoded fresh on the same file.

| | Sony A7 IV (10) | FiveK `holdout2` (37 linear) |
| --- | --- | --- |
| JPEG XL + predictor (P1) / plain JPEG XL (P3) | 1.0006 [0.9963, 1.0094] | 1.0114 [1.0002, 1.0169] |
| MiniRaw lossless / plain JPEG XL (P3) | 0.9730 [0.9671, 0.9865] | 0.9874 [0.9819, 0.9963] |
| MiniRaw lossless / best JPEG XL variant | **0.9746** [0.9698, 0.9865] | **0.9897** [0.9853, 0.9963] |
| Files where MiniRaw is smaller than every JPEG XL variant | 10 of 10 | 27 of 37 |

**What it shows.**
- **The predictor does not make JPEG XL competitive.** It is neutral on the A7 IV and slightly harmful on FiveK,
  where JPEG XL's own context modelling already captures cross-plane structure.
- **MiniRaw's lossless edge is small and real.** It is 1.0-2.5% at the median, and on FiveK 10 of 37 files favour
  JPEG XL. It comes from coding each value as a bin index plus a range-coded position within the bin, not from
  prediction.
- **Neither result makes it a fundamental floor** (guidance §3.3).

## 3. D31 verification

D31 changes how `vst.forward_table` repairs bins whose middle DN misses the error bound. D27 halved them; D31 trims
them at the top and starts the next bin with the leftover. The runbook, [docs/d31_second_mac_runbook.md](../docs/d31_second_mac_runbook.md),
was followed on this machine, which produced the stored D27 results with the same libraries.

**Tables** (`bench/experiments/d31_check.py tables`, no JPEG XL; 210 images: dev, test, holdout, holdout2 and the 10
A7 IV files; 840 planes):

| Step | Planes changed | Images | Bins, D27 to D31 | Pixels outside the bound |
| --- | --- | --- | --- | --- |
| 0.5 | 0 | 0 | — | 0 |
| 1.0 | 0 | 0 | — | 0 |
| 1.5 | 15 | 5 (test 2, holdout2 2, A7 IV 1) | 3,474 to 3,249 | 0 |
| 2.0 | 45 | 14 (dev 1, test 5, holdout 4, holdout2 3, A7 IV 1) | 12,726 to 12,071 | 0 |

Every changed plane has fewer bins; the code entropy falls by a median 0.62 bits/pixel at step 1.5 and 0.41 at step
2.0.

**Benchmarks** (format 2 re-runs under new names; the D27 results stay untouched):

| | Holdout (60) | `holdout2` (60) | A7 IV (10) |
| --- | --- | --- | --- |
| Images changed at steps 0.5 / 1.0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Step 1.5: images changed, smallest new/old | 0 | 2, 0.711 | 1, 0.926 |
| Step 2.0: images changed, smallest new/old | 4, 0.782 | 3, 0.689 | 1, 0.696 |
| Largest new/old at any step | 1.0000 | 1.0000 | 1.0000 |
| Pixels outside the bound, D27 to D31 | 0 to 0 | 0 to 0 | 0 to 0 |
| Images whose hot tier grows from step 1.5 to 2.0, D27 to D31 | 2 to **0** | 1 to **0** | 1 (DSC03393) to **0** |

All runbook pass conditions hold. Every README and v2 benchmark headline figure is at step 1.0 and is unchanged.
Two chart points at step 2.0 moved: the A7 IV median hot tier went from 8.29 to 8.07 MB, and FiveK from 1.821 to
1.815 bits per pixel. The README charts are regenerated from the D31 runs.

## What this means for MiniRaw's claims

Carried into the README and [docs/METHOD.md](../docs/METHOD.md):
- **The error bound is model-relative:** every pixel is within half a step of MiniRaw's own noise model, which is
  estimated from the image. It is not a measured guarantee relative to the true sensor noise (guidance §3.1).
- **The comparison that matters is the calibrated one.** "Smaller than a fixed square-root curve at the same
  worst-case error" stays true, but a calibrated curve matches MiniRaw's size and average error.
- **What MiniRaw adds over calibrated companding,** as measured:
  - lower reconstruction bias without breaking the bound
  - bin rules that keep the bound at every step
  - a slightly smaller lossless total
  - the layered, region-readable, indexed format

## Limitations

- **The A7 IV files are the tuning set, from one camera.** The FiveK cameras date from 2003-2009.
- **The worst-case comparison is unresolved;** it needs an independent noise reference.
- **The baseline is one reasonable implementation, not the only one.** It uses MiniRaw's affine estimator without
  validation, a plain uniform quantizer and no bin repair. A stronger calibrated baseline might add bias correction
  or bin repair.
- **Sizes compare quantized codes.** Headers, tables and the per-bin flags are a small part of the difference.
- **No downstream-workflow, camera-level-holdout or complete-system comparison yet** (guidance P1).

## Reproduce

```sh
python -m bench.experiments.d31_check tables dev test holdout holdout2 data/personal_raws
python -m bench.run --config configs/v2.toml --split holdout --name holdout_v2_d31      # and holdout2, --dir data/personal_raws
python -m bench.experiments.d31_check compare holdout_v2 holdout_v2_d31
python -m bench.experiments.matched_predictor data/personal_raws      # and a folder of the 37 linear holdout2 images
python -m bench.experiments.calibrated_sqrt data/personal_raws
```

Data:
- `results/experiments/`: `d31_tables.csv`, `matched_predictor_*.csv` and `calibrated_sqrt_*.csv`.
- `results/per_image/`: `holdout_v2_d31`, `holdout2_v2_d31` and `personal_raws_v2_d31`.
