# MiniRaw format version 2: benchmark

October 11, 2026. Code frozen at commit `945d863` before the confirmation runs. Plan and decision rules:
[docs/v2_advancement.md](../docs/v2_advancement.md) (v2.0: A1, B1, B2, C5, C6). Apple M4 Pro, Python 3.13,
libjxl 0.12.0.

## Summary

Format version 2 makes four changes:
- **No L1** (A1).
- **An `IDX` index thumbnail** placed right after the header (B1).
- **An optional int8 search embedding** in the header (B2).
- **The `csafe` signal-mode reconstruction** (C5).

On images never used to design it (`holdout2`), against format 1 on the same images:

| | Format 1 (v1.0) | Format 2 | Change, per image |
| --- | --- | --- | --- |
| Hot tier (`IDX` included) / JPEG XL lossless | 0.427 | **0.415** | **-1.8%** [-2.0, -1.5] |
| All layers, sensor-exact / JPEG XL lossless | 1.000 (a tie) | **0.987** | -1.2% [-1.4, -1.1] |
| Pixels outside the error bound (steps 1.0 and 2.0) | 0 | 0 | |
| Exact decodes matching the hash | 60/60 | 60/60 | |
| Decile bias (median; spec limit 0.02) | 0.102 | 0.063 | -38% |
| Search index: neighbour overlap@10 with a full render, CLIP / SigLIP (`index500b`) | 0.756 / 0.764 (L1) | **0.841 / 0.860** (`IDX`) | |
| Bytes read to index a file | 88 KB | 16 KB with a stored embedding; 51 KB to re-index | |

**Verdicts against the plan's decision rules:**

| Item | Rule | Result | Verdict |
| --- | --- | --- | --- |
| **A1** no L1 | Smaller hot tier on a fresh split | -1.8% hot tier, -1.2% L2, -1.2% lossless | **Pass** |
| **B1** `IDX` | Overlap@10 ≥ 0.90 under both models, zero-shot within 2 points of the full render, ≤ 40 KB | Overlap 0.841 / 0.860; zero-shot within 1 point on every axis; 34.5 KB median | **Partial: size and zero-shot pass, overlap fails.** Accepted by the owner. |
| **B2** int8 embedding | Negligible loss against float | Overlap -0.004 under both models | **Pass** |
| **C5** bias without violations | Bias ≤ 0.02σ with no bound violations | 0 violations; bias 0.102 to 0.063 (`holdout2`), 0.096 to 0.056 (`holdout`) | **Partial: 40% lower, gate still fails** |
| **C6** D24 and D25 | Written | In [DEVIATIONS.md](DEVIATIONS.md) | **Done** |

**Compatibility.** Format 2 is written by default. A file written by the v1.0.0 release (`tests/data`, encoded by the
tagged code in a separate worktree) decodes bit-identically: exact and signal hashes, and its preview.
`--format-version 1` still writes v1 files. Readers now reject newer format versions instead of guessing.

## What changed and why

| Change | Reason (evidence) | Deviation |
| --- | --- | --- |
| **No L1** in format 2 | L2 was 0.8-3.6% *larger* with L1 in every variant tested, and L1 itself costs about 65 KB (`bench/experiments/l1_index.py`) | D29 |
| **`IDX`** | L1 missed the retrieval bar; a thumbnail rendered from the full image scored far higher (`results/index_benchmark.md`) | D29 |
| **int8 embedding** in the header | Indexing becomes one header read; int8 halves float16's size with negligible loss | D29 |
| **`csafe` reconstruction** | The spec's floor rule sits half a DN low in even bins; the pixel checkerboard fixes that but can break the bound | D30 |

### `IDX`: how it is made

- **Rendering.**
  - Each plane is box-averaged in linear light until its long side is about 1024 px.
  - The reduced mosaic is developed by LibRaw at half size, with camera white balance and orientation applied.
    That is the same pipeline as the benchmark's full render.
  - The result is resized to 512 px on the long side.
- **Rendering matches the reference.** Uncompressed, this thumbnail's CLIP embedding has a median cosine of 0.996
  with the full render, the same as the benchmark's R-small control.
- **The cost is JPEG XL compression.** The codec was chosen on the 20 dev images:

