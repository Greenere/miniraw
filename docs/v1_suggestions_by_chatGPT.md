# Suggestions for Further Lossless Compression in MiniRaw

Based on the repository reviewed on October 10, 2026. These are research suggestions, not newly measured compression results.

## Conclusion

Further lossless compression is plausible, primarily through better probability models. Expect incremental improvements; the current evidence does not support another 2× reduction. Preserve MiniRaw’s layered decoding and random access while testing whether the remaining data is more predictable than the current models assume.

## Define the target

- **Sensor-exact:** recover every encoded RAW sensor sample.
- **File-exact:** recover the original ARW bytes, including metadata, previews, and container structure.

Report both separately. The README’s approximately 28.6 MB lossless sensor representation excludes the additional original-container and preview payloads needed for file-exact restoration. Removing those payloads is not a file-exact compression improvement.

## Current baseline

The ten-image A7 IV development report gives these median rates, in bits per sensor photosite:

| Component | Median bpp |
| --- | ---: |
| L0 header | ~0.001 |
| L1 base | ~0.01 |
| L2 quantized full-resolution data | ~2.96 |
| L3 exact in-bin remainder | ~3.86 |
| Direct lossless JPEG XL baseline | ~6.87 |

Individual medians do not necessarily sum to the median total. These results are from a small development set, not a held-out performance guarantee.

## Priority 1: Improve L3’s probability model

The current `kappa_encode` uses a uniform distribution over each quantization bin. A bin with 16 possible original values therefore costs approximately four bits per sample.

Instead, estimate `p(kappa | q, context)` and range-code the exact same remainder. Reconstruction remains unchanged:

```text
x = lo[q] + kappa
```

Candidate context:

- All four decoded L2 planes, local brightness, gradients, and edges.
- Previously decoded exact samples within the same tile.
- Sensor-specific row/column patterns and value distributions.
- A learned scene estimate and predictive uncertainty.

The decoder must reproduce the probability tables deterministically. It cannot depend on original samples that have not yet been decoded. Keep dependencies within each tile, or explicitly account for any extra reads.

The existing local-mean context experiment reports roughly 0.05 bpp potential savings on the dev set: about 0.21 MB at 33 MP. It ignores model overhead and uses empirical entropy estimates, so this is not a demonstrated net saving or a ceiling for stronger models.

**Important:** `L3 actual / ideal ≈ 1.00005` only shows that the range coder closely matches the uniform model’s cost. It does not prove that the uniform model is optimal or that L3 contains only unpredictable noise.

## Priority 2: Benchmark learned RAW compression and stronger L2 models

For fixed quantization tables, the transformation from the original data `X` to `(Q, K)` is reversible:

```text
H(X) = H(Q) + H(K | Q)
```

Layering does not remove information. Better conditional models can reduce the gap between actual coding cost and these entropies.

MiniRaw currently subtracts an upsampled L1 image and encodes the residual with JPEG XL. Investigate dedicated models for `p(Q | L1)` and `p(K | Q, previously decoded K)` that exploit Bayer geometry and signal-dependent noise.

RAWIC reports an average 7.7% bitrate reduction over JPEG XL. Benchmark its actual encoded output on the same A7 IV sensor arrays before drawing conclusions. Its published gain does not establish equivalent savings for this camera or MiniRaw’s layered format. A hypothetical 7.7% reduction of 28.6 MB would save about 2.2 MB; this is an illustration, not a forecast.

## Other directions

| Direction | Assessment |
| --- | --- |
| Per-tile predictor or codec selection | Worth testing; count mode flags and retain the current codec as a fallback. |
| Quantization-step tuning | Optimize total L0–L3 bytes, not just hot-tier size. A larger step shifts information into L3. |
| Joint burst compression | Shared scene structure may help; independent noise remains, and frame dependencies complicate access and recovery. |
| JPEG XL effort and plane packing | Existing experiments suggest modest gains; production defaults and benchmark effort should be distinguished. |
| Removing tiles | Current reported tiling overhead is slightly negative; no evidence of an easy win from sacrificing random access. |
| ZIP/zstd over completed `.mraw` files | Low priority: the major payloads are already entropy-coded. |

## Fundamental limit

A denoiser can improve scene prediction, but lossless recovery still requires the exact residual:

```text
X = predicted_clean_image + exact_residual
```

Independent shot noise and read noise cannot be removed losslessly. For a sufficiently resolved, approximately Gaussian noise distribution with standard deviation `sigma` in integer RAW units, the discrete entropy is approximately `log2(sigma) + 2.05` bits per sample. At `sigma = 16`, that is about 6.05 bits per sample even with the clean signal known. This is illustrative, not a measured lower bound for the benchmark images.

Generated replacement noise may look equivalent but does not reproduce the recorded samples.

## Recommended experiment sequence

1. Establish paired baselines on held-out A7 IV images spanning ISO, brightness, and scene detail. Report sensor-exact and file-exact sizes separately.
2. Replace only the L3 uniform model with a deterministic conditional model. Compare actual bytes, including all tables and side information.
3. Benchmark RAWIC on identical sensor arrays to estimate the broader opportunity beyond JPEG XL.
4. If the gains justify the cost, develop a stronger L2 model or joint layered model.

For each experiment, report per-layer and total bpp, paired savings, encode/decode time, model dependencies, and region-read cost. Verify exact sensor hashes and, where supported, original-file SHA-256. Select parameters on development data and evaluate generalization on held-out images.

**First recommendation:** improve L3’s model while retaining the current format’s useful behavior. This isolates whether the opportunity is a few hundred KB or several MB before committing to a larger codec redesign.

## Sources

- [MiniRaw README](https://github.com/Greenere/miniraw/blob/main/README.md)
- [A7 IV development report](https://github.com/Greenere/miniraw/blob/main/results/report_personal_raws.md)
- [Development findings](https://github.com/Greenere/miniraw/blob/main/docs/DEV_FINDINGS.md)
- [Entropy implementation](https://github.com/Greenere/miniraw/blob/main/src/miniraw/entropy.py)
- [Codec implementation](https://github.com/Greenere/miniraw/blob/main/src/miniraw/codec.py)
- [L3 context experiment](https://github.com/Greenere/miniraw/blob/main/bench/experiments/l3_context.py)
- [RAWIC paper](https://arxiv.org/abs/2603.28105)
- [RAWIC implementation](https://github.com/chunbaobao/RAWIC)
