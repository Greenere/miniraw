# MiniRaw: Implementation and Benchmark Spec

Oct 10, 2026 · @Green

## 1. Purpose and status

This spec defines a Phase 1 prototype of MiniRaw that needs no model training and no GPU, plus the benchmark that decides whether Phase 2 is worth funding. Nothing here has been built or measured; every number is a target or an estimate.

MiniRaw is a layered RAW codestream. The coarsest layer doubles as the search index, and the finest layer holds only sensor noise, so it can move to cold storage or be dropped.

| Layer | Contents | Phase 1 construction | Read cost |
| --- | --- | --- | --- |
| L0 header | Metadata, noise parameters, decode tables, embedding, section table | Binary header, 8 KB target | First byte-range read |
| L1 base | Low-resolution image in the noise-normalized domain | Integer downsample of L2 data, JPEG XL lossless | Second read, tens of KB |
| L2 signal | Full-resolution data quantized to a fraction of local noise sigma | Integer lookup table, then JPEG XL lossless, in tiles | Per-tile reads |
| L3 noise | Exact remainder for bit-exact recovery | Range-coded index within each quantization bin | Optional, cold tier |

**Instructions to the implementing agent.** Build in milestone order (section 10). Do not start a milestone until the previous acceptance tests pass. When a gate in section 2 fails, stop and report the failure with data; do not tune thresholds or the test split to pass. Where this spec offers alternatives, implement the first, measure, and try the next only if the first misses its target. Record every deviation from this spec in `results/DEVIATIONS.md`.

**Honest novelty note.** Noise-shaped tone curves already exist in Nikon and Sony lossy raw modes and in DxO's DNG compression. What Phase 1 tests is three things those lack: a per-image calibrated error bound, an exact-recovery layer at near-zero total cost, and a non-redundant base layer that serves as the index.

## 2. Hypotheses and gates

Phase 1 passes only if all four gates pass on the frozen test split. Sizes are medians over images, in bits per sensor photosite (bpp), with 95% bootstrap confidence intervals. "JXL-lossless" means baseline B2 in section 6.

| Gate | Hypothesis | Pass condition |
| --- | --- | --- |
| G1 Factorization is free | Splitting into signal and noise layers costs almost nothing in total | bytes(L1 + L2 + L3) at step 1.0 is at most 1.03 x JXL-lossless, and L3 decode is bit-exact on 100% of files |
| G2 Hot tier is small | Without L3 the file is far smaller, with error inside the noise floor | bytes(L0 + L1 + L2) at step 1.0 is at most 0.50 x JXL-lossless; every pixel meets the bound in 4.3; quality checks in 8.2 pass |
| G3 Index is free | The base layer adds no meaningful bytes and is enough to search | bytes(L1 + L2 given L1) is at most 1.03 x bytes(L2 coded directly); neighbor overlap@10 is at least 0.80; zero-shot accuracy within 2 points of the reference render |
| G4 Random access works | A region decodes from its own tiles only | Tiling overhead is at most 5% versus untiled; a 1024 x 1024 sensor crop reads only the header and touched tiles |

One further comparison is reported but does not gate. It asks whether calibration saves bytes or only adds a guarantee: the bitrate saving of L2 over baselines B4 and B5 at matched post-render quality (section 8.3). A tie with B5 is a plausible outcome and should be reported as such.

**Expected magnitudes, for sanity checking only.** At step 1.0, unit-variance noise costs about 2.05 bits per sample, so L2 should land near 2.5 to 3.5 bpp at base ISO. L3 should cost about log2 of the noise sigma in raw units, roughly 3 to 5 bpp. JXL-lossless should land near 7 to 9 bpp for 14-bit data. Results far outside these ranges suggest a bug before they suggest a discovery.

## 3. Scope

Phase 1 covers 2 x 2 Bayer sensors, still images, CPU only, in Python.

**In scope**

- Encoder and decoder for layers L0 to L3, with a single-file container.
- Noise parameter estimation from a single image.
- Tiled random access and region decode.
- Embedding-based search over a collection, using only L0 and L1.
- Baselines B0 to B5 and the benchmark harness and report.

