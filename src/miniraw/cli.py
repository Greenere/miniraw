"""miniraw command line.

    miniraw encode IN.dng OUT.mraw [--step 1.0] [--no-l3]
    miniraw decode IN.mraw OUT.{dng,jpg,png,tif,npy,json} [--mode preview|signal|exact] [--region x,y,w,h]
                   [--exposure EV] [--quality Q] [--bits 8|16] [--half-size] [--uncompressed] [--sidecar F]
    miniraw info IN.mraw
    miniraw strip IN.mraw
    miniraw split IN.mraw
    miniraw bundle DIR OUT.zip [--cold keep|split|drop | --no-l3] [--step 1.0] [--effort 4] [--threads N]
                   [--jpeg repack|store]
                   [--png store|repack]
    miniraw unbundle IN.zip OUTDIR [--raw-as dng|jpg|png|tif|mraw] [--l3 IN.l3.zip] | --list
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from . import codec, container, export, io_raw


def main(argv=None):
    ap = argparse.ArgumentParser(prog="miniraw")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("encode")
    e.add_argument("src")
    e.add_argument("dst")
    e.add_argument("--step", type=float, default=1.0)
    e.add_argument("--no-l3", action="store_true")
    e.add_argument("--effort", type=int, default=4, help="JPEG XL effort 1-9 (default 4; 7 is ~2x slower, ~1.7%% smaller hot tier)")
    e.add_argument("--threads", type=int, help="worker threads (default: up to 8 cores)")
    e.add_argument("--no-original", action="store_true",
                   help="do not keep what is needed to restore the original raw file byte for byte "
                        "(saves ~3 MB of cold data per 33 MP ARW)")
    d = sub.add_parser("decode", help="decode to DNG (raw), JPEG/PNG/TIFF (developed), .npy (mosaic) or .json (header)")
    d.add_argument("src")
    d.add_argument("dst", help="output; the type follows the extension. Using the source file's own "
                               "extension (e.g. .ARW) restores the original raw file byte for byte")
    d.add_argument("--mode", choices=["index", "preview", "signal", "exact"],
                   help="layers to decode (default: exact for .dng/.npy when L3 is present, signal for images)")
    d.add_argument("--region", help="x,y,w,h in sensor pixels (.npy, .dng and images)")
    d.add_argument("--dither", action="store_true", help="signal mode: random value within each bin")
    d.add_argument("--sidecar", help=".mraw.l3 file for exact decoding of a split file")
    d.add_argument("--threads", type=int)
    d.add_argument("--exposure", type=float, default=0.0, help="images: exposure shift in EV")
    d.add_argument("--quality", type=int, default=92, help="JPEG quality")
    d.add_argument("--bits", type=int, choices=[8, 16], default=8, help="PNG/TIFF bit depth")
    d.add_argument("--half-size", action="store_true", help="images: develop at half resolution (fast)")
    d.add_argument("--no-auto-bright", action="store_true", help="images: disable LibRaw auto brightness")
    d.add_argument("--uncompressed", action="store_true", help="DNG: write uncompressed instead of lossless JPEG")
    for name in ("info", "strip", "split"):
        sub.add_parser(name).add_argument("src")
    b = sub.add_parser("bundle", help="compress a folder into one zip: raws -> .mraw, JPEGs repacked losslessly")
    b.add_argument("src", help="folder to bundle (subfolders included)")
    b.add_argument("dst", help="output .zip")
    b.add_argument("--cold", choices=["keep", "split", "drop"], default=None,
                   help="bit-exact layer L3: keep in the zip (default), split into OUT.l3.zip, or drop "
                        "(near-lossless raws, ~55%% smaller)")
    b.add_argument("--no-l3", action="store_true", help="same as --cold drop (matches `encode --no-l3`)")
    b.add_argument("--step", type=float, default=1.0)
    b.add_argument("--effort", type=int, default=4)
    b.add_argument("--jpeg", choices=["repack", "store"], default="repack",
                   help="repack JPEGs losslessly with JPEG XL (byte-exact restore) or store them as-is")
    b.add_argument("--png", choices=["store", "repack"], default="store",
                   help="repack metadata-free PNGs as lossless JPEG XL (~40%% smaller; restored with identical "
                        "pixels, but not the identical PNG file bytes)")
    b.add_argument("--threads", type=int)
    u = sub.add_parser("unbundle", help="restore a bundle into a folder")
    u.add_argument("src", help="bundle .zip")
    u.add_argument("dst", nargs="?", help="output folder")
    u.add_argument("--list", action="store_true", help="list the bundle's contents and exit")
    u.add_argument("--raw-as", choices=["original", "dng", "jpg", "png", "tif", "mraw"], default="original",
                   help="restore raws as the original files byte for byte (default; falls back to DNG when "
                        "the original can't be restored), DNG, developed images, or .mraw")
    u.add_argument("--l3", help="the split-off .l3.zip (default: looked up next to the bundle)")
    u.add_argument("--threads", type=int)
    a = ap.parse_args(argv)

    if a.cmd == "encode":
        raw = io_raw.read(a.src)
        data, _ = codec.encode(raw, step=a.step, with_l3=not a.no_l3, effort=a.effort,
                               threads=a.threads, source={"file": Path(a.src).name},
                               keep_original=not a.no_original)
        Path(a.dst).write_bytes(data)
        _print_info(data)
    elif a.cmd == "decode":
        _decode(a)
    elif a.cmd == "bundle":
        from . import bundle
        if a.no_l3 and a.cold not in (None, "drop"):
            sys.exit(f"--no-l3 conflicts with --cold {a.cold}")
        cold = "drop" if a.no_l3 else (a.cold or "keep")
        bundle.bundle(a.src, a.dst, a.step, a.effort, cold, a.jpeg, a.threads, png=a.png)
    elif a.cmd == "unbundle":
        from . import bundle
        if a.list:
            return bundle.list_bundle(a.src)
        if not a.dst:
            sys.exit("unbundle needs an output folder (or --list)")
        bundle.unbundle(a.src, a.dst, a.raw_as, a.l3, a.threads)
    elif a.cmd == "info":
        _print_info(Path(a.src).read_bytes())
    elif a.cmd in ("strip", "split"):
        data = Path(a.src).read_bytes()
        hot, l3 = container.strip(data)
        if a.cmd == "split":
            Path(a.src + ".l3").write_bytes(l3)
        Path(a.src).write_bytes(hot)
        print(f"hot {len(hot)} B, L3 {len(l3)} B" + (" -> " + a.src + ".l3" if a.cmd == "split" else " dropped"))


def _decode(a):
    t0 = time.perf_counter()
    ext = Path(a.dst).suffix.lower()
    region = tuple(int(v) for v in a.region.split(",")) if a.region else None
    reader = container.RangeReader(a.src, a.sidecar)
    dec = codec.Decoder(reader, a.threads)
    hd = reader.header
    mode = a.mode
    orig = hd["source"].get("original") or {}
    src_ext = (orig.get("ext") or Path(hd["source"].get("file", "")).suffix).lower()
    if ext == src_ext or (ext in RAW_EXTENSIONS and ext != ".dng"):
        if ext != src_ext:
            shown = orig.get("ext") or src_ext or "unknown"
            sys.exit(f"cannot convert to {Path(a.dst).suffix}: this file came from a {shown} file. Use "
                     f"{shown} to restore the original, or .dng for a standard raw file.")
        if src_ext != ".dng" or reader.has_original:
            if not reader.has_original:
                sys.exit("cannot restore the original raw file: " + dec.original_unavailable_reason()
                         + ". Use .dng for the sensor data in a standard raw file.")
            Path(a.dst).write_bytes(dec.original())
            print(f"restored the original {orig['name']} byte for byte (SHA-256 verified): {a.dst} "
                  f"({orig['size'] / 1e6:.1f} MB) in {time.perf_counter() - t0:.2f} s")
            return
    if ext == ".json" or mode == "index":
        Path(a.dst).write_text(json.dumps(_jsonable(hd), indent=1))
        return print(f"wrote header to {a.dst}")
    if mode is None:
        mode = "exact" if ext in (".dng", ".npy") and reader.has_l3 else "signal"
        if ext == ".dng" and not reader.has_l3:
            print("note: L3 not present (stripped); writing the signal-mode DNG, which is near-lossless, not "
                  "bit-exact. Pass --sidecar FILE.mraw.l3 for the exact original.", file=sys.stderr)
    if mode == "exact" and not reader.has_l3:
        sys.exit("exact mode needs L3: this file is stripped; pass --sidecar FILE.mraw.l3")

    if mode == "preview":
        if region:
            sys.exit("--region is not supported with preview")
        mosaic, hd = dec.preview_mosaic()
    else:
        mosaic = dec.mosaic(mode, region, a.dither)
        if region:
            hd = {**hd, "sensor": {**hd["sensor"], "crop": None}}

    if ext == ".npy":
        np.save(a.dst, mosaic)
    elif ext == ".dng":
        data = export.dng_bytes(mosaic, hd, compress=not a.uncompressed)
        Path(a.dst).write_bytes(data)
        if mode == "exact" and region is None:
            import hashlib
            ok = hashlib.sha256(mosaic.astype("<u2").tobytes()).hexdigest() == hd["source"]["sha256"]
            print(f"sensor data verified against the original's SHA-256: {'OK' if ok else 'MISMATCH'}")
    elif ext in export.IMAGE_EXT:
        bits = 8 if ext in (".jpg", ".jpeg") else a.bits
        rgb = export.develop(mosaic, hd, a.exposure, bits, a.half_size, not a.no_auto_bright)
        export.save_image(a.dst, rgb, hd, a.quality)
    else:
        sys.exit(f"unsupported output extension {ext!r}: use .dng, .jpg, .png, .tif, .npy or .json")
    size = Path(a.dst).stat().st_size
    print(f"wrote {a.dst} ({mode} mode, {mosaic.shape[1]}x{mosaic.shape[0]} mosaic, "
          f"{size / 1e6:.1f} MB) in {time.perf_counter() - t0:.2f} s")


RAW_EXTENSIONS = {".dng", ".nef", ".nrw", ".cr2", ".cr3", ".arw", ".srf", ".sr2", ".raf", ".orf", ".rw2",
                  ".pef", ".srw", ".iiq", ".3fr", ".erf", ".kdc", ".mrw", ".x3f"}


def _print_info(data):
    r = container.RangeReader(data)
    hd = r.header
    s = hd["sensor"]
    npx = s["width"] * s["height"]
    sizes = container.layer_sizes(hd)
    present = sum(sizes.values()) - (0 if r.flags & container.FLAG_L3 else sizes["L3"] + sizes["SRC"])
    sizes["L0"] = len(data) - present
    print(f"{s['width']}x{s['height']}  bit depth {s['bit_depth']}  step {hd['noise']['step']}  "
          f"k {hd['layout']['k']}  tile {hd['layout']['tile']}  L3 {'present' if r.flags & 1 else 'stripped'}")
    for k in ("L0", "L1", "L2", "L3"):
        print(f"  {k}: {sizes[k]:>10} B  {8 * sizes[k] / npx:6.3f} bpp")
    orig = hd["source"].get("original")
    if orig:
        state = ("restorable byte for byte" if orig.get("restorable") and r.has_l3 else
                 "restorable with the .mraw.l3 sidecar" if orig.get("restorable") else
                 f"not restorable ({orig.get('reason')})")
        print(f"  original: {orig['name']} ({orig['size'] / 1e6:.1f} MB), {state}")
    for k in ("SRC",):
        if sizes.get(k):
            print(f"  SRC: {sizes[k]:>9} B  (cold: original file container)")
    for i, m in enumerate(hd["noise"]["planes"]):
        print(f"  plane {io_raw.PLANE_NAMES[i]}: {m['type']}" +
              (f" a={m['a']:.3f} c={m['c']:.2f}" if m["type"] == "affine" else ""))


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items() if k != "tables"}
    if isinstance(o, list):
        return [_jsonable(v) for v in o]
    if isinstance(o, bytes):
        return f"<{len(o)} bytes>"
    return o


if __name__ == "__main__":
    main()
