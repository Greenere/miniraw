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
