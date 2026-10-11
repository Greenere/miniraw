# Evaluation of the v1 suggestions

Oct 10, 2026. This responds to [v1_suggestions_by_chatGPT.md](v1_suggestions_by_chatGPT.md). All measurements are
on the Sony A7 IV development set (10 files, 33 MP, 14-bit, lossless ARW), in bits per photosite. Development data
only; nothing here is a held-out result. Scripts are in `bench/experiments/`.

## Summary

| Suggestion | Verdict | Evidence |
| --- | --- | --- |
| Report sensor-exact and file-exact separately | **Agree; done** | Sensor-exact 28.6 MB, file-exact 31.2 MB, against a 41.2 MB ARW. The README now gives both. |
| Priority 1: a better L3 probability model | **Capped at <1% of L3 at step 1.0; lowest priority** | See "L3" below: theory and measurement agree. |
| Quantization-step tuning on total bytes | **Agree; the strongest lever found** | Step 0.5 makes MiniRaw's lossless total 1.0% *smaller* than JPEG XL; step 2.0 makes it 3.6% larger. |
| Better L2 models | **Agree; the main opportunity** | JPEG XL sits 14-25% above the noise floor on texture-free noisy scenes, so that gap is coder inefficiency. |
| Benchmark RAWIC | **Agree** | The abstract reports an average 7.7% bitrate reduction over JPEG XL. It has not been run on these files yet. |
| Other directions (per-tile modes, burst, effort, tiles, zstd) | Mostly agree | Effort and packing are measured (1-1.5%). The rest are untested. |

## L3: the uniform model is close to the ceiling

L3 codes the position of each sample inside its quantization bin. The suggestion rightly notes that
"actual / ideal ≈ 1" says nothing about whether the uniform model is optimal. Two results bound the possible gain.

**Theory (`l3_bound.py`, oracle calculation).** Give a model the *true* noise-free signal, which no decoder has,
and assume Gaussian noise. With bins one noise-sigma wide (step 1.0), knowing the signal saves only 0.055 bits per
sample, 1.4-2.7% of L3. Quantizing at a step of sigma or finer leaves almost no signal information inside the bin.
The ceiling grows with the step: 0.12 bits at 1.5, 0.20 at 2.0, 0.40 at 3.0.

**Measurement (four files, empirical conditional entropy, tables ignored).**

| Context | Saving at step 1.0 | Saving at step 2.0 |
| --- | --- | --- |
| Signal-mode 3x3 mean (from L2) | 0.009-0.051 bpp (0.2-0.8%) | 0.076-0.093 bpp |
| Exact causal neighbours (the suggested context) | 0.005-0.046 bpp (0.1-0.7%) | 0.059-0.079 bpp |
| Exact neighbours on all sides (not decodable; upper bound) | 0.009-0.051 bpp | 0.079-0.097 bpp |

On this sensor, even the non-decodable context saves at most about 0.05 bpp, about 0.2 MB per photo. The suggested
causal exact-neighbour context does slightly *worse* than the L2-only context, because the neighbours' own noise
dilutes it. L3 matters again only at larger steps, or on sensors with correlated noise (in-camera noise reduction
reached 0.22 bpp on one older camera).

## Step size: lossless total versus hot-tier size

MiniRaw's lossless total, (L1+L2+L3) / JPEG XL lossless, median of 4 files (effort 7, P3):

| Step | 0.5 | 0.75 | 1.0 | 1.5 | 2.0 | 3.0 |
| --- | --- | --- | --- | --- | --- | --- |
| Total vs JPEG XL | **0.990** | 0.996 | 1.005 | 1.018 | 1.036 | 1.066 |

The rise at larger steps is consistent with the information the uniform L3 model loses (the oracle ceiling
above). The trade-off is that step 0.5 makes the hot tier about 30% larger.

To test next: **nested quantization.** Keep a step-1.0 hot tier, and add a cold refinement layer of step-0.5
bins coded with JPEG XL-style prediction, so lossless gets the step-0.5 total while the hot tier stays at step 1.0.

