# Plan: benchmarking MiniRaw as a search index

October 11, 2026. **Run on October 11, 2026: results in [results/index_benchmark.md](../results/index_benchmark.md),
which lists the changes made on dev before the frozen run.** This plan, kept as written before the run, tests the spec's claim that "the coarsest
layer doubles as the search index" (G3, sections 4.7 and 8.5), against what any photo library can already do.

## The question

MiniRaw's base layer (L1) is a small image that is part of the codestream, not a separate copy. The claim to test
is not that MiniRaw understands images better: meaning comes from the embedding model, and any format with a
thumbnail can feed one. The claims worth testing are narrower.

| # | Hypothesis | Measured by |
| --- | --- | --- |
| H1 | An embedding of L1 is as good for search as one of a full-resolution render. | Neighbour overlap and zero-shot accuracy against the full render |
| H2 | L1 is at least as good as the thumbnail the camera already embeds, which is what photo apps use today. | The same metrics, L1 against the embedded thumbnail |
| H3 | Indexing a MiniRaw file is cheaper and more uniform: one bounded byte-range read at a known offset, for every camera. | Bytes read, number of reads and time per file |
| H4 | The header's calibrated noise model and signal statistics support technical search that EXIF can't. | Exploratory; see the last section |

What is known before starting:
- **The embedding is not implemented.** The header's `embed` field is hard-coded to `None` (`src/miniraw/codec.py`).
  There is no `index` command, and DEVIATIONS lists CLIP, index and search as deferred.
- **Only the size half of G3 has been measured:** (L1 + L2) / L2 coded directly = 1.031 on the FiveK holdout, a
  marginal fail.
- **L1 costs bytes.** L1 is a median 64 KB, and having it costs about 115 KB of file size on 10 MP FiveK images.
  A separate 256-pixel JPEG thumbnail is roughly 15-30 KB.
- **L1 may be too small for the reference model.** On the FiveK holdout it is about 163 x 252 pixels, below
  CLIP ViT-B/32's 224-pixel input. On the 33 MP Sony A7 IV it is larger.
  *(Corrected after the run: those figures are per plane. A developed L1 is twice that, about 366 x 490 on FiveK.
  And L1 is not larger on high-resolution sensors: `k` doubles to keep the shorter side between 128 and 256 px
  per plane, so 33-45 MP sensors get about 146 px.)*
- **The baseline is unusual on FiveK.** The DNGs embed one 256 x 171 *uncompressed* RGB thumbnail (about
  130 KB) and no JPEG preview. Native raws (ARW, CR2, NEF) embed a small JPEG thumbnail plus larger JPEG previews.

## Arms

Every image is embedded in each of these forms, with the same model and the same normalization.

| Arm | Source | Purpose |
| --- | --- | --- |
| **R** (reference) | Full-resolution `rawpy` render: camera white balance, sRGB, default demosaic | The spec's reference (section 8.5) |
| **E** (embedded) | The thumbnail inside the original file (FiveK: the 256 x 171 TIFF thumbnail; native raws: the embedded JPEG) | What existing apps index |
| **L1-128** | L1 at the default `l1_min_side = 128`, rendered by the section 4.7 pipeline | The format's index as shipped |
| **L1-256**, **L1-64** | L1 with `k` halved and doubled | The bytes-versus-quality trade-off left open by D22 |
| **R-small** | R downscaled to L1-128's size | Control: separates "L1 is small" from "L1 is rendered differently" |

The section 4.7 render, from L1 alone:
- Map q back to linear DN with the decode tables.
- Subtract black, apply camera white balance and the colour matrix.
- Scale so mean log-luminance maps to 0.18, apply the sRGB curve, convert to 8 bits.

The existing `--mode preview` decode already renders L1, so start from it. If the colour matrix is missing, log
the file rather than switching pipelines (spec pitfall 13).

**Models.**
- Primary: OpenCLIP `ViT-B-32` (`laion2b_s34b_b79k`), as the spec names.
- Second, for robustness: one model with a larger input (OpenCLIP `ViT-L-14` at 224, or a SigLIP model at 256 or
  384). A small L1 should cost more against the larger input, and the result shouldn't hinge on one model.

## Data

