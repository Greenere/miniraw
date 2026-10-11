"""Keep enough of the original raw file to restore it byte for byte.

For TIFF-based raws whose sensor data is lossless-JPEG tiles (Sony ARW lossless,
lossless DNG, ...), MiniRaw stores the file *skeleton* (everything except the
compressed tiles: TIFF structure, maker notes, embedded previews), each tile's
JPEG header and trailing bytes, and the sensor area outside LibRaw's visible
region. Restoring re-encodes the tiles from the exact sensor data with the
original Huffman tables. Lossless JPEG is deterministic, so this reproduces the
original bytes; capture() checks that at encode time and declines otherwise.
"""
import hashlib
from concurrent.futures import ThreadPoolExecutor

import msgpack
import numpy as np
import zstandard

from . import entropy

FORMAT = "tiff-ljpeg-tiles/1"


class Unsupported(Exception):
    pass


# --- Lossless JPEG (ITU T.81 process 14) --------------------------------------------------

def _segments(stream):
    """Header segments up to and including SOS: {marker: payload}, and the scan start offset."""
    if stream[:2] != b"\xff\xd8":
        raise Unsupported("tile is not a JPEG stream")
    i, segs, dht = 2, {}, []
    while True:
        if stream[i] != 0xFF:
            raise Unsupported("malformed JPEG header")
        marker = stream[i + 1]
        n = int.from_bytes(stream[i + 2:i + 4], "big")
        payload = stream[i + 4:i + 2 + n]
        if marker == 0xC4:
            dht.append(payload)
        else:
            segs[marker] = payload
        i += 2 + n
        if marker == 0xDA:
            return segs, dht, i


def _huffman_tables(dht_payloads):
    """{table id: (code[sym], length[sym])} for DC/lossless tables (Annex C)."""
    tables = {}
    for p in dht_payloads:
        j = 0
        while j < len(p):
            tc_th = p[j]
            bits = list(p[j + 1:j + 17])
            nsym = sum(bits)
            vals = list(p[j + 17:j + 17 + nsym])
            j += 17 + nsym
            if tc_th >> 4:
                continue                       # AC tables are not used by lossless JPEG
            code = np.zeros(17, np.int64)
            length = np.zeros(17, np.int64)   # 0 = symbol not in table
            c, k = 0, 0
            for size in range(1, 17):
                for _ in range(bits[size - 1]):
                    code[vals[k]], length[vals[k]] = c, size
                    c += 1
                    k += 1
                c <<= 1
            tables[tc_th & 15] = (code, length)
    return tables


def parse_header(header):
    segs, dht, _ = _segments(header + b"\xff\xd9")
    if 0xC3 not in segs:
        raise Unsupported("not a lossless (SOF3) JPEG")
    if 0xDD in segs and int.from_bytes(segs[0xDD][:2], "big"):
        raise Unsupported("restart intervals are not supported")
    sof, sos = segs[0xC3], segs[0xDA]
    precision = sof[0]
    ncomp = sof[5]
    comp_ids = [sof[6 + 3 * i] for i in range(ncomp)]
    ns = sos[0]
    table_of = {sos[1 + 2 * i]: sos[2 + 2 * i] >> 4 for i in range(ns)}
    predictor, pt = sos[1 + 2 * ns], sos[3 + 2 * ns] & 15
    if ns != ncomp or not 1 <= predictor <= 7 or pt != 0:
        raise Unsupported(f"unsupported scan (components {ns}/{ncomp}, predictor {predictor}, Pt {pt})")
    tables = _huffman_tables(dht)
    return {"precision": precision, "tables": [tables[table_of[c]] for c in comp_ids], "ncomp": ncomp,
            "predictor": predictor}


_SSSS = np.zeros(32769, np.int64)
_SSSS[1:] = np.floor(np.log2(np.arange(1, 32769))).astype(np.int64) + 1   # bit length of |diff|


