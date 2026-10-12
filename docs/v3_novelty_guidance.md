# MiniRaw v3: Novelty and Publication Guidance

Date: 2026-10-11  
Reviewed implementation: [v2.0.0, commit 5f21d996](https://github.com/Greenere/miniraw/tree/5f21d9965e6eff8d9abab7533995529d3f803ae8)

This document summarizes a targeted literature and implementation review. Existing benchmark numbers are repository-reported, not independently reproduced. This is a research roadmap, not an exhaustive novelty determination or a claim that v3 has already been implemented.

## 1. Assessment

**MiniRaw has a plausible paper as a practical layered RAW system. Its present evidence does not establish a fundamentally new noise-aware compression algorithm.**

Noise-scaled quantization, single-image noise estimation, base-plus-residual exact recovery, and progressive regional access all have precedents. Even separating clean-image and noise information into different layers has prior work.

The strongest candidate contribution is the joint trade-off: a compact working representation with a model-relative error bound, detachable exact refinement, regional access, and inexpensive semantic indexing, at little total-rate penalty relative to ordinary lossless coding.

Before writing a manuscript, prioritize calibrated quantization baselines and matched-predictor ablations. Their results should determine whether the paper emphasizes an algorithmic advance or a complete storage/access system.

## 2. Closest prior art

| Work | Established idea | Consequence for MiniRaw |
| --- | --- | --- |
| Bernstein et al., *Noise and Bias in Square-Root Compression Schemes* (2010) [1] | Shot/read-noise-aware scalar quantization followed by lossless compression; reconstruction-bias analysis | Noise-calibrated companding is established. An uncalibrated square-root baseline is insufficient. |
| Foi et al., *Practical Poissonian-Gaussian Noise Modeling and Fitting for Single-Image Raw-Data* (2008) [2] | Automatic estimation of signal-dependent RAW noise, including clipping effects | Single-image calibration alone is not novel. Compare accuracy and failure tails. |
| Celik, Sharma, and Tekalp, *Gray-Level-Embedded Lossless Image Compression* (2003) [3] | Base/residual decomposition, exact reconstruction, truncation with bounded pixel error | Layered exact recovery and bounded-error prefixes have direct precedents. |
| Bai et al., *Deep Lossy Plus Residual Coding for Lossless and Near-Lossless Image Compression* (TPAMI 2024) [4] | Lossy coding plus lossless residuals; bounded-error near-lossless modes | Exact and near-lossless operation in one framework is established. |
| Alvar et al., *Joint Image Compression and Denoising via Latent-Space Scalability* (2022) [5] | Base features reconstruct a denoised image; enhancement features reconstruct the noisy image | Especially close conceptual prior art for a signal/noise layer story; its learned lossy reconstruction differs from MiniRaw's exact refinement. |
| Li et al., *Compress Clean Signal from Noisy Raw Image: A Self-Supervised Approach* (ICML 2024) [6] | RAW compression targeting clean content rather than real noise; calibrated evaluation data | Essential modern related work. Its clean-signal objective differs from preserving the observed RAW within a bound. |
| JPEG 2000 [7] | Lossless/lossy coding, quality scalability, regional access | Tiling and selective decoding are capabilities, not strong standalone novelty claims. |

Deployment context also matters. Jetraw describes sensor-calibrated noise-aware compression [8]. DxO PureRAW 6 combines nonlinear precision reduction with JPEG XL for processed linear DNGs [9]. These vendor descriptions do not establish equivalence to MiniRaw's guarantees, but rule out broad novelty claims about noise-aware preprocessing plus a modern codec.

## 3. Claims to tighten before publication

### 3.1 The bound is relative to an estimated model

The implementation checks a bound of the form:

```text
|T_hat(x) - T_hat(reconstruct(q(x)))| <= step / 2 + local one-DN allowance
```

Here `T_hat` is built from the selected or estimated noise model; DN means a RAW digital unit. This is a verifiable bound in the transformed domain. It is not automatically a guarantee relative to the true physical sensor noise.

The method report records remaining noise-sigma overestimation up to 3.3x in a texture-preserving synthetic harness. A codec can obey its model-relative bound while exceeding the intended physical-noise budget. Report model-relative compliance and independently calibrated noise-relative error separately.

### 3.2 L2 and L3 are not a physical signal/noise separation

- L2 is a quantized noisy observation, not an extracted clean image.
- L3 is the exact within-bin quantization remainder, not isolated physical sensor noise.
- Dropping L3 can remove information useful to downstream estimation, stacking, or other processing.

Use “quantized working representation” and “exact refinement” in technical claims. Explain signal/noise terminology as motivation, not a demonstrated decomposition of the latent scene and measurement noise.

### 3.3 Residual coding efficiency is not a fundamental floor

Being close to `sum(log2(bin_width))` shows efficiency under the uniform-within-bin model. It does not prove proximity to the conditional entropy of the residual or a universal lossless compression limit.

### 3.4 Exactness has different scopes

Keep these distinct throughout tables and prose:

1. Exact reconstruction of the encoded visible sensor mosaic.
2. Exact reconstruction of the entire original RAW file, where supported.
3. Approximate reconstruction from the hot tier.

The benchmark's sensor hashes must not be described as original-file hashes unless the file-restoration path was actually tested.

## 4. Two important comparison gaps

### A. The current square-root baseline is uncalibrated

`baselines/b5_sqrt.py` implements approximately:

```text
q(x) = round(sqrt(x / xmax) * (2**bits - 1))
```

Its mapping does not use the supplied black level or fitted shot/read-noise parameters. Beating this baseline establishes an advantage over a fixed curve, not over established calibrated companding [1]. It is also a simplified camera-style baseline, not a verified implementation of a particular camera's codec.

Add a calibrated Poisson–Gaussian/square-root baseline with appropriate reconstruction and bias treatment. Give it the same model, coding backend, tiling, effort, and prediction options as MiniRaw. Evaluate both average distortion and worst-case error at matched rates or constraints.

### B. Cross-plane prediction is a confound

MiniRaw uses explicit cross-plane prediction. The direct JPEG XL baseline in `baselines/b2_jxl.py` codes plane groupings without that explicit predictor.

This may be a legitimate system advantage, but it does not isolate the benefit of noise-based decomposition. Add direct RAW coding with the same predictor, and evaluate predictor on/off for both quantized and unquantized inputs. Handle residual ranges consistently without introducing clipping or losses.

## 5. Candidate paper directions

| Direction | Current assessment | Required advance |
| --- | --- | --- |
| Fundamental new noise-aware RAW codec | Not supported | Clear improvement over calibrated and structurally matched prior art |
| Practical layered RAW storage/access system | Most promising | Demonstrate a useful joint storage, latency, I/O, and fidelity advantage |
| Quantization robust to calibration uncertainty | Potentially stronger research contribution | Principled uncertainty handling plus independently validated noise-relative bounds |
| Bias-controlled bounded-error reconstruction | Possible narrower algorithmic contribution | Improve bias without breaking the bound or causing excessive bitrate; compare prior bias corrections |
| Software/demo paper | Closest to ready | Reproducibility, honest claims, broader validation, and demonstrated workflows |

Candidate positioning:

> MiniRaw is a practical RAW representation providing noise-model-bounded reconstruction, separately retrievable exact refinement, and inexpensive regional and semantic access while retaining competitive total lossless size.

Treat that sentence as a hypothesis to substantiate. Do not claim the combination is a verified first.

## 6. Evidence worth developing

The v2 report [10] contains encouraging results:

- Fresh size benchmark: 37 linear RAW images out of 60 tested images, from 14 older camera models.
- Hot tier: median 0.415x the JPEG XL lossless baseline.
- Reported sensor-exact layer size: median 0.987x the baseline.
- Index reads: 16 KB with a stored embedding; approximately 51 KB for thumbnail-based re-indexing.
- Exact sensor reconstruction, region access, stripping, and sidecar reconstruction are implemented and checked.
- Real-image median worst-decile bias remains 0.063 sigma against the project's 0.02 target.
- Ten A7 IV images provide modern-camera evidence, but were used during development.

The interesting result is the combination of capabilities with little apparent total-rate penalty. Small lossless savings alone are not yet strong evidence of general superiority. Reconcile payload-only and complete-file accounting before making publication claims.

## 7. Prioritized v3 experiments

| Priority | Experiment | Controls and outputs | Decision it enables |
| --- | --- | --- | --- |
| P0 | Calibrated quantization baseline | Same noise model, predictor, backend, tiling, effort; rate vs RMSE, maximum error, and bias | Does MiniRaw improve on established noise-scaled quantization? |
| P0 | Decomposition/predictor ablation | Direct RAW +/- cross-plane prediction; fixed-width split + exact residual; noise-adaptive split +/- prediction | Which component earns the compression benefit? |
| P1 | Independent noise validation | Repeated captures or independent calibration; held-out camera models and ISO settings; texture, shadows, clipping | Does model-relative compliance translate into actual noise-relative fidelity? |
| P1 | RAW workflow fidelity | Matched development, strong exposure and WB changes, denoising, stacking, highlight recovery | Are downstream changes acceptably small, including bias and structured artifacts? |
| P1 | Complete-system baseline | Lossless RAW archive + thumbnail/embedding index + compact working copy | Does the integrated representation beat the obvious composition of existing tools? |
| P2 | Broader codec comparison | JPEG-LS lossless/near-lossless, JPEG 2000 and relevant Bayer codecs; distinguish clean-target and observed-target objectives | How competitive is MiniRaw beyond its current baselines? |

For system workloads, include metadata search, re-indexing with a different embedding model, preview browsing, cropped edits, full-image development, and exact restoration. Measure bytes stored/read, request count, encode/decode time, peak memory, and end-to-end latency. Model cold-tier retrieval separately from hot-tier access.

For semantic indexing, compare against an external embedding database and equivalent cached thumbnails. Header placement alone is weak novelty. Distinguish agreement with full-render embeddings from relevance judged against independent labels or queries.

## 8. Evaluation discipline

- Freeze configurations before confirmation runs; retain the repository's dev/test distinction.
- Add camera-level holdout, not only image-level holdout. Broaden modern-camera coverage.
- Report per-image distributions and failure tails, not only medians; avoid treating correlated pixels as independent samples.
- Separate sensor payload, complete encoded file, and original-file-exact archive sizes. Include headers, tables, thumbnails, metadata, and refinement data under matched capability assumptions.
- Keep effort, threading, tiling, hardware, and input/output scope explicit. Separate in-memory codec timing from end-to-end RAW ingest/export timing.
- For fidelity, report transformed-domain error, DN error, independently calibrated noise-relative error, bias, and downstream outcomes.
- Evaluate checkerboard reconstruction for spatial artifacts and stacking behavior, not only mean bias.
- Attribute any gains to calibration, quantization, prediction, entropy coding, and layout through ablations.

## 9. Go/no-go criteria

These are proposed decision rules, not results or guarantees of acceptance.

1. **Algorithmic paper:** proceed if matched calibrated baselines reveal a repeatable, meaningful gain, or a new bound/bias/robustness property with convincing validation.
2. **Systems paper:** proceed if mixed workloads show a meaningful storage/access advantage over a lossless archive plus working copies and indexes, with fidelity and exactness clearly established.
3. **Software/demo paper:** proceed with narrower engineering claims if the implementation is useful and reproducible but algorithmic and systems advantages remain modest.
4. **Reassess the claim:** if gains disappear after matching calibration and prediction, do not retain an algorithmic novelty narrative. Focus on demonstrated integration benefits or develop a stronger method.

Immediate next step: implement P0 comparisons and produce one matched rate–error table plus one predictor/decomposition ablation table. Use those results to choose the paper's thesis before expanding the format.

## 10. After P0 (2026-10-11)

### 10.1 Outcome

[results/p0_benchmark.md](../results/p0_benchmark.md) answers both P0 questions. With calibration and prediction
matched, no algorithmic advantage remains: a calibrated square-root quantizer is 0.1-0.5% smaller than MiniRaw at
the same step, with the same average error. This is §9's "reassess the claim" case.

| Claim | Status after P0 | Novelty |
| --- | --- | --- |
| Smaller hot tier at the same error | Gone against calibrated companding | None: Bernstein et al. [1] |
| Lossless total 1.0-2.5% under JPEG XL | Real; from bin index plus range-coded in-bin position, not the predictor | Weak: lossy-plus-residual precedents [3, 4] |
| Lower reconstruction bias | Real against two weak baselines (see 10.3, route 2) | Not yet shown |
| Worst-case error | Unresolved: depends on whose noise model is the reference | Open (route 1) |
| Layers, region reads, index | Engineering | System contribution |

### 10.2 Why rate at the same guarantee is closed

At step 1.0, about 2.05 of the hot tier's roughly 2.8 bits/pixel is the entropy of quantized sensor noise
(0.5·log2(2πe) for bins one sigma wide; [DEV_FINDINGS.md](DEV_FINDINGS.md)). No coder removes it without
breaking the per-pixel bound. A perfect signal model could save at most the remaining ~0.75 bits/pixel, and JPEG
XL already captures much of it. A real improvement has to change the guarantee or the objective.

### 10.3 Three routes

| Route | What would be new | Gap |
| --- | --- | --- |
| 1. Guarantee against the true sensor noise | Error ≤ k·σ_true with stated confidence, from uncalibrated photos; for example the step chosen from a lower confidence limit on σ, or calibration pooled per camera and ISO across a library | No independent noise reference yet. First step: tripod pairs and dark frames on the A7 IV at about 4 ISOs. |
| 2. Bias-controlled reconstruction within the bound | Near-zero bias under the bound at negligible rate | (a) The per-decile bias metric fails even for perfect per-bin centroids (DEV_FINDINGS); condition on the clean signal or the decoded value instead. (b) Compare against stronger baselines: per-bin empirical centroids, a bias-corrected inverse [1], stored reconstruction offsets. (c) Show it matters with a stacking or averaging experiment. |
| 3. Noise resynthesis | A calibrated, non-learned working copy with regenerated noise, exactly recoverable from the cold tier. Prior work [6] and video film-grain synthesis can't recover the real noise. | Gives up the per-pixel bound to the observation; needs real-noise validation (route 1's data); see 10.4. |

