"""Bundle a folder into one zip, compressing photos first; unbundle it again.

Raw (2x2 Bayer) files become .mraw members; JPEGs are repacked losslessly with
JPEG XL (restorable byte for byte); with png="repack", PNGs without colour or
text metadata are stored as lossless JPEG XL and restored with identical pixels
(the PNG file bytes differ). Everything else is stored as-is. A
manifest.json inside the zip records each file's original path, size, SHA-256
and modification time, so unbundling can verify and restore every file.

Raw files come back byte for byte when MiniRaw could keep their container
(lossless-JPEG-tiled raws such as Sony ARW lossless; see source.py), and
otherwise as DNG with bit-exact sensor data.
"""
import hashlib
import json
import os
import time
import zipfile
from pathlib import Path, PurePosixPath

from . import __version__, codec, container, export, io_raw

MANIFEST = "manifest.json"
FORMAT = "miniraw-bundle/1"
RAW_EXT = {".dng", ".nef", ".nrw", ".cr2", ".cr3", ".arw", ".srf", ".sr2", ".raf", ".orf", ".rw2",
           ".pef", ".srw", ".iiq", ".3fr", ".erf", ".kdc", ".mrw", ".x3f"}
JPEG_EXT = {".jpg", ".jpeg"}
# PNG chunks that carry only pixels; any other chunk (iCCP, gAMA, tEXt, ...) would be lost by a repack
PNG_PLAIN_CHUNKS = {b"IHDR", b"IDAT", b"IEND"}
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
# Already-compressed formats: storing beats deflating them again.
STORE_EXT = {".mraw", ".jxl", ".jpg", ".jpeg", ".png", ".heic", ".heif", ".avif", ".webp", ".gif", ".mp4",
             ".mov", ".m4v", ".mkv", ".avi", ".mp3", ".m4a", ".aac", ".zip", ".gz", ".7z", ".xz"} | RAW_EXT


def _sha256(b):
    return hashlib.sha256(b).hexdigest()


def _zipinfo(name, mtime, ext):
    zi = zipfile.ZipInfo(name, time.localtime(max(mtime, 315532800))[:6])
    zi.compress_type = zipfile.ZIP_STORED if ext in STORE_EXT else zipfile.ZIP_DEFLATED
    zi.external_attr = 0o644 << 16
    return zi


def _walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not os.path.islink(os.path.join(dirpath, d)))
        for f in sorted(filenames):
            p = Path(dirpath) / f
            if f in SKIP_NAMES or p.is_symlink() or not p.is_file():
                continue
            yield p


def _repack_jpeg(data):
    """Lossless JPEG XL repack, verified to restore the identical bytes; None if not possible."""
    import imagecodecs
    try:
        jxl = bytes(imagecodecs.jpegxl_encode_jpeg(data))
        if bytes(imagecodecs.jpegxl_decode_jpeg(jxl)) == data and len(jxl) < len(data):
            return jxl
    except Exception:
        pass
    return None


def _png_chunks(data):
    i, out = 8, set()
    while i + 8 <= len(data):
        n = int.from_bytes(data[i:i + 4], "big")
        out.add(data[i + 4:i + 8])
        i += 12 + n
    return out


def _pixel_sha(arr):
    return hashlib.sha256(f"{arr.shape}{arr.dtype.str}".encode() + arr.tobytes()).hexdigest()


def _repack_png(data):
    """Lossless JPEG XL of a metadata-free PNG, verified pixel-identical; (jxl, pixel hash) or None."""
    import imagecodecs
    if data[:8] != b"\x89PNG\r\n\x1a\n" or not _png_chunks(data) <= PNG_PLAIN_CHUNKS:
        return None
    try:
        px = imagecodecs.png_decode(data)
        jxl = bytes(imagecodecs.jpegxl_encode(px, lossless=True, effort=7))
        back = imagecodecs.jpegxl_decode(jxl)
        if back.dtype == px.dtype and back.shape == px.shape and (back == px).all() and len(jxl) < len(data):
            return jxl, _pixel_sha(px)
    except Exception:
        pass
    return None