**Out of scope for Phase 1**

- Non-Bayer sensors (X-Trans, quad-Bayer, monochrome). Detect and skip them, and count the skips.
- Any trained component. Phase 2 (milestone M7) adds a learned entropy model and a distilled embedder.
- Writing standard DNG output, video, and encode speed optimization.
- Running proprietary codecs such as TicoRAW. Published file sizes may be quoted as external reference only.

## 4. Codec specification

Every decode step uses integer arithmetic and tables stored in the file, so decoding is bit-exact on any platform. Floating point is allowed only in the encoder.

### 4.1 Ingest

1. Open the file with `rawpy`. Read `raw_image_visible` (uint16), `raw_pattern`, `black_level_per_channel`, `white_level`, `camera_whitebalance` and the camera color matrix.
2. Reject the file if `raw_pattern` is not 2 x 2, and log the reason.
3. Crop to even width and height.
4. Split the mosaic into four planes in the fixed order R, G1, G2, B, each of size H/2 x W/2. Store the permutation needed to rebuild the mosaic.
5. Keep values as raw integers (DN). Never subtract black level in the coded domain.

### 4.2 Noise model

Per plane, variance is affine in signal: `a` is gain in DN per electron, `b` is black level, `c` is read-noise variance in DN squared.

```latex
\mathrm{Var}(x) = a \cdot \max(x - b,\, 0) + c
```

Parameter sources, in priority order:

1. **DNG `NoiseProfile` tag**, when present. It gives variance as S x + O for x normalized to 0..1, so a = S (w - b) and c = O (w - b)^2, with w the white level. Cross-check against the estimator below and flag files that disagree by more than 2x.
2. **Single-image estimator.**
   1. Split each plane into 8 x 8 blocks. Drop blocks containing any pixel at 0 or above 0.98 w.
   2. Per block, compute the mean and a robust variance: 1.4826 x MAD of horizontal neighbor differences divided by sqrt(2), squared.
   3. Sort blocks into 32 log-spaced intensity bins. In each bin keep the 20% of blocks with lowest variance and take their median.
   4. Fit the affine model with Huber-weighted least squares, constrained to a > 0 and c >= 0.
   5. Validate: after the transform in 4.3, flat blocks must have standard deviation between 0.9 and 1.1. On failure, replace the affine model with an isotonic (monotone) fit of the binned variances and build the table in 4.3 by numerical integration of 1 / sigma(x).

### 4.3 Variance-stabilizing transform and tables

The generalized Anscombe transform maps the data to approximately unit noise variance. Below black level, extend it linearly with the slope it has at b.

```latex
T(x) = \frac{2}{a}\sqrt{a\,(x-b) + \tfrac{3}{8}a^{2} + c}, \qquad x \ge b
```

The encoder builds one forward table per plane over every integer x from 0 to max(white level, largest observed value). The quantization step is a multiple of the noise sigma, default 1.0.

```latex
v_0 = 0, \qquad v_x = v_{x-1} + \min\!\left(1,\ \frac{T(x) - T(x-1)}{\Delta}\right), \qquad q(x) = \left\lfloor v_x + \tfrac12 \right\rfloor
```

The cap at 1 means q rises by 0 or 1 per raw value. Every q is therefore used, bins are contiguous, and where noise is below one DN the mapping is already lossless.

From the forward table derive, per q: `lo[q]` (smallest x in the bin), `n[q]` (bin size) and `rec[q] = lo[q] + (n[q] - 1) // 2`. Store only `lo` in the header, delta-coded and zstd-compressed. These stored tables are normative; the decoder never evaluates T.

**Error bound, tested on every pixel:** |T(x) - T(rec\[q(x)\])| <= step / 2 plus one DN of slack. Expected noise increase from quantization is 1.0% of sigma at step 0.5, 4.1% at 1.0, 9.0% at 1.5 and 15.5% at 2.0.

### 4.4 L2 signal layer