- **Evaluation set: 500 FiveK DNGs.** Name the split `index500` and add it to `bench/datasets.py`.
  - Drawn with a fixed seed from images in none of dev, test or holdout.
  - Stratified by camera model and by the `subject` category.
  - About 5-6 GB.
  - Includes images processed in camera (semantics don't depend on linearity); skips non-Bayer files.
- **Labels: FiveK's four category axes, available for all 5,000 images.**
  - location: indoor or outdoor
  - time: day, dusk or night
  - light: sun_sky, artificial or mixed
  - subject: people, nature, man_made, animals or abstract

  `unknown` is excluded per axis. The labels are coarse and somewhat noisy; the comparisons between arms are
  paired, so label noise affects every arm equally.
- **Development: the 20 dev images.** Use them to debug the pipeline and fix render parameters before touching
  `index500`. The evaluation set is run once with everything frozen. Holdout policy from
  `results/fivek_benchmark.md`.
- **Native-raw supplement for H2 and H3:** the 10 Sony A7 IV ARWs plus one file per camera from raw.pixls.us
  (`docs/BENCHMARK_DATASETS.md`). These measure what E costs and looks like when it is a real camera JPEG, which
  FiveK can't show.

## Metrics

For every arm X other than R, per image and over the collection:

1. **Cosine similarity** between X's and R's embeddings, per image (median and 10th percentile).
2. **Neighbour overlap@10:** for each image, the share of its 10 nearest neighbours under X that are also among
   its 10 nearest under R, averaged over the collection. Also report @5 and @50.
3. **Zero-shot accuracy per axis** with the prompt "a photo of {label}", plus a small fixed template set
   (for example "an indoor photo", "a photo taken at night"). Report the accuracy difference against R and E,
   with paired-bootstrap 95% intervals (10,000 resamples, seed 0).
4. **Text-to-image retrieval:** for each label prompt, recall@10 and average precision over the collection.
   This is closer to how people search than classification.
5. **Index cost (H3)**, per file, measured with the existing `RangeReader` log for MiniRaw and an instrumented
   reader for native files:
   - bytes read and number of reads
   - wall time to obtain the thumbnail
   - time to embed
6. **Storage cost:** L1's marginal bytes against E's bytes, per file.

## Decision rules (fixed before running)

- **H1 holds** if L1-128 reaches overlap@10 of at least 0.80 against R and zero-shot accuracy within 2 points of R
  on every axis. These are the spec's G3 thresholds.
- **H2 holds** if L1-128's zero-shot and retrieval scores are no worse than E's, with the 95% interval of the
  difference above -2 points. If R-small matches R but L1 doesn't, the gap is the rendering pipeline, not the
  size.
- **H3 holds** if indexing a MiniRaw file takes one read of at most 80 KB on every file, and reading E from native
  raws needs more bytes or more reads, or varies by format.
- **If L1-128 fails H1 but L1-256 passes,** report the size cost of L1-256 (G3 size ratio and KB). The default is
  then a product decision between index quality and bytes, not a pass or fail.
- Report every arm whatever the outcome. The README gets a section only if H1 or H2 is decided at the 95% level.

## H4: technical search (exploratory)

The header stores per-plane noise parameters (a, c), black and white levels, and the clipped-pixel fraction can
be stored cheaply too. Questions:
- Does the measured noise slope rank images by real noisiness better than EXIF ISO across cameras? Check that it
  is monotone in ISO within each camera, and how it compares across cameras at the same ISO.
- Does a simple header-only query (low mean signal, high a) retrieve FiveK's `time = night` and
  `light = artificial` images better than chance, and how does it combine with the embedding?

This part has no pass or fail. It shows whether the header has search value beyond the embedding.

## Implementation steps

1. **Section 4.7 in `src/miniraw/index.py`.**
   - Render L1 to an 8-bit thumbnail, reusing the preview path.
   - Embed it with an optional dependency group `[index]` (`open_clip_torch`, CPU `torch`, `faiss-cpu`).
   - Store 512 float16 values plus the model ID in the header's `embed` field. That adds about 1 KB; the header
     stays under its 8 KB target.
   - Encoding without the `[index]` extra writes `embed: None`, as now.
2. **`miniraw index DIR`** reads only L0 (and L1 when an embedding is missing), and writes the spec's Parquet
   manifest and a FAISS index.
3. **`bench/experiments/index_eval.py`**, which:
   - builds the five arms
   - caches embeddings per arm under `results/cache/` (already ignored by git)
   - computes the metrics above and writes `results/experiments/index_eval.csv`
4. **Extracting baselines:** the FiveK TIFF thumbnail with `tifffile`; native raws with `rawpy.extract_thumb`,
   plus an instrumented file object that counts bytes and reads.
5. **Report:** `results/index_benchmark.md`, in the same shape as `results/fivek_benchmark.md`.
6. **Tests:** the L1 render is deterministic; the embedding round-trips through the header; `index` reads no
   bytes beyond L0 and L1 (checked with `RangeReader`).

## Cost

- **Downloads:** about 5-6 GB for `index500`.
- **Encoding:** effort 4 without L3 is enough, since only L0 and L1 matter: roughly 10-15 minutes for 500 files on
  4 workers.
- **Rendering R:** about 1-2 s per image with `rawpy`, so about 5 minutes on 8 workers.
- **Embedding:** ViT-B/32 on CPU (Apple M4 Pro) is a few hundred images per minute per arm, so minutes. A larger
  second model is slower but still under an hour for every arm.
- **No GPU needed.**

## Risks and confounds

- **Rendering differences dominate small-image comparisons.** The R-small control exists to measure this. The
  colour pipelines for R and L1 should match as closely as possible: same white balance, matrix and tone mapping.
- **FiveK's embedded thumbnail is an Adobe render at 256 pixels,** which is better than many in-camera
  thumbnails. That favours E on FiveK; the native-raw supplement covers the other case.
- **Coarse labels can hide differences** (a ceiling effect). The neighbour-overlap and cosine metrics don't
  depend on labels.
- **One model's input size can decide the result.** Hence the second model.
- **Embedding in the file ties it to a model version.** Re-indexing with a newer model still needs only L1
  (about 64 KB per file); measure that path in H3 as well.