def bundle(src, dst, step=1.0, effort=4, cold="keep", jpeg="repack", threads=None, log=print, png="store",
           force=False):
    """Bundle folder `src` into zip `dst`. cold: keep | split (L3 to <dst>.l3.zip) | drop.

    Refuses to replace an existing `dst` (or its .l3.zip) unless force.
    """
    src, dst = Path(src), Path(dst)
    if not src.is_dir():
        raise NotADirectoryError(f"{src}: not a folder")
    files = [p for p in _walk(src) if p.resolve() != dst.resolve()]
    l3_path = dst.with_name(dst.name[:-4] + ".l3.zip" if dst.name.endswith(".zip") else dst.name + ".l3.zip")
    for out in (dst, l3_path) if cold == "split" else (dst,):
        if out.exists() and not force:
            raise FileExistsError(f"{out} already exists; pass --force to replace it")
    tmp, tmp_l3 = dst.with_name(dst.name + ".part"), l3_path.with_name(l3_path.name + ".part")
    entries, t0 = [], time.perf_counter()
    total_in = total_out = total_l3 = 0
    with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf, \
            (zipfile.ZipFile(tmp_l3, "w", allowZip64=True) if cold == "split" else _Null()) as zl3:
        for i, p in enumerate(files, 1):
            rel = PurePosixPath(p.relative_to(src).as_posix())
            data = p.read_bytes()
            st = p.stat()
            ext = p.suffix.lower()
            e = {"path": str(rel), "size": len(data), "sha256": _sha256(data), "mtime": st.st_mtime}
            payload, note = None, ""
            if ext in RAW_EXT:
                try:
                    raw = io_raw.read(p)
                    mr, _ = codec.encode(raw, step=step, effort=effort, threads=threads,
                                         with_l3=cold != "drop", source={"file": p.name}, keep_original=True)
                    hdr = container.RangeReader(mr).header
                    restorable = bool((hdr["source"].get("original") or {}).get("restorable"))
                    e.update(kind="raw", member=f"{rel}.mraw", mosaic_sha256=hdr["source"]["sha256"],
                             step=step, exact=cold != "drop", original_restorable=restorable)
                    if cold == "split":
                        mr, l3 = container.strip(mr)
                        zl3.writestr(_zipinfo(f"{rel}.mraw.l3", st.st_mtime, ".mraw"), l3)
                        e["l3_member"] = f"{rel}.mraw.l3"
                        total_l3 += len(l3)
                    payload = mr
                    container_kept = bool((hdr["source"].get("original") or {}).get("container"))
                    e["container"] = container_kept
                    note = ("raw, original restorable byte for byte" if restorable else "raw, bit-exact sensor data") \
                        if cold == "keep" else ("raw, L3 split off" if cold == "split" else
                                                "raw, near-lossless" + (" (original format kept)" if container_kept else ""))
                except io_raw.NotBayer as err:
                    note = f"raw stored as-is (not 2x2 Bayer: {err})"
                except Exception as err:  # unreadable or unsupported raw: keep the original
                    note = f"raw stored as-is ({type(err).__name__}: {err})"
            elif ext in JPEG_EXT and jpeg == "repack":
                jxl = _repack_jpeg(data)
                if jxl is not None:
                    e.update(kind="jpeg-jxl", member=f"{rel}.jxl")
                    payload, note = jxl, "JPEG, lossless repack"
                else:
                    note = "JPEG stored as-is (repack not smaller or not supported)"
            elif ext == ".png" and png == "repack":
                r = _repack_png(data)
                if r is not None:
                    e.update(kind="png-jxl", member=f"{rel}.jxl", pixel_sha256=r[1])
                    payload, note = r[0], "PNG, lossless repack (pixel-identical)"
                else:
                    note = "PNG stored as-is (has colour/text metadata, or repack not smaller)"
            if payload is None:
                e.update(kind="file", member=str(rel))
                payload = data
            zf.writestr(_zipinfo(e["member"], st.st_mtime, PurePosixPath(e["member"]).suffix.lower()), payload)
            e["stored_size"] = len(payload)
            entries.append(e)
            total_in += len(data)
            total_out += len(payload)
            log(f"[{i}/{len(files)}] {rel}  {len(data) / 1e6:.1f} MB -> {len(payload) / 1e6:.1f} MB"
                + (f"  ({note})" if note else ""))
        manifest = {"format": FORMAT, "miniraw": __version__, "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "settings": {"step": step, "effort": effort, "cold": cold, "jpeg": jpeg, "png": png},
                    "files": entries}
        zf.writestr(_zipinfo(MANIFEST, time.time(), ".json"), json.dumps(manifest, indent=1))
    tmp.replace(dst)
    if cold == "split":
        tmp_l3.replace(l3_path)
    dt = time.perf_counter() - t0
    size = dst.stat().st_size
    change = "" if not total_in else (f" ({(1 - size / total_in) * 100:.0f}% smaller)" if size <= total_in else
                                      f" ({(size / total_in - 1) * 100:.0f}% larger: zip overhead on small files)")
    log(f"\n{len(files)} files, {total_in / 1e6:.1f} MB -> {size / 1e6:.1f} MB{change} in {dt:.1f} s: {dst}")
    if cold == "split":
        log(f"cold layer (needed for bit-exact raws): {l3_path.stat().st_size / 1e6:.1f} MB -> {l3_path}")
    return manifest


