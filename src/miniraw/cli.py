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
import os
import sys
import time
from pathlib import Path

import numpy as np

from . import __version__, codec, container, export, io_raw


def _step(v):
    step = float(v)
    if not step > 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0 (in units of the sensor noise), not {v}")
    return step


FORCE = "replace an existing output file"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="miniraw", description="MiniRaw: compact layered raw photos. "
                                 "Run `miniraw COMMAND --help` for a command's options.")
    ap.add_argument("--version", action="version", version=f"miniraw {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    e = sub.add_parser("encode", help="compress one raw photo (ARW, NEF, CR2, DNG, ...) into a .mraw file")
    e.add_argument("src", help="raw photo")
    e.add_argument("dst", help="output .mraw file")
    e.add_argument("--step", type=_step, default=1.0, help="error bound in units of the sensor noise: every pixel of the working copy stays within step/2 of it (default 1.0; 1.5 or 2.0 give smaller files)")
    e.add_argument("--no-l3", action="store_true",
                   help="leave out the exact layer: about 60%% smaller, near-lossless only (no bit-exact restore)")
    e.add_argument("--force", action="store_true", help=FORCE)
    e.add_argument("--effort", type=int, default=4, help="JPEG XL effort 1-9 (default 4; 7 is ~2x slower, ~1.7%% smaller hot tier)")
    e.add_argument("--threads", type=int, help="worker threads (default: up to 8 cores)")
    e.add_argument("--format-version", type=int, choices=[1, 2], default=2,
                   help="2 (default): with search thumbnail; 1: a file MiniRaw v1.0 readers can decode")
    e.add_argument("--embed", nargs="?", const="ViT-B-32/laion2b_s34b_b79k", metavar="MODEL",
                   help="store an int8 OpenCLIP embedding of the IDX thumbnail (needs the [index] extra)")
    e.add_argument("--no-original", action="store_true",
                   help="do not keep the original raw file's container (~0.4 MB hot) and previews "
                        "(~1-4 MB cold per 33 MP ARW): no restore to the original format")
    d = sub.add_parser("decode", help="turn a .mraw back into the original raw, a DNG, or a JPEG/PNG/TIFF image")
    d.add_argument("src", help=".mraw file")
    d.add_argument("dst", help="output; the type follows the extension. Using the source file's own "
                               "extension (e.g. .ARW) restores the original raw file byte for byte")
    d.add_argument("--mode", choices=["index", "preview", "signal", "exact"],
                   help="layers to decode (default: exact for .dng/.npy when L3 is present, signal for images)")
    d.add_argument("--region", help="x,y,w,h in sensor pixels (.npy, .dng and images)")
    d.add_argument("--dither", action="store_true", help="signal mode: random value within each bin")
    d.add_argument("--sidecar", help=".mraw.l3 file for exact decoding of a split file")
    d.add_argument("--threads", type=int, help="worker threads (default: up to 8 cores)")
    d.add_argument("--exposure", type=float, default=0.0, help="images: exposure shift in EV")
    d.add_argument("--quality", type=int, default=92, help="JPEG quality")
    d.add_argument("--bits", type=int, choices=[8, 16], default=8, help="PNG/TIFF bit depth")
    d.add_argument("--half-size", action="store_true", help="images: develop at half resolution (fast)")
    d.add_argument("--no-auto-bright", action="store_true", help="images: disable LibRaw auto brightness")
    d.add_argument("--uncompressed", action="store_true", help="DNG: write uncompressed instead of lossless JPEG")
    d.add_argument("--force", action="store_true", help=FORCE)
    for name, what in (("info", "show what a .mraw file holds: sizes, what it restores, noise model"),
                       ("strip", "delete the exact layer from a .mraw in place, keeping the near-lossless working copy"),
                       ("split", "move the exact layer into FILE.mraw.l3, e.g. for cheaper storage")):
        sub.add_parser(name, help=what).add_argument("src", help=".mraw file")
    sub.choices["split"].add_argument("--force", action="store_true", help="replace an existing .mraw.l3")
    b = sub.add_parser("bundle", help="compress a folder into one zip: raws -> .mraw, JPEGs repacked losslessly")
    b.add_argument("src", help="folder to bundle (subfolders included)")
    b.add_argument("dst", help="output .zip")
    b.add_argument("--cold", choices=["keep", "split", "drop"], default=None,
                   help="bit-exact layer L3: keep in the zip (default), split into OUT.l3.zip, or drop "
                        "(near-lossless raws, ~55%% smaller)")
    b.add_argument("--no-l3", action="store_true", help="same as --cold drop (matches `encode --no-l3`)")
    b.add_argument("--step", type=_step, default=1.0, help="error bound in units of the sensor noise: every pixel of the working copy stays within step/2 of it (default 1.0; 1.5 or 2.0 give smaller files)")
    b.add_argument("--effort", type=int, default=4, help="JPEG XL effort 1-9 for raws (default 4)")
    b.add_argument("--jpeg", choices=["repack", "store"], default="repack",
                   help="repack JPEGs losslessly with JPEG XL (byte-exact restore) or store them as-is")
    b.add_argument("--png", choices=["store", "repack"], default="store",
                   help="repack metadata-free PNGs as lossless JPEG XL (~40%% smaller; restored with identical "
                        "pixels, but not the identical PNG file bytes)")
    b.add_argument("--threads", type=int, help="worker threads (default: up to 8 cores)")
    b.add_argument("--force", action="store_true", help="replace an existing output .zip")
    u = sub.add_parser("unbundle", help="restore a bundle into a folder")
    u.add_argument("src", help="bundle .zip")
    u.add_argument("dst", nargs="?", help="output folder")
    u.add_argument("--list", action="store_true", help="list the bundle's contents and exit")
    u.add_argument("--raw-as", choices=["original", "dng", "jpg", "png", "tif", "mraw"], default="original",
                   help="restore raws as the original files byte for byte (default; falls back to DNG when "
                        "the original can't be restored), DNG, developed images, or .mraw")
    u.add_argument("--l3", help="the split-off .l3.zip (default: looked up next to the bundle)")
    u.add_argument("--threads", type=int, help="worker threads (default: up to 8 cores)")
    u.add_argument("--force", action="store_true", help="overwrite files that already exist in the output folder")
    a = ap.parse_args(argv)
    try:
        _run(a)
    except Exception as err:  # noqa: BLE001 - one-line messages for the CLI; MINIRAW_DEBUG=1 shows the traceback
        if os.environ.get("MINIRAW_DEBUG"):
            raise
        expected = isinstance(err, (ValueError, OSError, io_raw.NotBayer)) or type(err).__module__.startswith("rawpy")
        msg = str(err) if expected else (f"unexpected {type(err).__name__}: {err} "
                                         "(please report it; MINIRAW_DEBUG=1 shows the traceback)")
        if isinstance(err, OSError) and err.filename and err.strerror:   # name the path the OS complained about
            msg = f"{str(err.filename).removesuffix('.part')}: {err.strerror}"
        elif a.cmd in ("decode", "info", "strip", "split") and a.src not in msg:
            msg = f"{a.src}: {msg}"
        sys.exit(f"miniraw {a.cmd}: {msg}")


def _check_input(path):
    if not Path(path).exists():
        raise FileNotFoundError(f"{path}: no such file")


def _check_output(path, force):
    if Path(path).exists() and not force:
        raise FileExistsError(f"{path} already exists; pass --force to replace it")


def _read_raw(path):
    _check_input(path)
    try:
        return io_raw.read(path)
    except io_raw.NotBayer as err:
        raise ValueError(f"{path}: not a 2x2 Bayer raw ({err}); MiniRaw supports Bayer sensors only") from err
    except Exception as err:
        if type(err).__module__.startswith("rawpy"):
            raise ValueError(f"{path}: not a raw file LibRaw can read") from err
        raise


def _run(a):
    if a.cmd == "encode":
        t0 = time.perf_counter()
        _check_output(a.dst, a.force)
        raw = _read_raw(a.src)
        data, _ = codec.encode(raw, step=a.step, with_l3=not a.no_l3, effort=a.effort,
                               threads=a.threads, source={"file": Path(a.src).name},
                               keep_original=not a.no_original, format_version=a.format_version,
                               embed=a.embed)
        Path(a.dst).write_bytes(data)
        print(f"{a.src} ({_mb(Path(a.src).stat().st_size)}) -> {a.dst} ({_mb(len(data))}) "
              f"in {time.perf_counter() - t0:.1f} s")
        print("\n".join(_summary(data)))
        if len(data) > Path(a.src).stat().st_size:
            print("note: larger than the original. This raw was probably already compressed lossily in the camera, "
                  "so there is little noise left to set aside; `bundle` stores such files as they are.")
    elif a.cmd == "decode":
        _decode(a)
    elif a.cmd == "bundle":
        from . import bundle
        if a.no_l3 and a.cold not in (None, "drop"):
            sys.exit(f"--no-l3 conflicts with --cold {a.cold}")
        cold = "drop" if a.no_l3 else (a.cold or "keep")
        bundle.bundle(a.src, a.dst, a.step, a.effort, cold, a.jpeg, a.threads, png=a.png, force=a.force)
    elif a.cmd == "unbundle":
        from . import bundle
        _check_input(a.src)
        if a.list:
            return bundle.list_bundle(a.src)
        if not a.dst:
            sys.exit("unbundle needs an output folder (or --list)")
        bundle.unbundle(a.src, a.dst, a.raw_as, a.l3, a.threads, force=a.force)
    elif a.cmd == "info":
        _check_input(a.src)
        _print_info(Path(a.src).read_bytes(), a.src)
    elif a.cmd in ("strip", "split"):
        _check_input(a.src)
        if a.cmd == "split":
            _check_output(a.src + ".l3", a.force)
        data = Path(a.src).read_bytes()
        if not container.RangeReader(data).flags & container.FLAG_L3:
            sys.exit(f"miniraw {a.cmd}: {a.src} has no exact layer (already stripped or split)")
        hot, l3 = container.strip(data)
        if a.cmd == "split":
            Path(a.src + ".l3").write_bytes(l3)
        Path(a.src).write_bytes(hot)
        print(f"{a.src}: " + (f"moved the exact layer ({_mb(len(l3))}) to {a.src}.l3" if a.cmd == "split" else
                              f"deleted the exact layer ({_mb(len(l3))})")
              + f"; the working copy is {_mb(len(hot))}")


def _decode(a):
    t0 = time.perf_counter()
    _check_input(a.src)
    _check_output(a.dst, a.force)
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
        if reader.has_original:
            Path(a.dst).write_bytes(dec.original())
            print(f"restored the original {orig['name']} byte for byte (SHA-256 verified): {a.dst} "
                  f"({orig['size'] / 1e6:.1f} MB) in {time.perf_counter() - t0:.2f} s")
            return
        if reader.has_container:
            blob, mosaic = dec.near_lossless_original(a.dither)
            _verify_raw(blob, mosaic)
            Path(a.dst).write_bytes(blob)
            print(f"wrote a near-lossless {orig.get('ext', ext)} (the exact original needs L3: "
                  f"{dec.original_unavailable_reason()}): {a.dst} ({len(blob) / 1e6:.1f} MB) in "
                  f"{time.perf_counter() - t0:.2f} s. Same format and metadata, pixels within the sensor "
                  f"noise of the original, verified readable by LibRaw.")
            return
        if src_ext != ".dng":
            sys.exit(f"cannot write {Path(a.dst).suffix}: {dec.original_unavailable_reason()}. "
                     "Use .dng for the sensor data in a standard raw file.")
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
        if ("IDX", 0, 0, 0) in reader.sections and ext in export.IMAGE_EXT:
            rgb = dec.preview()                       # the stored index thumbnail, as is
            export.save_image(a.dst, rgb, hd, a.quality)
            print(f"wrote {a.dst} (preview from IDX, {rgb.shape[1]}x{rgb.shape[0]}) in "
                  f"{time.perf_counter() - t0:.2f} s")
            return
        if not dec.layout.get("l1"):
            sys.exit("this file's preview is an image (IDX); write it to .jpg, .png or .tif")
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


def _verify_raw(blob, mosaic):
    """LibRaw must read back exactly the pixels we wrote."""
    import io
    import rawpy
    with rawpy.imread(io.BytesIO(blob)) as r:
        got = r.raw_image_visible[:mosaic.shape[0], :mosaic.shape[1]]
        if not np.array_equal(got, mosaic):
            sys.exit("internal error: the written raw file does not read back as the decoded pixels")


RAW_EXTENSIONS = {".dng", ".nef", ".nrw", ".cr2", ".cr3", ".arw", ".srf", ".sr2", ".raf", ".orf", ".rw2",
                  ".pef", ".srw", ".iiq", ".3fr", ".erf", ".kdc", ".mrw", ".x3f"}


def _mb(n):
    return f"{n / 1e6:.1f} MB" if n >= 1e5 else f"{n / 1e3:.1f} KB"


def _tiers(data):
    """Reader, header, layer sizes, and the hot (working copy) and cold (exact layer) byte counts."""
    r = container.RangeReader(data)
    hd = r.header
    sizes = container.layer_sizes(hd)
    has_l3 = bool(r.flags & container.FLAG_L3)
    present = sum(sizes.values()) - (0 if has_l3 else sizes["L3"] + sizes.get("SRC", 0))
    sizes["L0"] = len(data) - present
    cold = sizes["L3"] + sizes.get("SRC", 0) if has_l3 else 0
    return r, hd, sizes, len(data) - cold, cold


def _summary(data, path=None):
    """What a person needs: sizes of the working copy and the exact layer, and what each gives back."""
    r, hd, sizes, hot, cold = _tiers(data)
    step = hd["noise"]["step"]
    orig = hd["source"].get("original") or {}
    lines = [f"  working copy  {_mb(hot):>9}  near-lossless: every pixel within {step / 2:g}x the estimated sensor noise "
             f"(step {step:g})"]
    sidecar = Path(f"{path}.l3") if path else None
    if cold:
        what = (f"restores the original {orig['name']} ({_mb(orig['size'])}) byte for byte" if r.has_original else
                "restores every sensor value bit for bit (as DNG; the original file itself is not restorable"
                + (f": {orig['reason']})" if orig.get("reason") else ")"))
        lines.append(f"  exact layer   {_mb(cold):>9}  {what}")
    elif sidecar is not None and sidecar.exists():
        lines.append(f"  exact layer   in {sidecar.name} ({_mb(sidecar.stat().st_size)}); decode with --sidecar")
    else:
        lines.append("  exact layer   not included: this file decodes to the near-lossless working copy only")
    if sizes.get("IDX"):
        idx = hd.get("idx", {})
        lines.append(f"  thumbnail     {_mb(sizes['IDX']):>9}  {idx.get('width')}x{idx.get('height')} "
                     "(decode --mode preview)")
    return lines


def _print_info(data, path=None):
    r, hd, sizes, hot, cold = _tiers(data)
    s = hd["sensor"]
    npx = s["width"] * s["height"]
    exif = hd["source"].get("exif") or {}
    camera = " ".join(v for v in (exif.get("Make"), exif.get("Model")) if v)
    print(f"{path or 'file'}: {_mb(len(data))}, {s['width']}x{s['height']} {s['bit_depth']}-bit"
          + (f" ({camera})" if camera else "") + f", MiniRaw format {r.version}")
    print("\n".join(_summary(data, path)))
    print("\nDetails")
    print("  layers: " + ", ".join(f"{k} {_mb(sizes[k])} ({8 * sizes[k] / npx:.2f} bits/pixel)"
                                   for k in ("L0", "L1", "L2", "L3") if sizes[k] or k == "L2")
          + ("" if r.flags & container.FLAG_L3 else " (L3 stripped)"))
    for e in hd.get("embed") or []:
        print(f"  search embedding: {e['model']} ({e['dim']} x {e['dtype']})")
    orig = hd["source"].get("original")
    if orig:
        parts = [f"{k} {_mb(sizes[k])} ({what})" for k, what in (("CTR", "container, in the working copy"),
                                                                 ("SRC", "previews, in the exact layer"))
                 if sizes.get(k)]
        print(f"  original file {orig['name']}: " + (", ".join(parts) if parts else
                                                      f"not kept ({orig.get('reason', 'no container')})"))
    planes = []
    for i, m in enumerate(hd["noise"]["planes"]):
        planes.append(f"{io_raw.PLANE_NAMES[i]} " + (f"a={m['a']:.3f} c={m['c']:.2f}" if m["type"] == "affine"
                                                      else m["type"]))
    print("  noise model (variance = a x signal + c, in DN): " + ", ".join(planes))
    st = hd.get("stats")
    if st:
        f = lambda v, fmt: "n/a" if v is None else format(v, fmt)  # noqa: E731
        print(f"  SNR at 18% grey {f(st['snr18'], '.1f')}, clipped {f(st['clipped_frac'], '.3%')}, "
              f"mean level {f(st['mean_level'], '.3f')}")
    print(f"  tile {hd['layout']['tile']} px, base layer k {hd['layout']['k']}")


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
