# Changelog

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