## L2: where JPEG XL leaves room

`bounds.py` compares each coder with the noise floor, the entropy of the sensor noise alone, which no lossless coder
can beat.

| Bits per photosite (median of 10) | Lossless | L2 at step 1.0 |
| --- | --- | --- |
| Noise floor | 5.91 | 2.05 |
| JPEG XL | 6.87 | 2.88 |
| Best quick model tried | 6.81 (-1.0%) | 2.84 (-1.6%) |

On texture-free synthetic scenes with the same noise, JPEG XL is 14-25% above the floor. That part of the gap is
coder inefficiency, not image content. Its predictors combine several noisy neighbours, which multiplies the
noise variance (a gradient predictor triples it), while averaging K neighbours costs only (1 + 1/K) times the
noise. A coder built for MiniRaw can use three things a general-purpose coder lacks:
- the exact noise level (unit variance after the transform)
- already-decoded neighbouring Bayer planes on all sides
- the L1 base layer

The estimate is about 8-12% off L2 and 3-5% off the lossless total. That is in line with RAWIC's reported 7.7%
over JPEG XL, which comes from a learned model.

## Adopted

- **Cross-plane L2 prediction**, a first, deterministic step towards item 1 below, is now the format default.
  - Measured in real JPEG XL bytes: L2 is 5.7-8.9% smaller at effort 7 and 4.5-5.1% smaller at the CLI default effort 4.
  - Exact decode and byte-identical ARW restore are re-verified.
  - Details are in `results/DEVIATIONS.md` (D24).
- **Separate sensor-exact and file-exact reporting** in the README.
- **The step trade-off is documented.** `--step 0.5` gives the smallest lossless total, about 1% under JPEG XL lossless, but a hot tier about 30% larger. The default stays at 1.0.

Not adopted:
- **An L3 context model:** capped below 1% at step 1.0, as shown above.
- **Nested quantization:** unproven so far. It needs its own experiment.

## Revised plan

1. **Noise-aware L2 coder:**
   - Edge-aware averaging predictor plus cross-plane and L1 prediction.
   - Per-pixel Gaussian residuals with the known noise variance, range-coded.
   - A row-parallel decoder, so it stays fast in numpy.
   - It helps both the hot tier and the lossless total.
2. **Nested quantization:** test whether lossless gets the step-0.5 total while the hot tier stays at step 1.0.
3. **Run RAWIC** on the same sensor arrays to set a learned-model reference. It needs PyTorch and the released
   weights; it may run on M1 CPU or MPS.
4. **L3 context model:** only if steps above 1.5 become the default, or for noise-reduced sensors.

Throughout, as the suggestion says:
- Report every layer, sensor-exact and file-exact totals, tables and side information, time, and region-read cost.
- Verify hashes.
- Tune on development files and confirm on held-out images that span ISO and scene detail.

## The algorithm and implementation review

This responds to `v1_algo_improvements_guidance_by_chatGPT.md`. The review is accurate. The contract chosen for
every change was **identical decoded values and identical encoded bytes**. It was verified on 42 of 42 checks;
details are in `results/DEVIATIONS.md` (D25).

| Item | Outcome |
| --- | --- |
| P0 production-path measurement | Done: `bench/experiments/prod_paths.py` (fresh process per path, file-level read accounting) |
| P1 real selective I/O | Done. The finding was right: every decode read the whole file. A crop now reads 1.4 MB of 28.2 MB, a header 0.02 MB. |
| P2 direct tile selection | Done |
| P3 skip unused preview repack; one source read | Done (76 to 38 MB read). Exact-reproduction verification kept. |
| P4 compact arrays and fewer copies | Done: uint16 q, no padded L1 copy, direct mosaic writes, strip hashing. Peak memory down 13-36%. |
| P5 partition instead of sort | **Rejected.** It changes encoded bytes through tie-breaking, for about 3% of encode time. |
| P6 single CRC computation | Done. A streaming writer is deferred. |