| Thumbnail coding | Size (median) | CLIP cosine to full render | SigLIP cosine |
| --- | --- | --- | --- |
| JPEG XL distance 1.5 | 25 KB | 0.956 | — |
| **JPEG XL distance 1.0 (chosen)** | **33 KB** | **0.975** | **0.993** |
| JPEG quality 85 | 34 KB | 0.915 | 0.977 |
| JPEG quality 90 | 43 KB | 0.935 | 0.986 |
| JPEG XL distance 0.5 | 57 KB | 0.991 | 0.996 |
| JPEG quality 95 | 63 KB | 0.948 | 0.991 |
| JPEG XL lossless | 232 KB | 0.996 | 0.998 |

At equal size JPEG XL beats JPEG for both models. Distance 1.0 was the best setting inside B1's 40 KB budget.

### `csafe`: choosing the reconstruction

**The candidates.** Bins of even size have no centre value. Four rules were compared on the 14 linear dev images:

| Rule | Worst-decile bias (median) | Excluding the top decile (median) | Pixels outside the bound |
| --- | --- | --- | --- |
| Floor (spec, format 1) | 0.100 | 0.075 | 0 |
| Pixel checkerboard of floor and ceiling (D17) | 0.084 | 0.040 | some: 2,749 pixels on the test split |
| Floor and ceiling alternating between bins | 0.081 | 0.059 | 0 |
| **`csafe`: checkerboard only where the ceiling keeps the bound** | **0.084** | **0.040** | **0** |

`csafe` stores one bit per bin, flagging even bins whose ceiling keeps every value within the bound; only those
bins use the checkerboard. It matches the checkerboard's bias with no violations.

**Why the gate still fails.**
- **On smooth synthetic scenes `csafe` meets it:** median 0.016, worst 0.034, against 0.026 and 0.045 for floor
  (3 scenes x 3 noise settings).
- **On real images the remaining bias comes from uneven pixel density within bins:** steep histograms and texture.
  In the worst dev image it sits in the top decile, just below clipping, where pixels pile up toward white inside
  53-DN-wide bins.
- **The bound pins each reconstruction near its bin's midpoint,** so no choice of reconstruction values can follow
  the density. Closing the gate would need narrower bins where density is steep, or a revised definition of the
  gate.

## Protocol

