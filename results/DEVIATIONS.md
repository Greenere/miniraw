# Deviations from SPEC.md

Every departure from the spec, with the reason. Numbered for reference from code and report.

## Naming

- **D0. Renamed StrataRAW to MiniRaw.** The package is `miniraw`, files are `.mraw`, sidecars are `.mraw.l3`, the magic is `MRAW` and the CLI is `miniraw`. `SPEC.md` is the original spec with these names substituted; the original is kept in `docs/`.

## Noise estimation (spec 4.2, M1)

- **D1. Per-block statistic: Immerkær 3x3 Laplacian residual instead of horizontal neighbour differences.**
  As specified, the estimator overestimated `a` by 15 to 73% on the synthetic set, worst at low gain. The cause is image texture leaking into the variance: run on the clean planes alone, it reported 16 to 740 DN² of "noise". The Laplacian kernel `[[1,-2,1],[-2,4,-2],[1,-2,1]]` cancels planar gradients. After the switch, clean-plane leakage is 0.13 DN² (the rounding floor) and `a` is within 3% in every synthetic case. Everything else is unchanged (8x8 blocks, MAD, 32 log bins, lowest 20%, Huber fit).
- **D2. Selection-bias correction.** The median of the lowest 20% of block variances sits near the 10th percentile of a noisy estimate, so on its own it is biased low. Binned variances are divided by a constant (0.479), computed once by Monte Carlo on pure Gaussian blocks with seed 0 (`noise.selection_bias()`).
- **D3. Interpretation of "flat blocks must have standard deviation between 0.9 and 1.1".** After the transform, the same binned estimator is applied. Validation passes when every bin with at least 50 kept blocks lies in [0.9, 1.1]. With a 20-block minimum, sampling noise alone pushed bins to 0.89 on synthetic data.
- **D4. Accuracy of `c` is reported only where `c` is identifiable.** That means `c` is at least 20% of the predicted variance in the darkest populated bin. When a scene has no pixels near black, `a·s ≫ c` in every bin, and no single-image estimator can recover `c`. In that case `c` also barely affects the transform over the occupied range. The report gives both the identifiable and the overall figure.
- **D5. DNG NoiseProfile is implemented but untested.** None of the 20 FiveK dev DNGs carries the tag, which was written by older Adobe converters.
- **D26. Lower-only noise-model correction, trimmed affine fit, unidentifiable bins dropped.** Added after the first FiveK test-split run (`results/report_test_pre_d26.md`), which broke the per-pixel error bound on 3 of 60 images and used the isotonic fallback on 47 of 60.
  - **Cause.** At low ISO the noise is small next to image texture, so even the quietest flat blocks carry texture and read too noisy. The spec's isotonic fallback then fitted that texture as noise. It was also built from bins with as few as 8 kept blocks, while validation trusts only bins with at least 50, and it extended its last value flat to the white level. One 14-block bin at σ 94 DN (the affine fit gave 26) set σ for every highlight in a Canon 30D frame. An over-estimated σ discards real signal in the hot tier, and the steep jumps in T broke the bound check by up to 0.05σ.
  - **Evidence.** `bench/synth.py` blurs its clean scenes, so the estimator passed there. `bench/experiments/noise_texture.py` instead keeps texture (4x4 box-downsampled dev planes, no blur, known noise added, residual source noise about 3% of the truth). It reproduced the failure: the fallback fired on 59% of planes, and the worst over-estimate of σ had a median of 1.24, a 90th percentile of 2.9 and a maximum of 28.
  - **Change.**
    - The fallback only lowers the model: where validation finds σ below 0.9 (the model too high), the model becomes min(affine, isotonic over bins with at least 50 kept blocks), stored exactly as a piecewise-linear variance. σ above 1.1 is left alone; it is usually texture, and an under-estimate only costs bits.
    - The affine fit is refitted without bins more than 1.25x above it (one-sided, since texture only inflates).
    - Bins below σ 0.5 DN are left out of the fit: the MAD of integer data can't measure them, and on a Sony A900 plane they collapsed the fit to a = 0.
  - **Result on the textured harness.** Fallback rate 2.1%; worst over-estimate median 1.10, 90th percentile 1.45, maximum 3.3. The remaining tail is planes where texture dominates very low noise (a below 0.1 with c of 25): the fit moves c into a, and the texture also inflates the validation bins, so no single-image flat-block estimator can catch it. The affine slope is still over-estimated by about 20% on this harness, which packs more texture per pixel than full-resolution photos.