### 10.4 Route 3 feasibility

`bench/experiments/noise_resynthesis.py`, run on 29 files (10 A7 IV, 19 FiveK dev; Leica M8 excluded, where the
model falls back to lossless):
- **Method:** a sliding-window 8x8 DCT denoiser in MiniRaw's transformed domain, on four 512 px crops per plane.
- **Rate:** the denoised estimate quantized at 0.5σ and coded with JPEG XL lossless, against the current L2 on the
  same crops.
- **Detail loss:** ground truth from a 4x-downsampled clean scene with known noise added.
- **Noise realism:** the real residual against a control with ideal model noise.

| | Noisy frames (SNR at 18% < 33; n = 6) | Cleaner frames (n = 23) |
| --- | --- | --- |
| Typical ISO | 800-3200 | 100-400 |
| Hot-tier saving at 0.5σ / 1.0σ (median) | 58% / 75% | 11% / 33% |
| Error against the clean scene, flat / busy texture | 0.32 / 0.72σ | 0.52 / 0.98σ |

- **High-ISO frames gain 2-4x** with the clean estimate well inside the noise, across four camera makes.
- **Cleaner frames gain little.** In busy areas the denoiser can't separate texture from noise. On 6 files the
  denoised estimate at 0.5σ costs more than the current hot tier.