| Split | Images | Role |
| --- | --- | --- |
| dev | 20 | Design: every choice above |
| **holdout2** | 60, first used here | **Confirmation** of sizes and checks, format 1 and 2 on the same images |
| holdout | 60 | Second paired comparison. Seen before (v1's held-out set), not used for v2 design. |
| **index500b** | 500, first used here (493 Bayer) | **Confirmation** of the index |

The configurations are `configs/phase1.toml` (format 1) and `configs/v2.toml` (format 2), identical apart from the
format changes; JPEG XL effort 7 for both. Ratios are medians of per-image ratios with 95% paired-bootstrap
intervals (10,000 resamples, seed 0), on linear sources: images processed in camera are encoded and checked but
left out of size statistics. The index protocol is that of [index_benchmark.md](index_benchmark.md), with two
added arms: `IDX` as the encoder stores it, and `IDX-int8`, its embedding quantized as in the header.

## Results

### Size and fidelity, paired

**holdout2** (fresh; 37 linear of 60 images, 14 cameras):

| Measure (format 2 / format 1, per image) | Median [95% CI] |
| --- | --- |
| Hot tier (L0 + IDX + L1 + L2) | 0.9824 [0.9796, 0.9855] |
| L2 alone | 0.9884 [0.9866, 0.9894] |
| All layers (L1 + L2 + L3) | 0.9883 [0.9859, 0.9893] |

| Check | Format 1 | Format 2 |
| --- | --- | --- |
| Hot tier / JPEG XL lossless (median) | 0.427 | 0.415 |
| All layers / JPEG XL lossless (median) | 1.000 | 0.987 |
| Hot tier / camera file (median) | 0.378 | 0.372 |
| L1 / IDX (median) | 57 KB / — | 0 / 33 KB |
| Exact decodes matching the hash | 60/60 | 60/60 |
| Pixels outside the bound, step 1.0 / step 2.0 | 0 / 0 | 0 / 0 |
| Worst pixel, step 1.0 (median) | 0.569σ | 0.584σ |
| Decile bias (median) | 0.102 | 0.063 |
| Stabilized RMSE (median / max; limit 0.32) | 0.289 / 0.299 | 0.289 / 0.299 |
| Encode time, all layers (median) | 0.64 s | 0.68 s |

**holdout** (40 linear of 60, 14 cameras): hot tier 0.9816 [0.9782, 0.9852], L2 0.9883 [0.9866, 0.9901], all
layers 0.9861 [0.9830, 0.9893]. Against JPEG XL lossless: hot tier 0.472 to 0.464, all layers 1.004 to 0.989.
Bias 0.096 to 0.056. The stabilized-RMSE maximum falls from 0.321 to 0.319, under the 0.32 limit format 1 missed.
60/60 exact, 0 pixels outside the bound.

**dev** (14 linear of 20): hot tier 0.9791 [0.9777, 0.9849]; all layers 0.995x JPEG XL lossless; the synthetic
ground-truth ratio is unchanged (1.0404 against a theory of 1.041).

**Costs.**
- The worst pixel is about 0.015σ higher (median 0.584σ against 0.569σ), because some pixels now decode to the
  ceiling, which is still inside the bound.
- Encode time rises about 6% (0.64 to 0.68 s median, all layers, effort 7) for rendering `IDX`.

Per-split reports: [report_holdout2_v2.md](report_holdout2_v2.md), [report_holdout2_v1.md](report_holdout2_v1.md),
[report_holdout_v2.md](report_holdout_v2.md), [report_dev_v2.md](report_dev_v2.md).

### Search index (index500b, fresh)

| Arm | Neighbour overlap@10, CLIP | SigLIP | Cosine to full render (median), CLIP / SigLIP |
| --- | --- | --- | --- |
| Full render, shrunk, uncompressed (R-small, control) | 0.919 | 0.924 | 0.997 / 0.998 |
| **`IDX`** | **0.841** | **0.860** | 0.980 / 0.992 |
| **`IDX`, int8 embedding (as stored)** | **0.837** | **0.856** | 0.979 / 0.990 |
| L1 at 256 px, half-size render | 0.888 | 0.895 | 0.994 / 0.997 |
| Format 1 default L1 | 0.756 | 0.764 | 0.946 / 0.971 |
| Camera's embedded thumbnail | 0.668 | 0.691 | 0.911 / 0.959 |

Zero-shot accuracy of `IDX` minus the full render, in points with 95% intervals:

| Model | Location | Time | Light | Subject |
| --- | --- | --- | --- | --- |
| CLIP | +0.0 [-1.1, +1.3] | -0.3 [-1.4, +0.6] | -0.4 [-2.4, +1.5] | +0.0 [-1.8, +1.8] |
| SigLIP | +0.2 [-0.4, +0.9] | +0.9 [-0.6, +2.3] | +0.9 [-0.6, +2.4] | +0.0 [-1.5, +1.5] |

The format 1 L1 lost 3.1 points on time and 2.4 on subject under SigLIP; `IDX` loses nothing measurable.

**Index cost.**
- `IDX` is a median 34.5 KB (90th percentile 52 KB, maximum 83 KB).
- Indexing a file with a stored embedding reads the 16 KB header.
- Re-indexing with another model reads header plus `IDX`: median 51 KB, 90th percentile 68 KB, maximum 99 KB.
  Format 1's header plus L1 was a median 88 KB.

**Why overlap falls short.** On 20 dev images `IDX` scored 0.920 / 0.945. With 493 images neighbourhoods are
denser and the metric is stricter. The uncompressed control reaches 0.92, so JPEG XL compression at about 34 KB
costs about 0.07 of overlap. Distance 0.5 (about 57 KB on dev) would likely clear 0.90 but breaks the 40 KB
budget, and would need another fresh split to confirm. The owner accepted B1 at the current setting.

## Limitations

- **Two FiveK confirmation sets** of older cameras (2003-2009); modern sensors are not in the paired runs.
- **Bias.** The gate still fails on real images, and the reason is structural (above).
- **`IDX` is lossy and bakes in one rendering** (LibRaw, camera white balance). That is fine for an index, and it
  can be re-rendered from L2.
- **Neighbour overlap** depends on collection size; the 0.90 bar was set without that in mind.
- **The embedding needs the optional `[index]` dependencies** (OpenCLIP, PyTorch) at encode time; reading it needs
  nothing extra.

## Reproduce

```sh
python -m bench.datasets --split holdout2        # and index500b
python -m bench.run --config configs/phase1.toml --split holdout2 --name holdout2_v1
python -m bench.run --config configs/v2.toml --split holdout2 --name holdout2_v2
python -m bench.experiments.v2_compare holdout2_v1 holdout2_v2 holdout holdout_v2
python -m bench.experiments.index_eval prepare --split index500b    # then embed, metrics
python -m pytest tests/test_v2.py                # format 2 and the v1.0.0 compatibility fixture
```
