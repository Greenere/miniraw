# Changelog

## Unreleased

## v1.0.0 (2026-10-11)

The first stable release. **Format promise:** files written by v1.0.0 with default settings decode bit-identically
in every later version. New features arrive as optional sections or header flags (container format version 1 is
unchanged). The experimental L1 options below will keep decoding, but the encoder may drop them.

### Format and codec (since v0.1.0)
- **Cross-plane L2 prediction (format default).** G2 is predicted from the surrounding G1, and R and B from the surrounding green plus the low-pass colour difference from L1. On the Sony A7 IV set the hot tier is 4.5-5% smaller at the default effort and 6-9% smaller at effort 7. Older files decode unchanged.
- **Restore the original raw file byte for byte.**
  - `miniraw decode IN.mraw OUT.ARW` (the source's own extension, matched case-insensitively) returns the identical file, checked against its SHA-256.
  - Supported where the sensor data is lossless-JPEG tiles: Sony ARW lossless and lossless DNG, any of predictors 1-7.
  - The TIFF structure, maker notes and embedded previews (repacked losslessly) are kept in a new cold `SRC` section. It is stripped and split together with L3.
  - `encode` verifies exact reproduction up front and records why when a file isn't supported. `--no-original` opts out.
- **Raws in their original format without L3.** Decoding to the source extension (or `unbundle`) without the bit-exact layer writes a valid file in the original raw format holding the near-lossless pixels.
  - The original's container (about 0.4 MB) is kept in the hot tier (`CTR` section). Only the large embedded previews stay cold (`SRC`).
  - Tiles are re-encoded with the original Huffman tables and packed into the original tile block, so the file is about the original's size.
  - The full-size preview is regenerated at the highest JPEG quality that fits its original slot.
  - Every written file is checked by reading it back with LibRaw.
- **Noise model only lowered, never raised (D26).** The isotonic fallback was fitting image texture as noise at low ISO, so the hot tier discarded real detail and broke the error bound on 3 of 60 test images. The fallback now only lowers the model where validation finds it too high, uses only well-populated bins, and the affine fit drops bins well above it and bins below σ 0.5 DN. Hot tier about 3% larger on that test set; lossless unchanged.
- **Error bound at large steps (D27).** Bins whose middle DN would miss the bound are split (encoder-only). Step 2.0 had 474,031 violating pixels on the test set, now 0. Steps 0.5 and 1.0 are byte-identical.
- **No crash on planes without a noise estimate** (too few unclipped flat blocks, e.g. a dark Leica M8 frame). They use the DNG noise profile if there is one, else a lossless model.
- **Header `stats` (D28):** SNR at 18% grey, clipped share, mean and log-mean level, all relative to full scale, so they compare across cameras (pooled rank correlation with ISO 0.80, against 0.48 for the raw-DN noise slope). Shown by `miniraw info`.
- **Experimental L1 options** `l1_step` (store L1 coarser) and `g1_pred="none"` (code G1 without L1), off by default and written to the header only when used.
- **Efficiency (byte-identical output).**
  - Files are read selectively with positional reads, so a header, preview or crop no longer loads the whole file (a crop reads 1.4 MB of a 28 MB file), and full signal decodes skip the cold layers.
  - The source file is read once.
  - q is stored as uint16 and the extra full-image copies are gone. Peak memory drops by 13-36% across encode and decode paths.

### Tools
- **`miniraw bundle DIR OUT.zip` / `miniraw unbundle IN.zip DIR`.**
  - Raws are encoded to `.mraw`, JPEGs repacked losslessly with JPEG XL (byte-exact), and PNGs optionally repacked (pixel-exact); everything else is stored.
  - `--cold keep|split|drop` controls the bit-exact layer.
  - A manifest verifies every restored file against its original SHA-256 and restores modification times.
  - `unbundle` restores original raws by default (`--raw-as original`) and falls back to DNG.
  - `--list` shows what a bundle holds.

### Benchmarks, reports and documents
- **Held-out FiveK benchmark** (`results/fivek_benchmark.md`): a frozen configuration run once on 60 unseen images, with the earlier pre-D26 test run kept in `results/report_test_pre_d26.md`.
- **Index benchmark** (`results/index_benchmark.md`, plan in `docs/INDEX_BENCHMARK_PLAN.md`): 498 FiveK images and 9 native raws, CLIP and SigLIP.
- **`docs/METHOD.md`** describes the method as implemented; **`docs/v2_advancement.md`** is the plan for v2.
- New splits (`holdout`, `index500`) and experiments in `bench/experiments`:
  - noise and quantization: `noise_texture.py`, `bounds.py`, `l3_bound.py`
  - coding: `cross_plane.py`, `prod_paths.py`
  - indexing: `index_eval.py`, `index_native.py`, `l1_index.py`, `l1_index_gamma.py`
  - header statistics: `header_stats.py`
  - charts and tables: `heldout_charts.py`, `heldout_summary.py`, `index_charts.py`
- An optional `[index]` dependency group for the index benchmark.
- `bench.report` labels the split and flags uncommitted code in its commit line.
- `bench.datasets` downloads FiveK files whose names contain spaces.

### Measured
- **Sony A7 IV, 33 MP, median of 10 files (the tuning set):**
  - Hot tier 11.7 MB against the 41.2 MB lossless ARW (-72%); worst pixel 0.72σ.
  - Every layer, sensor-exact: 27.7 MB, 2% under JPEG XL lossless.
  - File-exact (the original ARW byte for byte): 30.9 MB (-25%).
- **FiveK holdout, 60 unseen images (40 linear, 14 cameras):**
  - Hot tier 0.41x the camera's file; no pixel outside the error bound at steps 0.5-2.0.
  - Every layer: 1.004x JPEG XL lossless, a tie.
  - Exact decode verified on 60/60.

### Known limitations
- 2x2 Bayer sensors only. X-Trans, Quad Bayer and monochrome files are rejected.
- Byte-exact restore of the original file only for lossless-JPEG-tiled raws (Sony ARW lossless, lossless DNG); others restore as DNG with bit-exact sensor data.
- Lossless ties JPEG XL on held-out cameras. The 2% saving is specific to the A7 IV; one Leica D-LUX 3 frame is 15% larger than JPEG XL.
- The decile bias check fails (median 0.096σ against 0.02) because of the spec's floor reconstruction (D17).
- L1 misses the spec's retrieval bar as a search index (overlap 0.76-0.77 against 0.80), and L2 doesn't benefit from it. The search embedding (spec 4.7) is not implemented.
- The noise estimate can run about 10% high in σ on textured, low-noise images.
- Pure Python with NumPy, JPEG XL and a range coder.

## v0.1.0 (2026-10-10), pre-release

The first public release: a working Phase 1 prototype of the format and tools. The file format may change before v1.0.

### Codec
- Four-layer `.mraw` container:
  - L0: header with sensor metadata, per-plane noise model and tables.
  - L1: base layer, used for previews and indexing.
  - L2: signal layer, quantized to the sensor's own noise.
  - L3: noise layer, enough for bit-exact recovery. It can be stripped, or split off as a `.mraw.l3` sidecar.
- Noise calibrated from a single image, per colour plane, with a fallback for sensors whose noise isn't affine.
- Decode modes: `index`, `preview`, `signal` (every pixel within about half the sensor's noise) and `exact` (bit-identical).
- Random access in 512x512 tiles; decoding a region reads only the tiles it touches.
- Encoding and decoding are multithreaded. Output is identical for any thread count.

### Tools
- `miniraw encode / decode / info / strip / split`.
- `decode` writes DNG (bit-exact sensor data, verified against the original's SHA-256), JPEG, PNG, TIFF, `.npy` or `.json`, chosen by the output file's extension.
- `python -m bench.eval FILE` evaluates MiniRaw on your own raw file. `python -m bench.run` and `python -m bench.report` run the full benchmark.

### Measured (Sony A7 IV, 33 MP, median of 10 files, Apple M1)
- Hot tier (L0-L2): 12.3 MB per photo against 41.2 MB for the lossless ARW (-70%). The worst pixel errs by 0.61 sigma.
- Every layer together, bit-exact: 28.6 MB (-31% against the ARW), within 1% of JPEG XL lossless.
- Encode takes 1.4 s and decode 0.27 s (signal) or 0.66 s (exact).

### Known limitations
- 2x2 Bayer sensors only. X-Trans, Quad Bayer and monochrome files are rejected.
- Exported DNGs are larger than the camera's own files (single-component lossless JPEG).
- CLIP-based search over the base layer is not implemented yet.
- Tested on Python 3.14 on macOS arm64. Other platforms should work but are untested.
