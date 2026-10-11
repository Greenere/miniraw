# MiniRaw v2: more compressed, more indexable

October 11, 2026. A research plan, not a commitment. **Status:** v2.0 (A1, B1, B2, C5, C6) is implemented as format version 2;
results in [results/v2_benchmark.md](../results/v2_benchmark.md). A1, B2 and C6 met their rules. B1 met size and
zero-shot but not neighbour overlap (0.84-0.86 against 0.90) and was accepted. C5 cut bias 38-42% without bound
violations but not to 0.02. Every gain below is labelled **measured** (a number from this
repo), **estimated** (derived from a measured bound) or **speculative** (plausible, no evidence yet). How the codec
works today is in [METHOD.md](METHOD.md).

## Where v1 stands

| Goal | v1 on held-out data | Source |
| --- | --- | --- |
| Hot tier (near-lossless) | 0.41x the camera's raw file, 0.47x JPEG XL lossless; worst pixel 0.61σ; no pixel outside the bound | [fivek_benchmark.md](../results/fivek_benchmark.md) |
| Lossless (every layer) | 1.004x JPEG XL lossless: a tie | same |
| Search index | Default L1 misses the retrieval bar: neighbour overlap 0.76-0.77 against 0.80. Indexing reads 67-251 KB. | [index_benchmark.md](../results/index_benchmark.md) |
| Technical search | Header SNR tracks ISO across cameras (rank correlation 0.80) | D28 |

### Where the bits go

Median FiveK holdout image, about 10 MP, linear sources, bits per photosite:

| Part | Size | Headroom |
| --- | --- | --- |
| L0 header | about 3 KB | None worth chasing |
| L1 | 0.05 (about 65 KB) | It costs more than it saves: L2 is 1.2% smaller without it (**measured**, dev) |
| **L2** (the hot tier) | **2.72** | The noise floor is 2.05 against JPEG XL's 2.88 on the A7 IV. On texture-free scenes JPEG XL is still 14-25% above the floor, which is coder inefficiency (**measured**). |
| L3 (cold) | 3.19 | Within 1% of its ceiling at step 1.0 (**measured**: oracle bound and conditional entropy) |
| Lossless total | 5.97 | Floor 5.91 against JPEG XL's 6.87 on the A7 IV |

