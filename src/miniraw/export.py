"""Export a decoded mosaic as DNG, or develop it to JPEG / PNG / TIFF.

The DNG holds the sensor data exactly as decoded (bit-identical to the source
raw in `exact` mode) plus the colour, white-balance, crop, orientation and
capture metadata MiniRaw stored at encode time. Images are developed from
that DNG with LibRaw (rawpy), so colours match LibRaw-based tools.
"""
import io
import struct
from fractions import Fraction
from pathlib import Path

import numpy as np

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
FLIP_TO_ORIENTATION = {0: 1, 3: 3, 5: 8, 6: 6}

# TIFF field types
BYTE, ASCII, SHORT, LONG, RATIONAL, SRATIONAL = 1, 2, 3, 4, 5, 10


def _rational(v, den=10000):
    if isinstance(v, (list, tuple)):
        return [int(v[0]), int(v[1])]
    f = Fraction(float(v)).limit_denominator(den)
    return [f.numerator, f.denominator]


def _cfa_layout(header):
    """2x2 CFAPattern (0=R, 1=G, 2=B) and per-position black levels, raster order."""
    s = header["sensor"]
    pattern, black = [0] * 4, [0] * 4
    for p, (dy, dx) in enumerate(s["cfa"]):
        pattern[dy * 2 + dx] = (0, 1, 1, 2)[p]
        black[dy * 2 + dx] = int(s["black"][p])
    return pattern, black


DNG_TILE = 256


def _ljpeg_tiles(mosaic, bits):
    """Lossless-JPEG (DNG compression 7) tiles, zero-padded at the edges."""
    import imagecodecs

    H, W = mosaic.shape
    for y in range(0, H, DNG_TILE):
        for x in range(0, W, DNG_TILE):
            t = np.zeros((DNG_TILE, DNG_TILE), np.uint16)
            blk = mosaic[y:y + DNG_TILE, x:x + DNG_TILE]
            t[:blk.shape[0], :blk.shape[1]] = blk
            yield bytes(imagecodecs.ljpeg_encode(t, bitspersample=bits))


def dng_bytes(mosaic, header, compress=False):
    """DNG 1.4 of a decoded mosaic; compress=True uses lossless JPEG tiles (as Adobe does)."""
    import tifffile

    s, src = header["sensor"], header.get("source", {})
    exif = src.get("exif", {}) or {}
    pattern, black = _cfa_layout(header)
    wb = s.get("wb") or [1, 1, 1, 1]
    g = (wb[1] + wb[2]) / 2 if wb[1] and wb[2] else 1.0
    neutral = [g / wb[0] if wb[0] else 1.0, 1.0, g / wb[3] if wb[3] else 1.0]
    make = exif.get("Make") or "Unknown"
    model = exif.get("Model") or "Unknown"
    tags = [
        (271, ASCII, 0, make, True),
        (272, ASCII, 0, model, True),
        (274, SHORT, 1, FLIP_TO_ORIENTATION.get(s.get("flip", 0), 1), True),
        (33421, SHORT, 2, (2, 2), True),
        (33422, BYTE, 4, bytes(pattern), True),
        (50706, BYTE, 4, bytes((1, 4, 0, 0)), True),
        (50707, BYTE, 4, bytes((1, 1, 0, 0)), True),
        (50708, ASCII, 0, f"{make} {model}", True),
        (50710, BYTE, 3, bytes((0, 1, 2)), True),
        (50711, SHORT, 1, 1, True),
        (50713, SHORT, 2, (2, 2), True),
        (50714, LONG, 4, tuple(black), True),
        (50717, LONG, 1, int(s["white"]), True),
        (50728, RATIONAL, 3, tuple(x for v in neutral for x in _rational(v, 1_000_000)), True),
    ]
    if s.get("cam_xyz"):
        vals = [x for row in s["cam_xyz"] for v in row for x in (int(round(v * 10000)), 10000)]
        tags += [(50721, SRATIONAL, 9, tuple(vals), True), (50778, SHORT, 1, 21, True)]  # D65
    if s.get("crop"):
        left, top, w, h = s["crop"]
        H, W = mosaic.shape
        w, h = min(w, W - left), min(h, H - top)
        tags += [(50719, LONG, 2, (left, top), True), (50720, LONG, 2, (w, h), True)]
    if exif.get("DateTime"):
        tags.append((306, ASCII, 0, exif["DateTime"], True))
    for code, key in ((33434, "ExposureTime"), (33437, "FNumber"), (37386, "FocalLength")):
        if key in exif:
            tags.append((code, RATIONAL, 1, tuple(_rational(exif[key])), True))
    if "ISO" in exif:
        tags.append((34855, SHORT, 1, int(exif["ISO"]), True))
    if exif.get("DateTimeOriginal"):
        tags.append((36867, ASCII, 0, exif["DateTimeOriginal"], True))
    if exif.get("LensModel"):
        tags.append((42036, ASCII, 0, exif["LensModel"], True))
    buf = io.BytesIO()
    mosaic = np.ascontiguousarray(mosaic, dtype=np.uint16)
    common = dict(photometric=32803, subfiletype=0, software="miniraw", extratags=tags, metadata=None)
    if compress:
        bits = max(int(s.get("bit_depth", 16)), int(mosaic.max()).bit_length(), 2)
        tifffile.imwrite(buf, _ljpeg_tiles(mosaic, bits), shape=mosaic.shape, dtype=np.uint16,
                         tile=(DNG_TILE, DNG_TILE), compression=7, **common)
    else:
        tifffile.imwrite(buf, mosaic, **common)
    return buf.getvalue()


