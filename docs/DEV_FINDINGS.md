# MiniRaw Phase 1: dev-split findings

Oct 10, 2026. These come from the dev split: 20 FiveK DNGs, one per camera model. The 14 linear-source images are used for the gates; 6 sources processed in camera are reported separately. Numbers come from `results/report.md`, and every departure from the spec is in `results/DEVIATIONS.md`. This is not the frozen test-split verdict.

## Gates

| Gate | Result | Verdict |
| --- | --- | --- |
| G1: factorization is free | (L1+L2+L3)/B2 = 1.016 [1.000, 1.031]; exact decode 20/20, region decode 20/20 | Marginal pass |
| G2: hot tier is small | (L0+L1+L2)/B2 = 0.448 [0.371, 0.532]; bound holds on every pixel; stabilized RMSE 0.290 (theory 0.289); synthetic ground-truth ratio 1.0406 (theory 1.041) | Marginal pass on size; the bias sub-check fails (see below) |
| G3: index is free | (L1+L2)/L2 direct = 1.048 [1.037, 1.077] with construction A; construction B is worse at 1.096 | Fail at the spec's L1 size |
| G4: random access works | Tiling overhead -0.8%; a 1024x1024 crop reads only the header, L1 and touched tiles (20/20) | Pass |

## What the data says about the direction

1. **The core idea works on well-behaved sensors.** On cameras whose noise fits the affine model (no fallback), the split costs 0 to 2% over JPEG XL lossless. The hot tier is 0.30 to 0.46 of B2, about 2.5 bpp against 6.5 to 8 bpp. L3 codes at its ideal cost (actual/ideal = 1.00006). Decoding is bit-exact everywhere.
2. **The weak spot is sensors with in-camera noise processing.** The four cameras that fell back to the isotonic model on all four planes are the Sony A900, Leica D-LUX 3, Canon 1Ds Mk II and Canon 5D. They account for most of the G1 misses (1.03 to 1.08) and the G2 misses (0.53 to 0.64). Low-noise sensors (30D, sigma about 1.7 DN) have little noise to discard, which gives G2 = 0.71. This is the main risk for the test split.
3. **The index is nearly free with a smaller base layer.** At the spec's k (L1 short side at least 256 px, median 212 KB), G3 overhead is 4.8%. Doubling k (short side at least 128 px, 57 KB) gives 1.025 [1.014, 1.031]. Quadrupling it (18 KB) gives 1.015. A 128 px short side is still enough for a 224 px CLIP input. The wavelet construction (B) is worse, because JPEG XL's predictors suit pixels, not detail bands.
4. **Calibration buys the guarantee, not bytes on average.** At matched stabilized RMSE, the uncalibrated sqrt curve (B5) roughly ties MiniRaw (8 bits: 0.99 [0.97, 1.04]). At matched worst-case error, B5 needs 15 to 35% more bytes. The spec anticipated this tie.
5. **Tone-curve sources need a different L3 model.** The Nikon D70, D70s, D80 and D100, the Leica M8 and the D700's R and B planes have empty codes. The uniform in-bin model wastes bits on codes that never occur (G1 from 1.19 to 4.5 on these sources). The spec's empirical L3 model, or a gap-aware one, would fix this.

## Spec issues found

- **Noise estimator (D1, D2).** As written, image texture leaks into the variance estimate and `a` comes out 15 to 73% high. A Laplacian residual plus a selection-bias correction brings `a` within 3% on synthetic data. `c` cannot be recovered when a scene has no dark pixels.
- **Bias check (8.2).** This check fails even with a perfect per-bin centroid reconstruction (0.06 to 0.17). Deciles are taken on the original value, and at step 1.0 a dark decile spans only a few bins about 1 sigma wide, so partial bins at the decile edges dominate. Binning by decoded value (or by q) would measure the actual bias. The spec's floor `rec` adds a further -0.5 DN, which matters on low-noise sensors. The checker alternative (D17) removes that offset, but it puts 0.001% of pixels slightly past the bound.
- **Clipped pixels (D20).** Clipped pixels now get single-DN bins, so they decode exactly at no rate cost.
- **Bound at large steps.** At step 1.5 and above, the spec's table construction exceeds the bound on a few cameras (up to 87k pixels at step 2.0 on the 5D Mk II). Step 1.0 is clean.

## Suggested next steps

1. Decide the L1 size rule: a short side of at least 128 px passes G3 on dev.
2. Redefine the bias check to bin by decoded value.
3. Before the test run, get RAISE access (registration form plus a decision on the non-commercial terms) and download RawNIND. Both are needed for ISO coverage and for the noise-reduction question in point 2.
4. Build CLIP indexing (the rest of G3), render-based fidelity checks, and B4. Then run the test split once.