**The lesson.** L2 is where compression can still be won, and L3 is essentially done. For indexing, the base layer
was the wrong tool. A small purpose-built index costs less and works better (**measured**: a 20-35 KB thumbnail
scores overlap 0.925 against L1's 0.76).

## Invariants v2 must keep

1. **A stated error bound on every pixel** in the hot tier, checked on every pixel.
2. **Bit-exact recovery** with the cold layers, checked by hash.
3. **Integer, table-driven decoding** for the signal and exact paths.
4. **Independent tiles and bounded region reads.**
5. **Old files keep decoding.** New features are optional sections or layout flags, as `l1_step` and `g1_pred`
   already are.
6. **Every claim is confirmed on a fresh held-out split, run once,** with the decision rule written down first.

## Track A: compression

### A1. Take L1 out of L2's prediction (quick win)

- **What.** Predict L2 from neighbouring pixels and planes only.
- **Gain.** L2 is 1.2% smaller on dev (**measured**, `bench/experiments/l1_index.py`), so the hot tier shrinks
  by about 1.2%. If L1 is then not needed for preview (B1), it can shrink or go too, removing about 65 KB more.
- **Risk.** Low. It is a format default change, so re-run the size gates on the holdout and confirm on a fresh
  split.

### A2. A noise-aware L2 coder (the main lever)

**What.** Replace JPEG XL for L2 with a coder that uses what MiniRaw knows and a general-purpose coder doesn't:
- **The noise level.** Unit variance after the transform. JPEG XL's predictors combine several noisy neighbours,
  which multiplies the noise (a gradient predictor triples its variance). Averaging K neighbours costs only
  (1 + 1/K) times the noise.
- **Already-decoded Bayer planes on all sides,** not only causal pixels.
- **A residual model with known variance:** a per-pixel Gaussian or Laplacian mixture whose scale is the noise
  plus a local-texture term, coded with the existing range coder.

**Gain** (**estimated** from the measured bounds):
- L2 8-12% smaller, which takes the hot tier from 0.47x to about 0.42x JPEG XL lossless.
- The lossless total about 4-5% smaller, from 1.00x to about 0.96x JPEG XL.
- That is comparable to RAWIC's reported 7.7% over JPEG XL, which comes from a learned model.

**Plan.**
1. Measure the ideal cost of candidate predictors and residual models on dev, extending `bounds.py`.
2. Build a real coder only for the best one.
3. Decoding must stay row-parallel and fast. In pure NumPy this is the risk: plan for a small native core
   (Rust or C, see C3).

**Decision rule.** Adopt only if the coded L2 is at least 5% smaller than JPEG XL's on a fresh held-out split, at
no more than 2x the decode time.

### A3. Nested quantization for the lossless total

- **What.** Keep the step-1.0 hot tier. Add a cold refinement layer that splits each bin to step 0.5, then L3
  within the finer bins.
- **Gain.** A single step-0.5 encode already makes the lossless total 0.990x JPEG XL (**measured**, 4 files). The
  question is whether a refinement layer keeps that saving without a 30% larger hot tier. **Untested.**
- **Risk.** The refinement layer may cost as much as it saves. Measure its ideal cost first.

### A4. A learned conditional entropy model (later)

- **What.** A small network predicting the distribution of each q from decoded neighbours and L1, which is the
  spec's Phase 2. It is also the only route found to a truly non-redundant base layer: the measurements showed
  JPEG XL can't use L1 as side information.
- **Gain.** Speculative here. RAWIC reports 7.7% over JPEG XL in the raw domain, and the noise-normalized domain may
  be easier to model.
- **Cost.** Training data, model size in the decoder, and decode time without a GPU. Gate it behind A2: if a
  hand-built noise-aware coder captures most of the gap, A4 isn't worth its weight.

### A5. Better noise models from more than one image

- **What.** A noise model has two parameters per camera and ISO, the same for every photo. Estimate them across a
  library: pool per camera and ISO, or use a shipped calibration table, or the DNG profile. Re-fit per image only
  if the image disagrees.
- **Gain.** It removes the main remaining estimator error (σ about 10% high on textured, low-noise images, D26).
  That makes the error bound truthful where it is weakest. The hot tier gets slightly larger where σ was inflated;
  this is correctness, not compression. It also improves the header statistics for technical search.
- **Risk.** The pooled model is wrong for an individual frame (temperature, long exposure). Keep per-image
  validation; only lower, never raise, as in D26.

### A6. Bursts and brackets (speculative)

- **What.** Libraries contain bursts, brackets and near-duplicates. Predict a frame's q from an aligned previous
  frame, with an optional reference to another file in a bundle.
- **Gain.** Unknown. For static bursts it could be large, because the shared scene is coded once and only noise
  remains. Measure it on real bursts before designing anything.
- **Risk.** Inter-file dependencies break "one file is self-contained". Confine them to bundles.

### Not worth pursuing now

- **L3 context models:** under 1% of the file at step 1.0 (**measured**). Revisit only for larger default steps
  or sensors with in-camera noise reduction.
- **JPEG XL effort 9:** 0.9% smaller at 4.9x the encode time.
- **More plane packings:** measured, 1-1.5% (D21).

## Track B: indexability

### B1. An index section and a stored embedding (the main step)

- **What.**
  - Add an optional hot section `IDX`: a thumbnail of about 512 px rendered at encode time from the full sensor
    data, as lossy JPEG XL, about 20-35 KB.
  - Write the embedding into the header (`embed`, spec section 4.7): 512 float16 values, about 1 KB, plus the
    model ID.
  - `preview` decodes `IDX`, falling back to L1 for older files.
- **Gain.**
  - Retrieval quality of a full render: the equivalent arm scored overlap 0.925 under both models (**measured**).
  - Indexing becomes one 16 KB header read; re-indexing with a new model reads the header plus `IDX`, about
    50 KB.
  - The hot tier grows about 1%, which A1 offsets.
- **What it gives up.** The spec's "non-redundant base layer as the index". The evidence says redundancy is
  cheaper here (section 6 of METHOD.md); record it as a deviation.
- **Decision rule.** On a fresh split (`index500b`), adopt if overlap@10 is at least 0.90 under both models and
  zero-shot accuracy is within 2 points of the full render, at no more than 40 KB per image.

### B2. Embeddings that survive model changes

- **What.** Allow several embeddings per file, each tagged with its model ID and dtype, and quantize them to int8
  (512 bytes) or product-quantization codes (64 bytes) to keep the header small.
- **Why.** Embedding models improve every year. A file should carry a current embedding and be cheap to re-embed
  (B1's `IDX`), never require a full decode.
- **Test.** Retrieval quality of int8 and product-quantized vectors against float16 on `index500`.

### B3. Region embeddings (research)

- **What.** Small embeddings per tile or per salient crop, so a search can find a small subject in a large frame
  and jump straight to it with region decode.
- **Gain.** Speculative. Coarse labels like FiveK's can't measure it; it needs object-level queries, such as a
  COCO-style labelled raw set or synthetic insertions.
- **Cost.** About 35 tiles per 33 MP frame, at 64 bytes each with product quantization: about 2 KB.

### B4. Collection index and technical search

- **What.** The spec's `miniraw index DIR`: read only headers, and write a Parquet manifest and a FAISS index of
  the embeddings plus D28's technical fields. Queries combine meaning and measurement, for example "people
  indoors, SNR at 18% grey below 20, under 1% clipped".
- **Test.** Technical labels the index benchmark couldn't evaluate: build a small labelled set (noisy,
  underexposed, clipped, blurred) and measure retrieval from header fields against EXIF.
- **Also.** Near-duplicate and burst grouping from embedding distance. It is useful for culling, and finds the
  candidates for A6.

### B5. Size L1 for its remaining job

- Once `IDX` serves preview and search, L1 exists only if it helps coding. After A1 it doesn't, so it can shrink to
  a few KB or be dropped. If A4 makes L1 genuinely useful to L2, size it for that.

## Track C: reach and credibility

Both tracks need these to make claims that hold beyond FiveK and the A7 IV.

| Item | Why | Notes |
| --- | --- | --- |
| **C1. More data** | FiveK cameras date from 2003-2009; claims need modern sensors and ISO range | NUS-8 raw (RAWIC comparison; needs browser download), RAISE (registration), RawNIND (ISO series), raw.pixls.us (one file per model) |
| **C2. Render-based fidelity** | Hot-tier claims are in σ units only. Photographers see renders. | +3 EV pushed renders: colour difference (ΔE) and PSNR against the exact decode; a visual sheet |
| **C3. A native core** | A2's coder, and faster encode and decode generally | A small Rust or C extension for prediction and range coding; NumPy stays as the reference implementation |
| **C4. Byte-exact restore for more formats** | Adoption: people want their original files back | Canon CR2 and Nikon NEF lossless first; CR3 later. Others restore as DNG with exact sensor data, as now. |
| **C5. Bias without bound violations** | D17's floor reconstruction biases dark low-noise regions; the checker rule breaks the bound on 0.001% of pixels | Alternate floor and ceiling between even-sized bins rather than between pixels, using the ceiling only where the bound allows. Signal it with one bit per even-sized bin in the header (a few hundred bytes). This keeps the bound by construction; whether it brings the per-decile bias under 0.02σ is untested. Measure on dev, confirm on a fresh split. |
| **C6. Documentation debt** | D24 (cross-plane prediction) and D25 (speed-review contract) are cited but missing from DEVIATIONS.md | Write them |

## Roadmap

| Release | Contents | Expected effect (if the decision rules pass) |
| --- | --- | --- |
| **v2.0** | A1, B1, B2 (int8), C5, C6 | Index: overlap 0.76 to about 0.92; index read 83 KB to 16 KB. Hot tier about the same: -1.2% from A1, +1% from `IDX`. |
| **v2.1** | A2 with C3, A3 measured | Hot tier about 0.42x JPEG XL lossless (from 0.47); lossless total about 0.96x |
| **v2.2** | A5, B4, C2, C1 (NUS-8, RAISE, RawNIND), C4 for CR2 and NEF | Truthful bounds on low-noise images; collection search; claims on modern cameras |
| **v3** | A4, A6, B3 | Speculative: learned coder, bursts, region search |

### Targets, confirmed on fresh held-out splits

| Metric | v1 | v2 target |
| --- | --- | --- |
| Hot tier / JPEG XL lossless (median) | 0.47 | ≤ 0.43 |
| All layers / JPEG XL lossless | 1.00 | ≤ 0.97 |
| Pixels outside the error bound | 0 | 0 |
| Exact decodes matching the hash | 100% | 100% |
| Neighbour overlap@10 with a full render (CLIP and SigLIP) | 0.76-0.77 | ≥ 0.90 |
| Bytes read to index a file | 83 KB median | ≤ 16 KB with a stored embedding; ≤ 60 KB to re-index |
| Decile bias (median) | 0.096σ | ≤ 0.02σ with no bound violations |
| Encode and decode time | v1 | ≤ 1.5x v1 at the same effort |

## Risks

- **A2 may not reach 5%** once real coding overhead and speed limits are paid. The bound says 8-12%, and real coders
  lose some of it. The decision rule prevents a slower coder for a marginal gain.
- **Native code** raises the build and maintenance burden. Keep NumPy as the reference implementation, and test
  that the native path is bit-identical.
- **B1 is lossy and bakes in one rendering.** That's acceptable for an index, and a better thumbnail can always be
  re-rendered from L2.
- **Evaluation fatigue.** Every split used for a decision stops being held out. Keep drawing fresh splits (FiveK has
  about 4,300 unused images), and run each once.

## Decisions for the owner

1. **Drop the "non-redundant base layer as index" claim in favour of B1?** The evidence supports it. A4 is the only
   path back.
2. **Accept a native extension (C3)?** A2's full gain likely depends on it.
3. **Format versioning.** v2 sections and flags should bump the header version, with old files decoding unchanged.
   Agree on the policy before A1 changes a default.
4. **Data access.** NUS-8 needs browser downloads and RAISE a registration form; both are owner actions.