L2 carries the four q planes, tiled at 512 x 512 in plane coordinates (1024 x 1024 on the sensor), each tile coded independently with JPEG XL lossless at effort 7.

Plane packing options, chosen on the dev split only:

- P1: four separate grayscale images. Implement first.
- P2: one 3-channel image (R, G1, B) plus one grayscale image (G2), letting the codec decorrelate channels.
- P3: one 4-channel image, if the encoder binding supports extra channels.

The same packing search is applied to baseline B2, so neither side gets an advantage.

### 4.5 L1 base layer

L1 is a small image derived from q that a reader can fetch without touching L2. Let k be the largest power of two that keeps the shorter plane side at or above 256.

- **Construction A (implement first).** L1 is the integer floor-mean of q over k x k blocks, coded whole with JPEG XL lossless. L2 then codes d = q - up(L1) in place of q. Implement two integer-only upsamplers, nearest-neighbor and fixed-point bilinear, and pick on the dev split.
- **Construction B (only if A fails G3).** A reversible integer 5/3 lifting wavelet with log2(k) levels. The LL band is L1, and the detail bands are coded per tile.

The G3 denominator is q coded directly as in 4.4, with no L1.

### 4.6 L3 noise layer

For each pixel, L3 stores its position inside its quantization bin: `kappa = x - lo[q]`, an integer in 0..n\[q\]-1. Bins of size 1 cost nothing.

- Code kappa with a range coder (the `constriction` library) under a categorical model per plane and per q. Start with a uniform model over n\[q\].
- Then try an empirical model: bucket q into 64 groups, send a normalized-position histogram per group in the tile header, and keep it only if it lowers total bytes on the dev split.
- Tile L3 exactly as L2 so regions decode exactly too.
- Report actual bytes and the ideal cost, the sum of log2 n\[q\] over pixels.

Exact decode is `x = lo[q] + kappa`.

### 4.7 L0 embedding

The embedding is computed from L1 alone, so indexing a file never requires reading or decoding L2.

1. Map L1 through `rec`, subtract black, apply white balance, average G1 and G2, and apply the camera-to-sRGB matrix.
2. Scale so mean log-luminance maps to 0.18, apply the sRGB curve, clip, convert to 8-bit.
3. Embed with OpenCLIP `ViT-B-32` (`laion2b_s34b_b79k`), L2-normalize, and store as 512 float16 values. The model is configurable and its identifier is written to the header.

### 4.8 Decode modes

| Mode | Layers read | Output |
| --- | --- | --- |
| `index` | L0 | Metadata and embedding |
| `preview` | L0, L1 | 8-bit thumbnail |
| `signal` | L0, L1, L2 | Mosaic with x = rec\[q\]; with `--dither`, a seeded uniform draw within each bin |
| `exact` | L0 to L3 | Original mosaic, bit for bit |

All modes except `index` and `preview` accept `--region x,y,w,h` in sensor coordinates.

## 5. Container and random access

One `.mraw` file holds all layers in the order L0, L1, L2, L3, original metadata, so truncating the file removes the cold layers.

**Fixed preamble (16 bytes):** magic `MRAW`, version (u16), flags (u16), header length (u32), header CRC32 (u32). Flag bit 0 means L3 is present.

**Header:** zstd-compressed MessagePack with these groups.

| Group | Fields |
| --- | --- |
| `sensor` | Width, height, CFA permutation, black levels, white level, white balance, color matrix, bit depth |
| `source` | Camera make and model, ISO, original file name, SHA-256 of the original mosaic |
| `noise` | Per plane a, b, c; model type (affine or isotonic); quantization step |
| `tables` | `lo` per plane, delta-coded |
| `layout` | Tile size, k, upsampler, plane packing |
| `embed` | Model identifier, dimension, dtype, vector bytes |
| `sections` | Per section: layer, tile x and y, absolute offset, length, CRC32 |

**Rules**