class _Null:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def read_manifest(zf):
    m = json.loads(zf.read(MANIFEST))
    if m.get("format") != FORMAT:
        raise ValueError(f"not a MiniRaw bundle (format {m.get('format')!r})")
    return m


def _safe_target(root, rel):
    target = (root / rel).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise ValueError(f"unsafe path in bundle: {rel}")
    return target


def _expected_path(e, raw_as):
    """Where unbundle writes entry e (before sibling de-duplication)."""
    rel = PurePosixPath(e["path"])
    if e["kind"] != "raw" or (raw_as == "original" and e.get("container")):
        return rel
    if raw_as == "mraw":
        return rel.with_name(rel.name + ".mraw")
    return rel.with_suffix("." + ("dng" if raw_as == "original" else raw_as))


def unbundle(src, dst, raw_as="original", l3=None, threads=None, log=print, force=False, **develop):
    """Extract bundle `src` into folder `dst`. raw_as: original | dng | jpg | png | tif | mraw.

    "original" restores raw files byte for byte where the bundle kept their container, and
    falls back to DNG (bit-exact sensor data, or near-lossless without L3) otherwise.

    l3: the split-off <bundle>.l3.zip (looked up next to the bundle when not given).
    Refuses to write over existing files unless force; nothing is written if any would be replaced.
    """
    src, dst = Path(src), Path(dst)
    root = dst.resolve()
    t0 = time.perf_counter()
    if l3 is None:
        cand = src.with_name(src.name[:-4] + ".l3.zip" if src.name.endswith(".zip") else src.name + ".l3.zip")
        l3 = cand if cand.exists() else None
    stats = {"files": 0, "verified": 0, "raw_exact": 0, "raw_signal": 0, "raw_original": 0, "raw_fallback": 0,
             "raw_near_lossless": 0, "identical": 0, "pixel_identical": 0, "developed": 0, "mraw": 0}
    with zipfile.ZipFile(src) as zf, (zipfile.ZipFile(l3) if l3 else _Null()) as zl3:
        m = read_manifest(zf)
        files = m["files"]
        if not force:
            clash = [str(t) for t in (_expected_path(e, raw_as) for e in files) if (root / t).exists()]
            if clash:
                raise FileExistsError(f"{len(clash)} file(s) would be replaced in {dst}, e.g. {clash[0]}; "
                                      "unbundle into an empty folder, or pass --force to overwrite")
        for i, e in enumerate(files, 1):
            rel = PurePosixPath(e["path"])
            out = _safe_target(dst, rel)
            out.parent.mkdir(parents=True, exist_ok=True)
            payload = zf.read(e["member"])
            if e["kind"] == "raw":
                sidecar = None
                if e.get("l3_member") and zl3 is not None:
                    sidecar = zl3.read(e["l3_member"])
                reader = container.RangeReader(payload, sidecar)
                if raw_as == "original" and reader.has_original:
                    blob = codec.Decoder(reader, threads).original()
                    if _sha256(blob) != e["sha256"]:
                        raise ValueError(f"{rel}: restored file does not match the original's SHA-256")
                    out.write_bytes(blob)
                    stats["verified"] += 1
                    stats["raw_original"] += 1
                    stats["identical"] += 1
                    note = "original, byte-identical"
                elif raw_as == "original" and reader.has_container:
                    blob, _ = codec.Decoder(reader, threads).near_lossless_original()
                    out.write_bytes(blob)
                    stats["raw_near_lossless"] += 1
                    note = f"{rel.suffix.lstrip('.').upper()}, near-lossless (no L3)"
                elif raw_as == "mraw":
                    out = out.with_name(out.name + ".mraw")
                    out.write_bytes(payload)
                    if sidecar:
                        out.with_name(out.name + ".l3").write_bytes(sidecar)
                    stats["mraw"] += 1
                    note = "mraw"
                else:
                    fmt = "dng" if raw_as == "original" else raw_as
                    stats["raw_fallback"] += raw_as == "original"
                    dec = codec.Decoder(reader, threads)
                    exact = reader.has_l3
                    mode = "exact" if exact and fmt == "dng" else "signal"
                    mosaic = dec.mosaic(mode)
                    out = out.with_suffix("." + fmt)
                    sibling = str(rel.with_suffix("." + fmt))
                    if out.exists() or any(f is not e and f["path"] == sibling for f in files):
                        out = out.with_name(rel.name + "." + fmt)   # avoid clobbering a sibling
                    if fmt == "dng":
                        if mode == "exact":
                            ok = _sha256(mosaic.astype("<u2").tobytes()) == e["mosaic_sha256"]
                            if not ok:
                                raise ValueError(f"{rel}: decoded sensor data does not match the bundle's hash")
                            stats["verified"] += 1
                        out.write_bytes(export.dng_bytes(mosaic, reader.header, compress=True))
                    else:
                        rgb = export.develop(mosaic, reader.header, **develop)
                        export.save_image(out, rgb, reader.header)
                        stats["developed"] += 1
                    stats["raw_exact" if mode == "exact" else "raw_signal"] += 1
                    note = ("DNG, bit-exact sensor data" if mode == "exact" else
                            "DNG, near-lossless (no L3)" if fmt == "dng" else f"developed {fmt.upper()}")
                    if raw_as == "original":
                        note += " (original not restorable: " + dec.original_unavailable_reason() + ")"
            elif e["kind"] == "png-jxl":
                import imagecodecs
                px = imagecodecs.jpegxl_decode(payload)
                if _pixel_sha(px) != e["pixel_sha256"]:
                    raise ValueError(f"{rel}: restored pixels do not match the original PNG")
                stats["verified"] += 1
                stats["pixel_identical"] += 1
                out.write_bytes(imagecodecs.png_encode(px))
                note = "PNG, pixel-identical"
            else:
                if e["kind"] == "jpeg-jxl":
                    import imagecodecs
                    payload = bytes(imagecodecs.jpegxl_decode_jpeg(payload))
                if _sha256(payload) != e["sha256"]:
                    raise ValueError(f"{rel}: restored bytes do not match the original's SHA-256")
                stats["verified"] += 1
                stats["identical"] += 1
                out.write_bytes(payload)
                note = "byte-identical"
            os.utime(out, (e["mtime"], e["mtime"]))
            stats["files"] += 1
            log(f"[{i}/{len(files)}] {out.relative_to(root)}  ({note})")
    lost = ("the bundle was made with --cold drop" if m["settings"].get("cold") == "drop" else
            "its .l3.zip was not found; pass --l3 FILE")
    exact_dng = stats["raw_exact"]                               # exact mode is used for DNG output only
    near_dng = stats["raw_signal"] - stats["developed"]
    lines = [(stats["identical"], "identical to the original (SHA-256 verified)"),
             (stats["pixel_identical"], "PNG with identical pixels (file bytes differ: repacked)"),
             (exact_dng, "raw recreated as DNG with identical sensor data (verified; the original file itself "
                         "was not restorable)" if raw_as == "original" else "DNG with identical sensor data (verified)"),
             (stats["raw_near_lossless"], f"raw in its original format with near-lossless pixels ({lost})"),
             (near_dng, f"raw as DNG with near-lossless pixels ({lost})"),
             (stats["developed"], f"raw developed to {raw_as.upper()}"),
             (stats["mraw"], "raw kept as .mraw")]
    log(f"\n{stats['files']} files restored to {dst} in {time.perf_counter() - t0:.1f} s:")
    for n, what in lines:
        if n:
            log(f"  {n} {what}")
    return stats


def list_bundle(src, log=print):
    with zipfile.ZipFile(src) as zf:
        m = read_manifest(zf)
        orig = sum(e["size"] for e in m["files"])
        log(f"{src}: {len(m['files'])} files, created {m['created']} by MiniRaw {m['miniraw']}, "
            f"settings {m['settings']}")
        for e in m["files"]:
            log(f"  {e['path']:50s} {e['size'] / 1e6:8.1f} MB -> {e['stored_size'] / 1e6:7.1f} MB  {e['kind']}")
        log(f"  total {orig / 1e6:.1f} MB -> {Path(src).stat().st_size / 1e6:.1f} MB")
