# Changelog

## Unreleased

- **Restore the original raw file byte for byte.**
  - `miniraw decode IN.mraw OUT.ARW` (the source's own extension, matched case-insensitively) returns the identical file, checked against its SHA-256.
  - Supported where the sensor data is lossless-JPEG tiles: Sony ARW lossless and lossless DNG, any of predictors 1-7.
  - The TIFF structure, maker notes and embedded previews (repacked losslessly) are kept in a new cold `SRC` section. It is stripped and split together with L3.
  - `encode` verifies exact reproduction up front and records why when a file isn't supported. `--no-original` opts out.
  - `unbundle` now restores originals by default (`--raw-as original`) and falls back to DNG.
- **Raws in their original format without L3.** Decoding to the source extension (or `unbundle`) without the bit-exact layer now writes a valid file in the original raw format (ARW, ...) holding the near-lossless pixels.
  - The original's container (about 0.4 MB) is kept in the hot tier (`CTR` section). Only the large embedded previews stay cold (`SRC`).
  - Tiles are re-encoded with the original Huffman tables and packed into the original tile block, so the file is about the original's size.
  - The full-size preview is regenerated at the highest JPEG quality that fits its original slot.
  - Every written file is checked by reading it back with LibRaw.

- **`miniraw bundle DIR OUT.zip` / `miniraw unbundle IN.zip DIR`.**
  - Raws are encoded to `.mraw`, JPEGs repacked losslessly with JPEG XL (byte-exact), and PNGs optionally repacked (pixel-exact); everything else is stored.
  - `--cold keep|split|drop` controls the bit-exact layer.
  - A manifest verifies every restored file against its original SHA-256 and restores modification times.
  - `--list` shows what a bundle holds.

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