- Header size target is 8 KB. A reader issues one 16 KB range read first, and a second only if the header is longer.
- All offsets are absolute. `miniraw strip` truncates at the L3 offset and clears flag bit 0. `miniraw split` writes L3 to a `.mraw.l3` sidecar for cold storage.
- EXIF and maker notes from the source file are stored verbatim as a trailing blob.
- Region decode maps the requested rectangle to tiles, reads the L1 section plus those tiles, and nothing else.

**Collection manifest.** `miniraw index DIR` reads only L0 of each file and writes a Parquet manifest (path, dimensions, camera, ISO, noise parameters, layer sizes, embedding) and a FAISS inner-product index.

**Verification hook.** All file access goes through a reader class that logs every byte range. Tests for G4 assert on that log.

## 6. Baselines

B2 is the reference for the gates; B4 and B5 are the lossy competitors that decide whether calibration earns its complexity.

| ID | Baseline | How to run | Role |
| --- | --- | --- | --- |
| B0 | Uncompressed | Sensor bit depth as bpp | Sanity anchor |
| B1 | Camera-native lossless | Raw strip byte count from the source file via `exiftool`, previews excluded | Real-world anchor |
| B2 | JPEG XL lossless on raw planes | Same tiling and packing search as L2, effort 7; also report effort 9 | Gate reference |
| B3 | Lossless-JPEG DNG (optional) | `dnglab convert`, if it installs cleanly | Legacy DNG anchor |
| B4 | JPEG XL lossy on raw planes | 16-bit input, distance sweep 0.05, 0.1, 0.2, 0.3, 0.5, 1.0 | DNG 1.7 lossy analogue |
| B5 | Uncalibrated square-root curve | Fixed sqrt curve to 12, 11, 10 and 9 bits, then JPEG XL lossless | Camera-style tone-curve analogue |
| B6 | RAWIC (optional, GPU) | Public code at github.com/chunbaobao/RAWIC | Learned lossless reference |