def encode_scan(samples, hdr, pad=1):
    """Entropy-coded scan (with byte stuffing) for samples of shape (h, w, ncomp).

    Prediction follows ITU T.81 H.1.2.1: the first row uses the left neighbour (Ra), the
    first column the one above (Rb), the first sample 2**(P-1), and the rest the selected
    predictor 1-7. pad is the bit value filling the last byte: the standard says 1, some
    encoders (e.g. lj92) use 0.
    """
    s = samples.astype(np.int64)
    pred = np.empty_like(s)
    ra, rb, rc = s[1:, :-1], s[:-1, 1:], s[:-1, :-1]
    pred[1:, 1:] = {1: lambda: ra, 2: lambda: rb, 3: lambda: rc, 4: lambda: ra + rb - rc,
                    5: lambda: ra + ((rb - rc) >> 1), 6: lambda: rb + ((ra - rc) >> 1),
                    7: lambda: (ra + rb) >> 1}[hdr["predictor"]]()
    pred[0, 1:] = s[0, :-1]
    pred[1:, 0] = s[:-1, 0]
    pred[0, 0] = 1 << (hdr["precision"] - 1)
    diff = ((s - pred + 32768) & 0xFFFF) - 32768
    ssss = _SSSS[np.abs(diff)]
    elen = np.where(ssss == 16, 0, ssss)
    extra = np.where(diff < 0, diff - 1, diff) & ((1 << elen) - 1)
    code = np.empty_like(ssss)
    clen = np.empty_like(ssss)
    for k, (tc, tl) in enumerate(hdr["tables"]):
        code[..., k] = tc[ssss[..., k]]
        clen[..., k] = tl[ssss[..., k]]
    if (clen == 0).any():
        raise Unsupported("a difference category is missing from the Huffman table")
    # One field per sample: Huffman code followed by the extra bits (at most 32 bits).
    lens = (clen + elen).ravel()
    vals = ((code << elen) | extra).ravel()
    start = np.cumsum(lens) - lens
    total = int(start[-1] + lens[-1])
    nbytes = -(-total // 8)
    # Place each field in a 40-bit window starting at its byte; fields never overlap, so the
    # bytes are the sums of the window bytes that land on them.
    window = vals << (40 - (start & 7) - lens)
    byte = start >> 3
    idx = np.concatenate([byte + k for k in range(5)])
    contrib = np.concatenate([(window >> (32 - 8 * k)) & 0xFF for k in range(5)])
    out = np.bincount(idx, weights=contrib, minlength=nbytes + 5)[:nbytes].astype(np.uint8)
    if total & 7 and pad:
        out[-1] |= (1 << (8 - (total & 7))) - 1     # pad the last byte with 1-bits
    ff = np.flatnonzero(out == 0xFF)
    return np.insert(out, ff + 1, 0).tobytes()      # byte stuffing


# --- Tile <-> sensor layout ---------------------------------------------------------------

def _layout(shape, th, tw):
    """How lossless-JPEG samples (h, w, c) map onto a th x tw tile of the CFA."""
    h, w, c = shape
    if c == 4 and h * 2 == th and w * 2 == tw:
        return "2x2"
    if c == 2 and h == th and w * 2 == tw:
        return "1x2"
    if c == 1 and h == th and w == tw:
        return "1x1"
    raise Unsupported(f"unknown tile layout {shape} for {th}x{tw}")


def to_samples(tile, layout):
    if layout == "2x2":
        return np.stack([tile[0::2, 0::2], tile[0::2, 1::2], tile[1::2, 0::2], tile[1::2, 1::2]], -1)
    if layout == "1x2":
        return np.stack([tile[:, 0::2], tile[:, 1::2]], -1)
    return tile[..., None]


def from_samples(d, layout):
    if layout == "2x2":
        t = np.empty((d.shape[0] * 2, d.shape[1] * 2), np.uint16)
        t[0::2, 0::2], t[0::2, 1::2], t[1::2, 0::2], t[1::2, 1::2] = (d[..., k] for k in range(4))
        return t
    if layout == "1x2":
        t = np.empty((d.shape[0], d.shape[1] * 2), np.uint16)
        t[:, 0::2], t[:, 1::2] = d[..., 0], d[..., 1]
        return t
    return d[..., 0]


# --- Capture and restore ------------------------------------------------------------------

def _raw_ifd(path):
    """The CFA image's tiles, plus the byte ranges of other single-segment images (previews)."""
    import tifffile
    with tifffile.TiffFile(path) as tf:
        ifds = [s for p in tf.pages for s in [p] + list(p.pages or [])]
        cands = [s for s in ifds if getattr(s, "photometric", None) is not None and int(s.photometric) == 32803]
        if not cands:
            raise Unsupported("no CFA image in a TIFF structure")
        ifd = max(cands, key=lambda s: s.shape[0] * s.shape[1])
        if int(ifd.compression) != 7 or not ifd.is_tiled:
            raise Unsupported(f"raw data is not lossless-JPEG tiles (compression {int(ifd.compression)})")
        previews = []
        for s in ifds:
            if s is not ifd and len(s.dataoffsets) == 1 and s.databytecounts[0] > 4096:
                previews.append((int(s.dataoffsets[0]), int(s.databytecounts[0])))
        return (list(ifd.dataoffsets), list(ifd.databytecounts), ifd.shape,
                (int(ifd.tilelength), int(ifd.tilewidth)), previews)


def _repack_jpeg(blob):
    """Lossless JPEG XL repack of an embedded JPEG, verified byte-exact; None if not possible."""
    import imagecodecs
    if blob[:2] != b"\xff\xd8":
        return None
    try:
        jxl = bytes(imagecodecs.jpegxl_encode_jpeg(blob))
        if len(jxl) < len(blob) and bytes(imagecodecs.jpegxl_decode_jpeg(jxl)) == blob:
            return jxl
    except Exception:
        pass
    return None


def capture(path, visible, threads=None):
    """Container data needed to restore `path` byte for byte, as section bytes.

    visible: the exact sensor mosaic MiniRaw encodes (LibRaw raw_image_visible, cropped to even
    size), placed at the top-left of LibRaw's raw_image. Raises Unsupported when the file
    cannot be reproduced exactly.
    """
    import imagecodecs
    data = open(path, "rb").read()
    offs, cnts, shape, (th, tw), preview_ranges = _raw_ifd(path)
    H, W = shape
    ntx = -(-W // tw)
    full = np.zeros((-(-H // th) * th, ntx * tw), np.uint16)
    headers, tiles = [], []
    layout = None
    for i, (o, c) in enumerate(zip(offs, cnts)):
        t = data[o:o + c]
        _, _, scan0 = _segments(t)
        eoi = t.rfind(b"\xff\xd9")
        if eoi < scan0:
            raise Unsupported("tile without an end marker")
        head = t[:scan0]
        if head not in headers:
            headers.append(head)
        d = imagecodecs.ljpeg_decode(t)
        d = d[..., None] if d.ndim == 2 else d
        layout = layout or _layout(d.shape, th, tw)
        ty, tx = divmod(i, ntx)
        full[ty * th:(ty + 1) * th, tx * tw:(tx + 1) * tw] = from_samples(d, layout)
        tiles.append({"off": o, "len": c, "head": headers.index(head), "tail": t[eoi:]})
    vh, vw = visible.shape
    if not np.array_equal(full[:vh, :vw], visible):
        raise Unsupported("decoded tiles do not match the sensor data LibRaw reports")
    hdrs = [parse_header(h) for h in headers]

    def check(i, pad):
        ty, tx = divmod(i, ntx)
        tile = full[ty * th:(ty + 1) * th, tx * tw:(tx + 1) * tw]
        t = tiles[i]
        body = headers[t["head"]] + encode_scan(to_samples(tile, layout), hdrs[t["head"]], pad) + t["tail"]
        return body == data[t["off"]:t["off"] + t["len"]]

    pad = next((p for p in (1, 0) if check(0, p)), None)
    if pad is None:
        raise Unsupported("re-encoding the tiles does not reproduce the original bytes")

    with ThreadPoolExecutor(threads or 8) as ex:
        if not all(ex.map(lambda i: check(i, pad), range(len(tiles)))):
            raise Unsupported("re-encoding the tiles does not reproduce the original bytes")
    # Embedded JPEG previews are repacked losslessly (restored byte for byte)
    tile_ranges = [(t["off"], t["off"] + t["len"]) for t in tiles]
    previews = []
    for off, n in preview_ranges:
        if any(off < e and off + n > s for s, e in tile_ranges):
            continue
        jxl = _repack_jpeg(data[off:off + n])
        if jxl is not None:
            previews.append([off, n, jxl])
    # Skeleton: the file with every tile's and repacked preview's bytes cut out
    cuts = sorted([(t["off"], t["len"]) for t in tiles] + [(p[0], p[1]) for p in previews])
    skel, pos = [], 0
    for off, n in cuts:
        skel.append(data[pos:off])
        pos = off + n
    skel.append(data[pos:])
    rec = {"format": FORMAT, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
           "tile": [th, tw], "pad": pad, "grid": [full.shape[0] // th, ntx], "layout": layout, "visible": [vh, vw],
           "headers": headers, "tiles": [[t["off"], t["len"], t["head"], t["tail"]] for t in tiles],
           "previews": previews,
           "skeleton": b"".join(skel), "shape": list(full.shape),
           # sensor area outside the visible region: the right strip and the bottom strip
           "right": entropy.jxl_encode(full[:vh, vw:], 3) if vw < full.shape[1] else b"",
           "bottom": entropy.jxl_encode(full[vh:, :], 3) if vh < full.shape[0] else b""}
    return zstandard.ZstdCompressor(level=19).compress(msgpack.packb(rec, use_bin_type=True))


def restore(section, visible, threads=None):
    """Rebuild the original file from a captured section and the exact sensor mosaic."""
    rec = msgpack.unpackb(zstandard.ZstdDecompressor().decompress(section), raw=False)
    if rec["format"] != FORMAT:
        raise Unsupported(f"unknown source format {rec['format']}")
    th, tw = rec["tile"]
    vh, vw = rec["visible"]
    full = np.empty(rec["shape"], np.uint16)
    full[:vh, :vw] = visible
    if rec["right"]:
        full[:vh, vw:] = entropy.jxl_decode(rec["right"])
    if rec["bottom"]:
        full[vh:, :] = entropy.jxl_decode(rec["bottom"])
    hdrs = [parse_header(h) for h in rec["headers"]]
    ntx = rec["grid"][1]

    def tile_bytes(i):
        off, n, head, tail = rec["tiles"][i]
        ty, tx = divmod(i, ntx)
        tile = full[ty * th:(ty + 1) * th, tx * tw:(tx + 1) * tw]
        return off, rec["headers"][head] + encode_scan(to_samples(tile, rec["layout"]), hdrs[head],
                                                       rec.get("pad", 1)) + tail

    import imagecodecs
    with ThreadPoolExecutor(threads or 8) as ex:
        parts = list(ex.map(tile_bytes, range(len(rec["tiles"]))))
    parts += [(off, bytes(imagecodecs.jpegxl_decode_jpeg(jxl))) for off, n, jxl in rec.get("previews", [])]
    parts.sort(key=lambda p: p[0])
    out, pos, skel = bytearray(), 0, memoryview(rec["skeleton"])
    for off, body in parts:
        gap = off - len(out)
        out += skel[pos:pos + gap]
        pos += gap
        out += body
    out += skel[pos:]
    out = bytes(out)
    if len(out) != rec["size"] or hashlib.sha256(out).hexdigest() != rec["sha256"]:
        raise ValueError("restored file does not match the original's SHA-256")
    return out