- **Library-level saving is about 21%** (18% on the A7 IV set) if each file uses whichever version is smaller.
- **Noise realism.**
  - Spread and lag-1 correlation match the model.
  - Row banding is 20-40% above ideal on 7 files, mostly older Canons. Synthesis there needs a row-noise
    component.
- **Caveats.**
  - A simple denoiser (BM3D-class methods should do better), crops only, and few noisy frames per camera.
  - The ground-truth scenes are denser in texture than real photos.

So route 3 is viable as an **adaptive high-ISO mode**, not as a general replacement. Its proof needs the same
real-noise reference as route 1.

### 10.5 Recommended order

1. **Real-noise reference** (an afternoon of capture): A7 IV tripod pairs and dark frames at ISO 100-6400. It
   settles the worst-case question and validates route 3's synthesis, including row noise.
2. **Route 2 metric and baselines** (days), usable in either paper.
3. **Route 3 as an adaptive mode**, if step 1 confirms the synthesis on real noise. That makes the most promising
   algorithmic headline.

Meanwhile, the paper available now is the systems paper (§5): layered, region-readable raw storage with honest
model-relative bounds, a lossless total slightly under JPEG XL, and lower bias.

## References and inspected code

1. Bernstein et al. (2010), *Noise and Bias in Square-Root Compression Schemes*. [Paper](https://arxiv.org/abs/0910.4571).
2. Foi et al. (2008), *Practical Poissonian-Gaussian Noise Modeling and Fitting for Single-Image Raw-Data*. [DOI](https://doi.org/10.1109/TIP.2008.2001399).
3. Celik, Sharma, and Tekalp (2003), *Gray-Level-Embedded Lossless Image Compression*. [Author-hosted PDF](https://hajim.rochester.edu/ece/sites/gsharma/papers/LevelEmbedImageComm072003.pdf).
4. Bai et al. (2024), *Deep Lossy Plus Residual Coding for Lossless and Near-Lossless Image Compression*. [Paper](https://arxiv.org/abs/2209.04847), [DOI](https://doi.org/10.1109/TPAMI.2023.3348486).
5. Alvar et al. (2022), *Joint Image Compression and Denoising via Latent-Space Scalability*. [Paper](https://doi.org/10.3389/frsip.2022.932873).
6. Li et al. (2024), *Compress Clean Signal from Noisy Raw Image: A Self-Supervised Approach*. [ICML proceedings](https://proceedings.mlr.press/v235/li24bl.html).
7. JPEG Committee, [JPEG 2000 overview](https://jpeg.org/jpeg2000/index.html).
8. Dotphoton, [Jetraw Core](https://www.jetraw.com/products/jetraw-core). Vendor description; not independent validation.
9. DxO, [DNG compression technical explanation](https://www.dxo.com/en/news/dng-compression). Vendor description; processed linear DNG differs from original Bayer RAW.
10. MiniRaw, [v2 benchmark](https://github.com/Greenere/miniraw/blob/5f21d9965e6eff8d9abab7533995529d3f803ae8/results/v2_benchmark.md), [method](https://github.com/Greenere/miniraw/blob/5f21d9965e6eff8d9abab7533995529d3f803ae8/docs/METHOD.md).

Implementation inspection: [vst.py](https://github.com/Greenere/miniraw/blob/5f21d9965e6eff8d9abab7533995529d3f803ae8/src/miniraw/vst.py), [noise.py](https://github.com/Greenere/miniraw/blob/5f21d9965e6eff8d9abab7533995529d3f803ae8/src/miniraw/noise.py), [codec.py](https://github.com/Greenere/miniraw/blob/5f21d9965e6eff8d9abab7533995529d3f803ae8/src/miniraw/codec.py), [layers.py](https://github.com/Greenere/miniraw/blob/5f21d9965e6eff8d9abab7533995529d3f803ae8/src/miniraw/layers.py), [entropy.py](https://github.com/Greenere/miniraw/blob/5f21d9965e6eff8d9abab7533995529d3f803ae8/src/miniraw/entropy.py), [b5_sqrt.py](https://github.com/Greenere/miniraw/blob/5f21d9965e6eff8d9abab7533995529d3f803ae8/baselines/b5_sqrt.py), [b2_jxl.py](https://github.com/Greenere/miniraw/blob/5f21d9965e6eff8d9abab7533995529d3f803ae8/baselines/b2_jxl.py), [benchmark metrics](https://github.com/Greenere/miniraw/blob/5f21d9965e6eff8d9abab7533995529d3f803ae8/bench/metrics.py), and [benchmark runner](https://github.com/Greenere/miniraw/blob/5f21d9965e6eff8d9abab7533995529d3f803ae8/bench/run.py).
