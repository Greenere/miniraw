# MiniRaw Algorithm and Implementation Improvements

Source review: October 10, 2026. Scope: encode/decode efficiency, memory, and I/O. This guide proposes changes; it does not report newly measured speedups. References point to the reviewed `main` branch and may change.

## Objective

Improve throughput, peak memory, and selective access without changing reconstructed sensor values, the default signal reconstruction, or supported byte-exact original-file restoration.

The main pixel pipeline is already approximately linear in image size at fixed bit depth, tile size, and codec effort. The largest opportunities are unnecessary I/O, repeated work, memory traffic, and sorting that can be replaced with selection. A codec redesign is not required for these improvements.

Keep three compatibility goals distinct:

1. **Decoded-value compatibility:** identical exact and signal-mode samples.
2. **Format compatibility:** existing decoders can read newly produced files.
3. **Encoded-byte compatibility:** the `.mraw` bytes themselves remain identical.

Some optimizations preserve the first two but may change encoded bytes or layout. Declare the intended contract before implementing each change.

## Complexity baseline

Let `N` be sensor photosites, `B` eligible 8×8 noise-estimation blocks, `T` tile count, `K` touched tiles, `P` samples across those touched tiles, `C` compressed file bytes, `H` header bytes, and `W` concurrent workers. Hold bit depth, tile dimensions, and JPEG XL effort fixed.

| Stage | Current behavior | Target |
| --- | --- | --- |
| Noise statistics | Linear pixel/block work plus sorting within brightness bins, approximately `O(N + B log B)` | Linear block selection, with equivalent statistics |
| Quantization and residual construction | `O(N)` with wide temporary arrays | Same order; fewer allocations and passes |
| Tiled entropy coding | Approximately linear aggregate work for fixed tile settings | Profile constants; retain deliberate parallelism |
| Container assembly | Section sorting up to `O(T log T)`, checksums, and whole-output concatenation | Ordered assembly, bounded buffering, no redundant checksums |
| Full sensor decode | Approximately `O(C + N + T)` | Same order; less copying and optional streaming output |
| Fresh region decode | Loads `C`, processes directory, scans `T`, then decodes `P` | Read header plus required sections; calculate only `K` tiles |
| Peak memory | `O(N + C)` with multiple full-size representations | Remove avoidable copies; separate output memory from workspace |

Any exact general-purpose encoder must inspect the input, and full decode must emit the output: `Omega(N)` is the natural work lower bound. Fixed tile settings make library work scale with tile count, but this is not a proof about arbitrary libjxl configurations. The small iterative noise fit also has data-dependent convergence.

## P0: Measure the real production paths

Instrument the actual API and CLI, rather than relying only on `bench/experiments/profile_stages.py`. That script manually reconstructs a path with effort 7, different block reuse/validation behavior, and no original-container preservation. Current default effort is 4.

Measure these paths separately:

- Core sensor encode, with and without L3.
- CLI encode with original-file preservation enabled and disabled.
- Signal and exact sensor decode.
- Original ARW restoration, separately from sensor decode.
- Preview and small-region decode, including initialization and file I/O.

Record wall time, peak resident memory, bytes requested from the backing reader, output bytes, worker count, effort, and library versions. Distinguish first-use startup from warmed execution; `selection_bias()` performs cached Monte Carlo work on first use. Distinguish cold-file and cached-file reads where feasible.

Use representative low/high-ISO, smooth/detailed, and clipped images. Establish baselines before changes. No speedup percentage should be claimed from source inspection alone.

## P1: Implement actual selective I/O

**Finding:** `RangeReader.__init__()` calls `Path(src).read_bytes()`. A supplied sidecar path is also loaded in full. The access log records subsequent in-memory slices and misses the initial whole-file read.

**Consequence:** the format supports random access, but the current file-backed reader does not deliver its physical I/O or memory benefits. A small crop, preview, or signal decode can still load the entire file, including cold layers.

**Change:** introduce a backing reader with `read_at(offset, length)` and a separate in-memory implementation. Use positional file reads or synchronize seek/read pairs; concurrent tile jobs must not race on a shared file position. Read sidecars lazily. Give file handles an explicit lifecycle.