def develop(mosaic, header, exposure=0.0, bits=8, half_size=False, auto_bright=True, orient=True):
    """Develop a mosaic to RGB with LibRaw via an in-memory DNG (orientation applied unless orient=False)."""
    import rawpy

    with rawpy.imread(io.BytesIO(dng_bytes(mosaic, header))) as r:
        return r.postprocess(use_camera_wb=True, output_bps=bits, half_size=half_size,
                             exp_shift=2.0 ** exposure, exp_preserve_highlights=0.0,
                             no_auto_bright=not auto_bright, **({} if orient else {"user_flip": 0}))


def resize(img, h, w):
    """Bilinear resize of an HxWxC uint8 image (area-averaged first when shrinking a lot)."""
    while img.shape[0] >= 2 * h and img.shape[1] >= 2 * w:
        hh, ww = img.shape[0] // 2 * 2, img.shape[1] // 2 * 2
        img = img[:hh, :ww].reshape(hh // 2, 2, ww // 2, 2, -1).mean(axis=(1, 3))
    img = img.astype(np.float32)
    ys = np.clip((np.arange(h) + 0.5) * img.shape[0] / h - 0.5, 0, img.shape[0] - 1)
    xs = np.clip((np.arange(w) + 0.5) * img.shape[1] / w - 0.5, 0, img.shape[1] - 1)
    y0, x0 = ys.astype(int), xs.astype(int)
    y1, x1 = np.minimum(y0 + 1, img.shape[0] - 1), np.minimum(x0 + 1, img.shape[1] - 1)
    fy, fx = (ys - y0)[:, None, None], (xs - x0)[None, :, None]
    top = img[y0][:, x0] * (1 - fx) + img[y0][:, x1] * fx
    bot = img[y1][:, x0] * (1 - fx) + img[y1][:, x1] * fx
    return np.ascontiguousarray(np.clip(top * (1 - fy) + bot * fy + 0.5, 0, 255).astype(np.uint8))


def preview_renderer(mosaic, header, qualities=(92, 88, 84, 80, 75, 70, 60)):
    """render(h, w, max_bytes) -> JPEG of the developed mosaic at h x w (sensor orientation),
    at the highest quality that fits max_bytes (the lowest listed one if none does)."""
    import imagecodecs
    cache = {}

    def render(h, w, max_bytes=None):
        half = mosaic.shape[0] >= 4 * h
        if half not in cache:
            cache[half] = develop(mosaic, header, half_size=half, orient=False)
        img = cache[half]
        crop = (header.get("sensor") or {}).get("crop")
        if crop and not half and (crop[3], crop[2]) == (h, w) and img.shape[0] >= crop[1] + h \
                and img.shape[1] >= crop[0] + w:
            img = np.ascontiguousarray(img[crop[1]:crop[1] + h, crop[0]:crop[0] + w])   # the default crop
        elif img.shape[:2] != (h, w):
            img = resize(img, h, w)
        for q in qualities:
            jpg = bytes(imagecodecs.jpeg8_encode(img, level=q))
            if max_bytes is None or len(jpg) <= max_bytes:
                break
        return jpg
    return render


def _exif_app1(header):
    """Minimal EXIF APP1 segment (IFD0 + Exif IFD) for developed JPEGs."""
    exif = (header.get("source", {}) or {}).get("exif", {}) or {}
    ifd0 = [(271, ASCII, exif.get("Make")), (272, ASCII, exif.get("Model")), (274, SHORT, 1),
            (305, ASCII, "miniraw"), (306, ASCII, exif.get("DateTime"))]
    sub = [(33434, RATIONAL, exif.get("ExposureTime")), (33437, RATIONAL, exif.get("FNumber")),
           (34855, SHORT, exif.get("ISO")), (36867, ASCII, exif.get("DateTimeOriginal")),
           (37386, RATIONAL, exif.get("FocalLength")), (42036, ASCII, exif.get("LensModel"))]
    ifd0 = [t for t in ifd0 if t[2] is not None]
    sub = [t for t in sub if t[2] is not None]

    def encode_ifd(entries, start, next_ifd_extra=None):
        n = len(entries) + (1 if next_ifd_extra else 0)
        data_off = start + 2 + 12 * n + 4
        head, data = [], b""
        items = list(entries) + ([next_ifd_extra] if next_ifd_extra else [])
        for code, typ, val in sorted(items, key=lambda t: t[0]):
            if typ == ASCII:
                raw = val.encode() + b"\0"
                count = len(raw)
            elif typ == SHORT:
                raw, count = struct.pack("<H", int(val)), 1
            elif typ == LONG:
                raw, count = struct.pack("<I", int(val)), 1
            else:
                num, den = _rational(val, 100000)
                raw, count = struct.pack("<II", num, den), 1
            if len(raw) <= 4:
                head.append(struct.pack("<HHI", code, typ, count) + raw.ljust(4, b"\0"))
            else:
                head.append(struct.pack("<HHII", code, typ, count, data_off + len(data)))
                data += raw + (b"\0" if len(raw) % 2 else b"")
        return struct.pack("<H", n) + b"".join(head) + struct.pack("<I", 0) + data

    # IFD0 at offset 8; its size depends only on entry count and payload, so lay out twice.
    probe = encode_ifd(ifd0, 8, (34665, LONG, 0) if sub else None)
    exif_off = 8 + len(probe)
    ifd0_b = encode_ifd(ifd0, 8, (34665, LONG, exif_off) if sub else None)
    tiff = b"II*\0" + struct.pack("<I", 8) + ifd0_b + (encode_ifd(sub, exif_off) if sub else b"")
    payload = b"Exif\0\0" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def save_image(path, rgb, header, quality=92):
    import imagecodecs
    import tifffile

    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        if rgb.dtype != np.uint8:
            rgb = (rgb >> 8).astype(np.uint8)
        jpg = bytes(imagecodecs.jpeg8_encode(rgb, level=quality))
        path.write_bytes(jpg[:2] + _exif_app1(header) + jpg[2:])
    elif ext == ".png":
        path.write_bytes(imagecodecs.png_encode(rgb))
    elif ext in (".tif", ".tiff"):
        tifffile.imwrite(path, rgb, photometric="rgb", compression="zlib")
    else:
        raise ValueError(f"unsupported image extension {ext}")