- **D27. Bins split where the middle DN would miss the error bound.** The decoder reconstructs at a bin's middle DN (`lo + (n-1)//2`), not at its T-midpoint. T is concave, so in a wide bin the middle DN sits off-centre by an amount that grows with the square of the step. At step 1.0 that stays inside the one-DN slack of the bound. At step 2.0 it missed by up to 0.01σ: 474,031 pixels in 12 of the 60 pre-D26 test images, mostly on plain affine models (Canon 5D Mark II, 14-bit). It was noticed in the first holdout report and diagnosed on a dev image. `vst.forward_table` now splits any bin whose middle DN breaks the bound. It is encoder-only, since the decoder reads just the bin starts. On all 140 dev, test and holdout images, steps 0.5 and 1.0 produce byte-identical tables. At step 1.5, 7 of 560 planes gain 168 bins in total; at step 2.0, 32 planes gain 490.
- **D28. Technical statistics in the header, relative to full scale.** The noise model is stored in raw DN, so the same ISO differed 34x between cameras in the index benchmark (H4). The header now carries `stats` (`src/miniraw/stats.py`, about 120 bytes): SNR at 18% of full scale, the share of clipped pixels, and the mean and log-mean signal level. On the 498 `index500` images (`bench/experiments/header_stats.py`), noise at 18% grey tracks ISO with a pooled rank correlation of 0.80, against 0.48 for the raw-DN slope. The spread between camera medians at the same ISO fell from 19-34x to 5-7x. Log-mean level ranks FiveK night photos at an AUC of 0.90, against 0.83 for the mean level. Dynamic range (full scale over noise at black) was tried and dropped: it depends on the read-noise term, which one image often can't identify (D4), and it spread 19 stops between cameras at ISO 100. L1, L2 and L3 are unchanged.

## Format version 2

- **D29. Format version 2: no L1, an `IDX` index thumbnail, and an optional stored embedding.** Written by default; `--format-version 1` writes v1.0 files, and a file written by the v1.0.0 release decodes bit-identically (`tests/data`). Readers reject newer versions.
  - **No L1.** In every variant tested (`bench/experiments/l1_index.py`), L2 was 0.8-3.6% larger with L1 than without, so the spec's non-redundant base layer was an added cost. Construction B was worse still (D18). Without L1, G3 no longer applies.
  - **`IDX`**, placed first after the header (`src/miniraw/index.py`):
    - Planes are box-averaged in linear light to about 1024 px.
    - The reduced mosaic is developed by LibRaw at half size, resized to 512 px on the long side, and coded with JPEG XL at distance 1.0.
    - Distance 1.0 was chosen on dev: CLIP cosine to a full render 0.975 at 33 KB, against 0.956 at distance 1.5 and 0.915 for JPEG quality 85 at the same size. `preview` decodes it.
  - **Embedding.** `embed` holds a list of {model, dim, dtype, scale, vec}: an int8 OpenCLIP embedding of `IDX` (scale 1/127), 512 bytes for ViT-B/32. It needs the `[index]` extra at encode time (`--embed`). The int8 loss is 0.004 of neighbour overlap.
  - **Result** (`results/v2_benchmark.md`):
    - Hot tier -1.8% per image on the fresh `holdout2` split and on the holdout, and the lossless total -1.2 to -1.4% (0.987-0.989x JPEG XL).
    - `IDX` is a median 34.5 KB. Neighbour overlap with a full render is 0.841 (CLIP) and 0.860 (SigLIP) on the fresh `index500b`, against 0.756 and 0.764 for the format 1 L1, with zero-shot accuracy within 1 point of the full render.
    - The plan's overlap bar of 0.90 is not met; the owner accepted the result.
  - **The spec's claim that the base layer doubles as the index is withdrawn:** a redundant thumbnail is cheaper and better.
- **D30. `csafe` signal-mode reconstruction (format 2 default).**
  - **The rule.** Even-sized bins have no centre value. The spec's floor rule sits half a DN low (D17), and the pixel checkerboard fixes that but can break the bound (2,749 pixels on the test split). `csafe` stores one bit per bin flagging even bins whose ceiling keeps every value within the bound; those bins use the checkerboard, all others the floor.
  - **Dev results.**
    - On dev it matches the checkerboard's bias (median worst decile 0.084, 0.040 excluding the top decile) with no violations.
    - Alternating floor and ceiling *between* bins was also tried, and was weaker (0.059 excluding the top decile).
  - **Confirmation.** On `holdout2` the bias falls from 0.102 to 0.063, and on the holdout from 0.096 to 0.056, with 0 pixels outside the bound.
  - **The 0.02 gate still fails on real images,** though `csafe` meets it on smooth synthetic scenes (median 0.016). The remainder comes from uneven pixel density inside bins, which the bound keeps reconstruction from following.
  - **Cost.** The median worst pixel rises about 0.015σ.