For the existing format, initialization still reads and parses the complete section directory. Target per-call work is therefore header/directory initialization plus selected-section reads and tile decoding—not unconditional `O(K)` end to end. Reusing a reader amortizes directory parsing.

**Acceptance:** compare exact and signal outputs with the old reader; test concurrent region reads and split sidecars. Instrument the backing reader, not just section slicing. Demonstrate that crop/preview requests do not issue a full-file read. Header read-ahead may legitimately include adjacent bytes; account for it explicitly.

## P2: Calculate intersecting tiles directly

**Finding:** `layers.tiles_touching()` creates the complete grid and filters it.

For an exclusive plane rectangle `[y0:y1, x0:x1]` and tile side `s`, enumerate only:

```text
ty in range(y0 // s, ceil(y1 / s))
tx in range(x0 // s, ceil(x1 / s))
```

Clip/validate the rectangle first, preserve tile ordering and edge dimensions, handle empty rectangles, and retain the untiled special case.

**Benefit:** tile selection changes from `O(T)` time and grid allocation to `O(K)`. It does not remove the section-directory initialization cost.

**Acceptance:** cover odd sensor-coordinate crops, exact tile boundaries, edge tiles, empty/invalid inputs under the chosen API contract, and untiled files. Region output must match a crop of full decode.

## P3: Avoid unused original-preview work

**Finding:** with `keep_original=True`, `source.capture()` prepares container and preview records. When `with_l3=False`, `codec.encode()` discards the cold preview record after capture has already repacked it.

**Change:** allow capture to omit cold-preview repacking while still preserving the preview locations and container information required to regenerate a valid original-format file.

Also review duplicated original-file reads and hashing between `codec.encode()` and `source.capture()`. Reuse bytes or a shared reader where practical. Original preservation additionally decodes and re-encodes source sensor tiles to verify exact reproducibility; measure this separately and do not silently remove that verification.

**Acceptance:** no-L3 original-format output still opens and contains the expected near-lossless samples. The L3-enabled path still restores the original file hash. Confirm the no-L3 path does not invoke large-preview repacking.

## P4: Reduce wide arrays, allocations, and memory passes

At 33 million samples, one full uint16 representation occupies about 66 MB; one int64 representation occupies about 264 MB. Concurrent work can multiply temporary storage.

| Location | Current overhead | Suggested change |
| --- | --- | --- |
| `_plane_state()` | Full-resolution `q` inherits int64 from the forward table | Store indices compactly after proving their range |
| `l1_from_q()` | Padded full-plane int64 allocation | Block/strip reduction with wide accumulators and explicit edge handling |
| `entropy.jxl_encode()` | Min/max scans, widening, shifting, narrowing | Reuse buffers and fuse preparation where profiling justifies it |
| `entropy.jxl_decode()` | uint16 output widened to int64 before subsequent reconstruction | Use the narrowest proven-safe representation or fuse reconstruction |
| `Decoder.mosaic()` | Decode into planes, then allocate and interleave a mosaic | Write tile results directly into the destination mosaic |
| `mosaic_sha256()` | Rebuild mosaic, convert, materialize bytes | Hash correctly ordered row/strip buffers incrementally |
| Tile jobs | Repeated coordinate/weight setup and temporary expressions | Reuse geometry and scratch buffers where safe |

Do not globally replace int64 with int32. Bilinear weighted sums and L1 accumulations need separate overflow proofs, including extreme sample values and large downsampling factors. Preserve operation order and rounding where necessary for exact signal reconstruction. Incremental hashing must preserve the original little-endian raster byte order, not plane order.

Start with compact `q` storage and removal of avoidable full-image copies; pursue native fused kernels only after profiling. Limit in-flight tasks so scratch memory scales with active workers rather than all tiles.

**Acceptance:** sample equality on edge cases, hash equality, peak-memory measurement, and throughput measurement at multiple worker counts. Verify that lower per-worker memory translates into lower process peak memory.

## P5: Replace full sorting in noise estimation

**Finding:** `_binned()` uses `np.argsort(var[sel])[:k]` to select the lowest 20% of variances in each intensity bin. The selected values do not need a complete order before median calculations.

