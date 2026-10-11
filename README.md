# MiniRaw

A layered RAW codestream, Phase 1 prototype (formerly "StrataRAW"; see [SPEC.md](SPEC.md)).

| Layer | Contents |
| --- | --- |
| L0 header | Sensor metadata, per-plane noise model, quantisation tables (`lo`), section table |
| L1 base | Low-resolution image in the noise-normalised domain (also the preview/index source) |
| L2 signal | Full resolution, quantised to a fraction of local noise sigma, tiled JPEG XL lossless |
| L3 noise | Exact remainder inside each quantisation bin, range-coded, tiled; can be stripped or split |

Decode modes: `index` (L0), `preview` (L0+L1), `signal` (L0..L2, error bounded by the noise model),
`exact` (L0..L3, bit-exact). `signal` and `exact` support region decode that reads only the touched tiles.

## Setup

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest              # M0-M3 acceptance tests on synthetic data
```

## CLI

```sh
miniraw encode IN.dng OUT.mraw [--step 1.0] [--no-l3]
miniraw decode IN.mraw OUT.npy --mode index|preview|signal|exact [--region x,y,w,h] [--dither] [--sidecar F.l3]
miniraw info IN.mraw
miniraw strip IN.mraw        # drop L3 in place
miniraw split IN.mraw        # move L3 to IN.mraw.l3
```

## Try it on your own raw file

```sh
python -m bench.eval PATH/TO/IMG.NEF                  # sizes, checks, step sweep, B5 comparison
python -m bench.eval IMG.CR2 --render out/ --out IMG.mraw   # + render comparison crops, keep the .mraw
python -m bench.eval IMG.ARW --quick                  # skip the sweep and B5
```

Any 2x2-Bayer raw that LibRaw (rawpy) opens works: NEF, CR2, CR3, ARW, DNG, RAF (Bayer models), ORF, RW2 and others.
Use the camera's lossless or uncompressed raw mode: lossy raw modes (e.g. Nikon "compressed", Sony "compressed")
have already discarded data, and the script warns when it detects that.

## Benchmark

```sh
python -m bench.datasets --split dev        # 20 FiveK DNGs, one per camera (~200 MB)
python -m bench.run --split dev             # per-image CSVs in results/per_image/dev/ (resumable)
python -m bench.report --split dev          # results/report.md
```

Settings live in `configs/phase1.toml`. Every departure from the spec is in
[results/DEVIATIONS.md](results/DEVIATIONS.md).

## Layout

```text
src/miniraw/   io_raw, noise, vst, layers, entropy, container, codec, cli
baselines/     b1_native (DNG strip bytes), b2_jxl (JPEG XL lossless), b5_sqrt (uncalibrated sqrt curve)
bench/         datasets, synth, metrics, run, report, splits/
tests/         M0-M3 acceptance tests
results/       generated; never edited by hand (except DEVIATIONS.md)
```