- **D31. Bins that miss the error bound are trimmed, not halved (replaces D27's repair).** D27 halved every bin whose middle DN missed the bound. On a dark high-ISO frame (Sony A7 IV DSC03393), six bins hold 97% of the pixels at step 2.0 and each missed by 0.002-0.024σ; halving them made the hot tier larger at step 2.0 than at 1.5. `vst.forward_table` now trims each failing bin at the top until its middle DN passes, and the leftover starts the next bin. It is encoder-only, since the decoder reads just the bin starts.
  - **Tables** (`bench/experiments/d31_check.py tables`; 210 images: dev, test, holdout, holdout2 and the 10 A7 IV files):
    - Steps 0.5 and 1.0 are unchanged on all 840 planes.
    - At step 1.5, 15 planes in 5 images change (3,474 to 3,249 bins); at step 2.0, 45 planes in 14 images (12,726 to 12,071 bins).
    - Every change has fewer bins, and 0 pixels are outside the bound.
  - **Benchmarks** (format 2 re-runs `holdout_v2_d31`, `holdout2_v2_d31` and `personal_raws_v2_d31` against the stored D27 results):
    - No image changes at steps 0.5 or 1.0.
    - At steps 1.5 and 2.0, 11 image-steps change, all smaller: new/old 0.689-0.926.
    - 0 pixels are outside the bound.
    - The images whose hot tier grew from step 1.5 to 2.0 go from 4 to 0.
  - **Full report:** `results/p0_benchmark.md`, section 3.
## Data (spec 7)

- **D6. Dev split is FiveK only: 20 images, one per camera for the 20 most common models, seed 0.** RAISE downloads require a registration form with name, affiliation and email, plus acceptance of non-commercial terms. That is an owner action, and the legal question in spec 7 is still open. RawNIND has not been downloaded yet. `bench/splits/test.txt` holds 60 FiveK images from the same 20 cameras; it has not been run.
- **D7. Synthetic clean planes.** These are built from dev-image planes, not 50 RAISE images: box-downsample 2x2, then Gaussian blur with sigma 2, then rescale so the 99.9th percentile reaches 90% of the 14-bit range. b = 512, white = 16383. Only 3 scenes are used (x 12 settings x 4 planes), for runtime. A plain 4x4 box left too much source noise and texture in the "clean" planes (see D1).
- **D8. Detecting sources already processed in camera (pitfall 2).** The detector measures the fraction of empty DN codes among well-populated codes (33-code local mean of at least 20 pixels), takes the max over planes, and flags above 0.05. On dev it flags 6 of 20. Nikon D70, D70s, D80 and D100 (lossy NEF curve) and the Leica M8 (8-bit companding) show 70 to 97% empty codes. The Nikon D700 shows 9%: its R and B planes have an empty code every ~10.6 DN, the comb a digital white-balance pre-gain leaves; its G planes are clean. All six are encoded and reported but excluded from the gate table.

## Codec and container

- **D9. Signed L2 residuals.** Each JPEG XL section is prefixed with an int32 offset (its minimum), so `d = q - up(L1)` codes as unsigned 16-bit. That is 4 bytes per section, included in all sizes.
- **D10. Header reservation.** Absolute offsets depend on the header length, so the header is re-serialised until it fits a reservation of its length plus 64 bytes, and the gap is zero-padded. The padding is counted in L0.
- **D11. No trailing EXIF or maker-note blob.** `exiftool` is not installed. Source metadata goes in the header's `source` group.
- **D12. Config is TOML (`configs/phase1.toml`), not YAML.** `tomllib` is in the standard library.

## Metrics and baselines

- **D13. Outlier count (pitfall 7).** A pixel counts as an outlier when it lies more than 6 sigma beyond every one of its 8 same-plane neighbours (above their max or below their min). A median-difference test counted edges and flagged 4.8% of pixels on a textured scene.
- **D14. Stand-in for spec 8.3 and B5.** Rate-distortion uses stabilized-domain RMSE in noise-sigma units, computed with each image's own fitted model, instead of +3 EV render PSNR, MS-SSIM and BD-rate. B5 is compared with MiniRaw at matched stabilized RMSE, using log-log interpolation over the step sweep. Most dev sources are 12-bit, so B5 at 12 bits is skipped for them.
- **D15. Deferred, not built in this pass:**
  - CLIP embedding, `index` and `search`, and the G3 retrieval conditions (overlap@10, zero-shot)
  - B3, B4 and B6
  - Render-based fidelity (noise-equivalence, PSNR, MS-SSIM, Delta E, visual sheet)
  - The empirical L3 model
  - B2 at effort 9 (the config flag exists, off by default)
  - Plane packings P2 and P3
  - Parquet manifest and FAISS
  - Speed section beyond encode and decode times

## Changes after the first, spec-faithful dev run

That run is archived in `results/per_image/dev_specA/`. It used construction A, the floor `rec`, a B5 that clipped below black, and no white-level bins.

- **D17. Reconstruction in `signal` mode (`rec`).** In the spec-faithful run the bias check failed: median 0.100, max 0.341 against a 0.02 limit. There are two causes.
  - **Floor offset.** For even `n`, the spec's `rec = lo + (n-1)//2` sits 0.5 DN below the bin centre. On low-noise sensors (sigma near 1.7 DN, so `n = 2`) that is about -0.3 sigma.
  - **Clipped highlights.** Every pixel at exactly `white` was reconstructed to the centre of a wide top bin (fixed by D20).

  The `checker` alternative is implemented: even bins alternate between floor and ceil of the centre on a pixel checkerboard. It removes the floor offset (30D: 0.187 to 0.022). It also pushes about 0.001% of pixels past the error bound, because for a concave T the ceil sits further from the bin's T-midpoint. The bound is normative and the bias check is a quality check, so the default stays the spec's `floor`. The report shows bias and bound violations under both. The header records `rec`.
- **D18. Choice of L1 construction.** Construction A failed G3 on dev (median 1.048, CI [1.035, 1.077] on 14 images), so construction B was built as spec 4.5 directs. B is a reversible integer 5/3 lifting wavelet applied per 512x512 tile, with log2(k) levels. The LL bands, assembled across tiles, form L1. Each L2 section holds that tile's detail bands in Mallat layout, with the LL area zeroed and coded by JPEG XL lossless. B was worse: G3 median 1.096 [1.089, 1.128]. It also pulled G1 up to 1.038 and G2 up to 0.464, because JPEG XL's predictors code wavelet detail bands less efficiently than pixels. That run is archived in `results/per_image/dev_B_checker/`. A is the dev choice; B remains selectable with `l1_mode = "wavelet"`. The runner also measures A with k doubled and quadrupled, to show how the G3 overhead scales with base-layer size.
- **D19. B5 curve range.** The first B5 subtracted black and clipped below it, so every below-black value collapsed to one code (max errors of 8 to 85 sigma). B5 now applies the sqrt curve over the whole raw range 0..xmax, and the bit sweep extends to 8 and 7 bits so that its distortion range overlaps MiniRaw's step sweep.
- **D20. Single-DN bins at and above the white level.** The forward table forces a bin boundary at `white` and gives every value at or above it its own bin. Clipped pixels carry no noise, so they decode exactly in `signal` mode, add nothing to L3, and stay out of the bias statistic. This applies spec pitfall 6 ("they still code correctly through the table") literally.

- **D21. Plane packing P3.** Every packing was measured on dev for both MiniRaw L2 and B2 (`bench/experiments/packing.py`), each relative to P1:

  | Packing | L2 | B2 |
  | --- | --- | --- |
  | P2 | -0.8% | -0.2% |
  | P2 with G1 first | -0.9% | -0.1% |
  | P3 | -1.5% | -0.4% |
  | Greens together | +7.7% | -0.2% |
  | Effort 9 | -0.9% | -0.6% (at 4.9x encode time) |

  P3, a single 4-channel image per tile, is used for MiniRaw, B2 and B5 alike. The report also keeps B2 under P1 (`B2_P1_bytes`). Effort stays at 7.
- **D22. Smaller base layer: L1 shorter side of at least 128 px instead of 256.** With the spec's rule, G3 failed (1.048). Halving the L1 resolution gave 1.025 [1.014, 1.031] under P1 packing. Under P3 it gives 1.036 [1.013, 1.072], because packing helps q coded directly more than the L1 residual. L1 is then about 57 KB, with a shorter side still above a 224 px CLIP input for most sensors. Halving again (L1 near 64 to 128 px, 18 KB) gives 1.013. That passes G3, but it is probably too small for a good embedding, so it is left as a product decision. The setting is `l1_min_side`.
- **D24. Cross-plane L2 prediction (format default since v1.0).** The spec codes each plane's q minus up(L1) independently. Instead, planes are coded in the order G1, G2, R, B, each as the residual from an integer prediction built from planes already decoded in the same tile (`layers.cross_predict`):
  - G1 from up(L1).
  - G2 from the mean of its neighbouring G1 samples plus the low-pass G2-G1 difference from L1.
  - R and B from the mean of neighbouring G1 and G2 samples plus the low-pass colour difference from L1.

  All arithmetic is integer with rounding offsets, identical in encoder and decoder. Measured in real JPEG XL bytes on the Sony A7 IV set (`bench/experiments/cross_plane.py`), L2 is 5.7-8.9% smaller at effort 7 and 4.5-5.1% smaller at effort 4. Exact decode and byte-identical ARW restore were re-verified. With cross-plane prediction, four separate grayscale images (P1) beat the 4-channel packing (P3) of D21, so `packing = "P1"`; the baselines keep P3. Later measurements (`bench/experiments/l1_index.py`, D29) showed the gain comes from the neighbour terms: the L1 terms make L2 0.8-3.6% larger.
- **Measured, not adopted: a context-modelled L3** (`bench/experiments/l3_context.py`). The context is where the 3x3 mean of signal-mode neighbours falls relative to the pixel's bin. It lowers the ideal L3 cost by a median 0.050 bpp (0.22 bpp on the noise-reduced Leica D-LUX 3), about half of G1's overhead, before the cost of storing tables. That is under 1% of the file and touches only the cold tier, so it is deferred.

## Speed (encode/decode are byte-identical to the earlier code)

- **D23. Faster encode and decode.** A 33 MP Sony A7 IV file on an Apple M1 (`bench/experiments/profile_stages.py`):

  | | Before | After |
  | --- | --- | --- |
  | Encode, effort 7 | 14.6 s | 2.9 s |
  | Encode, effort 4 | — | 1.4 s |
  | Encode, effort 4, cached noise model | — | 0.9 s |
  | Exact decode | 2.7 s | 0.66 s |
  | Signal decode | — | 0.27 s |
  | 1024x1024 region | — | 53 ms |

  The changes:
  - Planes are fitted in parallel, and each tile (residual, L3 range coding, JPEG XL) is a thread-pool job; decode does the same. Output is identical for any thread count, and imagecodecs releases the GIL.
  - Noise blocks are extracted once and reused for validation, residuals use float32 (exact for these integers), and medians use a single partition. Estimates are identical.
  - Re-validating the isotonic fallback is skipped unless `diagnostics=True` (the benchmark runner sets it).
  - L1 is built with a reshape-sum and upsampled separably. Both are bit-identical to the earlier code.
  - The codec and CLI default to JPEG XL effort 4. That gives a 1.7% larger hot tier at half the encode time; the effort curve is in `bench/experiments`. The benchmark config keeps effort 7 for both MiniRaw and the baselines.

- **D25. Production-path efficiency, with identical output.** A review of the implementation (`docs/v1_algo_improvements_guidance_by_chatGPT.md`, response in `docs/v1_suggestions_evaluation.md`) found that every decode read the whole file. The contract for every change was **identical decoded values and identical encoded bytes**, verified on 42 of 42 checks.
  - Reads are selective and positional: a crop of a 28.2 MB file reads 1.4 MB, a header 0.02 MB. Tiles are selected directly.
  - The source file is read once, and the unused preview repack is skipped (76 to 38 MB read), with exact-reproduction verification kept.
  - Arrays are compact: q is uint16, there is no padded L1 copy, mosaics are written directly, and stripping hashes as it goes. Peak memory is 13-36% lower.
  - A partition instead of a sort in the noise estimator was **rejected**: tie-breaking changes the encoded bytes, for about 3% of encode time.
  - The CRC is computed once; a streaming writer is deferred.
  - `bench/experiments/prod_paths.py` measures wall time, peak memory and bytes read per production path, in a fresh process each.

- **D16. Platform.** macOS arm64 (Apple M1) with Python 3.14, not Linux with Python 3.11. libjxl 0.12.0 via imagecodecs, used for both MiniRaw and the baselines.