**External reference, not a baseline.** Nikon Z9 files are reported at 55.1 MB lossless, 33.0 MB for High Efficiency\* and 22.0 MB for High Efficiency ([Photography Life](https://photographylife.com/nikon-z9-lossless-vs-high-efficiency-raw-files)). On a 45.7 MP sensor that is roughly 9.6, 5.8 and 3.9 bpp. These come from a different camera and codec we cannot run, so the report may quote them but must not claim a head-to-head win.

**Fairness rules.** Use one libjxl version for everything and record it. Use the same effort, tiling and thread count for MiniRaw and baselines. Choose every tunable on the dev split.

## 7. Datasets and splits

Three public RAW sets plus one synthetic set cover the main benchmark, cross-camera behavior, ISO behavior and ground-truth checks.

| Dataset | Contents | Used for | Notes |
| --- | --- | --- | --- |
| [RAISE-1k](https://loki.disi.unitn.it/RAISE/download.html) | 1,000-image subset of 8,156 Nikon NEF files (D7000, D90, D40), seven category tags | Main compression benchmark; retrieval labels | Licensed for non-commercial research and education only |
| [MIT-Adobe FiveK](https://huggingface.co/datasets/yangkun2197/MIT-Adobe-FiveK) | 5,000 DNG files from many cameras, with semantic categories | Cross-camera generalization; retrieval labels | Use a 300-image subset stratified by camera model |
| [RawNIND](https://arxiv.org/pdf/2501.08924) | Same scenes at several ISO values on several cameras | Rate versus ISO; noise estimator validation | Skip non-Bayer files; confirm hosting and terms at download |
| Synthetic | Clean planes with simulated Poisson-Gaussian noise at known parameters | Unit tests; estimator accuracy; quality against ground truth | Generated locally |

**Synthetic generation.** Take 50 base-ISO RAISE images and box-downsample each plane 4 x 4 to suppress noise; treat the result as clean. For each a in {0.3, 1, 3, 10} and c in {1, 9, 100}, generate x = b + a x Poisson(clean / a) + Normal(0, c), rounded and clipped to 14 bits. Generate two independent noise draws per setting.

**Splits, committed as file lists with seed 0**

- Dev: 20 RAISE and 20 FiveK images. All tuning happens here.
- Test: 300 RAISE images (D7000, 14-bit lossless only), 300 FiveK images, and every usable RawNIND Bayer file.
- The two splits share no image and no scene.

**Open question for the owner.** RAISE terms allow non-commercial research only. Confirm with legal before running this inside a company, or substitute in-house captures for that row.

## 8. Metrics and evaluation protocol

Every metric is computed per image and written to CSV; the report aggregates from those files only.

### 8.1 Rate

bpp = 8 x bytes / (sensor width x height), reported per layer and in total. Aggregate as median, mean, 10th and 90th percentile, broken down by dataset, camera and ISO bucket (up to 200, 400 to 800, 1600 to 3200, 6400 and above).

### 8.2 Fidelity of `signal` mode (quality checks for G2)

| Check | Definition | Pass at step 1.0 |
| --- | --- | --- |
| Bound | Error bound of 4.3 on every pixel | 100% of pixels |
| Bias | Mean of (decoded - original) / sigma, per intensity decile | Absolute value at most 0.02 |
| Stabilized RMSE | RMSE of T(original) - T(decoded) | At most 0.32 (theory: 0.289) |
| Ground truth | On synthetic data, RMSE(decoded, clean) / RMSE(noisy, clean) in stabilized units | At most 1.05 (theory: 1.041) |
| Noise-equivalence | On synthetic data, render PSNR(decoded, original) minus render PSNR(noise draw 1, noise draw 2) | At least 6 dB (theory: about 13.8 dB) |

Rendered comparisons use `rawpy.postprocess` with identical parameters on the original and on the decoded mosaic, which is written back into `raw_image_visible` before rendering. Produce three renders: default with camera white balance, a +3 EV push (`exp_shift=8.0`), and the darkest tile at +3 EV. Report PSNR, MS-SSIM, and mean and 99th-percentile Delta E 2000.

Save a visual sheet of 12 crops at 4x zoom to `results/visual/`, each showing original, decoded, and decoded with dither.

### 8.3 Rate-distortion comparison

Run MiniRaw at steps 0.5, 1.0, 1.5 and 2.0, and B4 and B5 across their sweeps. Plot bpp against +3 EV render PSNR and MS-SSIM. Compute BD-rate of MiniRaw against B4 and against B5.

### 8.4 Exactness (G1)

The SHA-256 of the `exact` decode must equal the header hash for every file. Repeat through region decode on five random regions per file.

### 8.5 Index quality (G3)

The reference embedding uses the same CLIP model on a full-resolution default `rawpy` render.

- Cosine similarity between L1 embedding and reference, per image.
- Neighbor overlap@10: mean fraction of shared top-10 neighbors between the two embedding sets across the test collection.
- Zero-shot accuracy on RAISE tags and FiveK categories with the prompt "a photo of {label}", for both embedding sets.
- Bytes read to index one file and to preview one file, from the reader log.

### 8.6 Speed (informational)

Encode and decode throughput in megapixels per second, single thread, per mode; region decode latency; files indexed per second. Record CPU model and library versions.

### 8.7 Statistics

Use a paired bootstrap over images with 10,000 resamples for every ratio. A gate passes on the median point estimate. If the 95% interval straddles the threshold, label the gate "marginal" in the report.

## 9. Repository, CLI and environment

A single Python package with the codec in `src/`, baselines and benchmark code beside it, and results written to a directory that is never edited by hand.

```text
miniraw/
  pyproject.toml
  README.md
  SPEC.md                 # export of this document
  configs/phase1.yaml     # step, tile size, effort, model id, paths
  src/miniraw/
    io_raw.py             # rawpy ingest, CFA packing, metadata
    noise.py              # NoiseProfile reader, single-image estimator
    vst.py                # transform, forward table, lo/n/rec tables
    layers.py             # L1 pyramid, L2 tiles, L3 tiles
    entropy.py            # JPEG XL wrapper, range coder wrapper
    container.py          # .mraw reader/writer, logging range reader
    index.py              # thumbnail render, embedding, manifest, FAISS
    codec.py              # encode(), decode() public API
    cli.py
  baselines/              # b1_native.py ... b5_sqrt.py
  bench/
    datasets.py           # download, filter, split lists
    synth.py              # synthetic set generator
    metrics.py            # section 8 metrics
    run.py                # per-image CSV writer, resumable
    report.py             # tables, plots, gate verdicts
    splits/dev.txt, test.txt
  tests/
  results/
```

**CLI**

```text
miniraw encode IN.NEF OUT.mraw [--step 1.0] [--no-l3]
miniraw decode IN.mraw OUT.npy --mode index|preview|signal|exact [--region x,y,w,h] [--dither]
miniraw info IN.mraw
miniraw strip IN.mraw | miniraw split IN.mraw
miniraw index DIR --out manifest.parquet
miniraw search manifest.parquet "text query" [-k 10]
python -m bench.run --config configs/phase1.yaml --split dev|test
python -m bench.report --out results/report.md
```

**Environment**

- Python 3.11 or later, Linux, CPU only for milestones M0 to M6.
- Packages: `rawpy`, `numpy`, `scipy`, `imagecodecs` (JPEG XL), `constriction`, `zstandard`, `msgpack`, `open_clip_torch`, `torch` (CPU build), `faiss-cpu`, `scikit-image`, `colour-science`, `pandas`, `pyarrow`, `matplotlib`, `pytest`.
- System tools: `exiftool`; `cjxl` and `djxl` as a fallback JPEG XL path through 16-bit PGM files.
- Pin all versions in a lock file and print them at the top of the report.
- Fixed seed 0 everywhere. `bench.run` must be resumable and must skip images whose CSV row already exists.

## 10. Milestones and acceptance tests

Seven milestones run in order; each ends with tests in `tests/` that must pass before the next begins. M7 starts only if all four gates pass.

| Milestone | Build | Acceptance test |
| --- | --- | --- |
| M0 Scaffold and data | Package skeleton, dataset download and filtering, split lists, synthetic generator | Pack then unpack reproduces the mosaic exactly on all dev images; JPEG XL 16-bit lossless round-trip is exact through the chosen binding |
| M1 Noise and transform | `noise.py`, `vst.py` | On synthetic data, estimated a within 10% and c within 25% (or 1 DN squared); flat-block sigma after transform within 0.9 to 1.1; forward table is monotone with increments of 0 or 1 |
| M2 L2, L3 and B2 | Quantize, tile, code; exact decode; baseline B2 | Exact round-trip on every dev image; error bound holds on every pixel; G1 and G2 size conditions evaluated on dev and printed |
| M3 Container and regions | `.mraw` format, logging reader, region decode, strip and split | Region decode equals the same crop of a full decode; reader log shows only header, L1 and touched tiles; stripped file still decodes in `signal` mode |
| M4 L1 and index | Pyramid, thumbnail, embedding, manifest, search | G3 size condition evaluated on dev; indexing reads at most 16 KB per file; search returns results from the manifest alone |
| M5 Lossy baselines and quality | B1, B4, B5 (B3 and B6 if feasible); render metrics; step sweep | Section 8.2 checks pass on dev at step 1.0; rate-distortion plots render for all methods |
| M6 Full benchmark | Run test split once; generate report | `results/report.md` exists with a pass, fail or marginal verdict for G1 to G4 and all per-image CSVs |
| M7 Phase 2 (gated, GPU) | Learned conditional entropy model for L2 given L1; embedder distilled from CLIP that reads L1 directly; signal-only mode with noise resynthesis | Separate spec, written after the Phase 1 report |

**Stop conditions.** If M2 shows total size above 1.10 x B2 on dev, stop and report before building the container. If the estimator in M1 fails on more than 10% of dev images, stop and report the failure modes.

## 11. Known pitfalls

These are the mistakes most likely to produce a wrong result that looks right.

1. **Embedded previews inflate baselines.** Camera files carry JPEG previews. B1 must count raw strip bytes only.
2. **Already-lossy sources.** Some files (Nikon D90 12-bit compressed, Sony compressed ARW) were tone-curve quantized in camera, so their histograms have gaps. Detect gaps, exclude these from the main tables, and report them separately.
3. **Float in the decoder.** Any float math at decode breaks exact recovery across platforms. Decode uses stored tables and integer upsampling only. Add a test that decodes with `numpy` float operations disabled or absent from the decode path.
4. **Values outside the nominal range.** Pixels below black level and above white level exist. The forward table must cover every observed value.
5. **Per-plane parameters.** Fit noise parameters for each of the four planes. Do not share them across R, G and B.
6. **Clipped highlights.** Saturated regions have near-zero variance and bias the fit. Exclude them from estimation; they still code correctly through the table.
7. **Hot pixels and fixed-pattern noise.** These cost extra L2 bits and are not modeled. Count outliers above 6 sigma and report their share of bytes.
8. **Non-affine sensors.** Dual-gain sensors and in-camera noise reduction break the affine model. The isotonic fallback in 4.2 covers this; report how often it triggers.
9. **Lossy JPEG XL is not bit-exact across decoders.** Never stack an exact layer on a lossy JPEG XL layer. Phase 1 uses lossless JPEG XL inside MiniRaw only.
10. **Histogram combing.** `signal` mode output has gaps in its histogram. Check banding in +3 EV renders of smooth gradients, with and without `--dither`.
11. **Test-split leakage.** No parameter, packing choice or model is selected on the test split. The test split is run once, in M6.
12. **Unequal encoder effort.** A baseline at effort 3 against MiniRaw at effort 7 is not a result. Assert equal settings in the harness.
13. **Thumbnail color.** If the camera matrix is missing, render without it and log the file; do not silently fall back to a different pipeline for the reference embedding.

## 12. Deliverables and kickoff

The run ends with one report that states a verdict per gate and lets a reader trace every number to a per-image CSV.

**Deliverables**

- The repository, with all tests passing.
- `results/per_image/*.csv`: one row per image, method and setting.
- `results/report.md` containing, in this order:
  1. Environment, library versions, hardware, commit hash.
  2. Gate table: G1 to G4, measured value, threshold, 95% interval, verdict.
  3. Rate table per layer and baseline, by dataset and ISO bucket.
  4. Rate-distortion plots and BD-rate against B4 and B5.
  5. Fidelity checks of 8.2 and the visual sheet.
  6. Index results of 8.5.
  7. Failures: skipped files with reasons, estimator fallbacks, outliers.
  8. A recommendation on Phase 2, with the evidence for it.
- `results/DEVIATIONS.md`: every departure from this spec and why.

**Kickoff prompt for Claude Code**

```text
Read SPEC.md in full before writing code. Implement MiniRaw Phase 1
exactly as specified, milestone by milestone (section 10). After each
milestone, run its acceptance tests and show me the output before
continuing. Do all tuning on the dev split; run the test split once, in M6.
If a stop condition or gate fails, stop and report with data; do not
change thresholds or the spec to make it pass. Log every deviation in
results/DEVIATIONS.md. Start with M0: scaffold the repo, verify that the
JPEG XL binding round-trips 16-bit data losslessly, and download only
the dev split first. Ask me before downloading more than 20 GB.
```

## Sources

- [Photography Life: Nikon Z9 lossless vs High Efficiency RAW](https://photographylife.com/nikon-z9-lossless-vs-high-efficiency-raw-files)
- [DxO: high-fidelity DNG compression](https://www.dxo.com/news/dng-compression/)
- [Points in Focus: raw compression strategies](https://pointsinfocus.com/?p=16966)
- [RAWIC: Bit-Depth Adaptive Lossless Raw Image Compression](https://sotaverified.org/papers/260328105)
- [RAISE dataset](https://loki.disi.unitn.it/RAISE/)
- [MIT-Adobe FiveK dataset](https://huggingface.co/datasets/yangkun2197/MIT-Adobe-FiveK)
- [RawNIND paper](https://arxiv.org/pdf/2501.08924)
- [cjxl manual](https://dyn.manpages.debian.org/trixie/libjxl-tools/cjxl.1)