**Change:** use partition-based selection. Brightness bin count and block dimensions are fixed, so selection can remove the `B log B` sorting term from this stage. The current repeated scan across 32 bins is linear but may also warrant grouping if profiles show a material cost.

**Compatibility issue:** equal variance values at the selection boundary can select different associated block means. Specify tie behavior and compare fitted parameters, quantization tables, and signal outputs. Partitioning is not automatically byte-identical to the existing sort.

Sampling fewer blocks or reusing a camera noise model is a separate algorithmic change. It may be faster but can change calibration and quantization behavior; validate it independently rather than bundling it with a selection optimization.

**Acceptance:** compare selected statistics and resulting tables, explicitly include ties, and measure both estimation and total encode time. Preserve the estimator’s required validation; `diagnostics=False` currently does not bypass initial validation.

## P6: Stream container assembly and cache checksums

**Finding:** encoded sections are retained, sorted, and concatenated into one output byte string. The header-reservation loop can recompute every payload CRC while adjusting offsets.

**Change:** calculate each section CRC once. Generate canonical ordering directly where possible; current sort inputs may already benefit from partial ordering, so benchmark before prioritizing sorting itself. Add a file-writing API with bounded buffering or a temporary section spool.

The existing format puts compressed metadata containing absolute offsets ahead of payloads. Streaming must respect that constraint: finalize layout via a spool/two-pass process, or use a deliberately designed reservation scheme. Do not patch offsets without rebuilding and validating the header. Preserve the layer order needed for strip/split.

An API returning one `bytes` object necessarily retains `O(C)` output memory. Keep it as a convenience wrapper; use a writer API when bounded compressed-output buffering matters. RAW ingestion and full-array return APIs independently impose `O(N)` storage.

**Acceptance:** existing readers decode new output, CRC validation succeeds, strip/split works, and byte-exact original restoration survives. Measure peak memory as well as elapsed time.

## Parallelism and smaller cleanups

The existing structure parallelizes plane preparation and tile jobs while using one JPEG XL thread per tile. This is a reasonable starting point. More workers can increase memory pressure and saturate bandwidth; GIL behavior and native-library scaling must be measured, not assumed.

After major costs are addressed, consider lazy RNG creation when dithering is disabled, reuse of interpolation geometry, and skipping decoder-table construction for header-only mode. Treat these as secondary unless profiles demonstrate otherwise.

## Recommended implementation order

1. Establish production-path timings, memory, and backing-reader I/O measurements.
2. Implement real selective reads and direct tile selection.
3. Skip unused cold-preview repacking and remove duplicated source-file work where safe.
4. Reduce full-image copies and wide stored arrays.
5. Replace unnecessary noise-statistic sorting with compatible selection.
6. Add bounded container writing and checksum reuse.
7. Optimize measured native hotspots and worker scheduling.

Keep changes independently reviewable. For each, report the preserved compatibility contract, measured improvement, and any size or behavior tradeoff. Do not conflate decoding sensor data with restoring ARW or rendering JPEG: those endpoints include additional work.

## Source references

- [Core encode/decode](https://github.com/Greenere/miniraw/blob/main/src/miniraw/codec.py)
- [Container and reader](https://github.com/Greenere/miniraw/blob/main/src/miniraw/container.py)
- [Noise estimation](https://github.com/Greenere/miniraw/blob/main/src/miniraw/noise.py)
- [Layer construction and tile selection](https://github.com/Greenere/miniraw/blob/main/src/miniraw/layers.py)
- [Entropy wrappers](https://github.com/Greenere/miniraw/blob/main/src/miniraw/entropy.py)
- [Quantization tables](https://github.com/Greenere/miniraw/blob/main/src/miniraw/vst.py)
- [Original-file capture and restoration](https://github.com/Greenere/miniraw/blob/main/src/miniraw/source.py)
- [RAW ingestion and plane packing](https://github.com/Greenere/miniraw/blob/main/src/miniraw/io_raw.py)
- [CLI defaults and export paths](https://github.com/Greenere/miniraw/blob/main/src/miniraw/cli.py)
- [Existing profiling script](https://github.com/Greenere/miniraw/blob/main/bench/experiments/profile_stages.py)
