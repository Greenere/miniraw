"""Keep enough of the original raw file to restore it byte for byte.

For TIFF-based raws whose sensor data is lossless-JPEG tiles (Sony ARW lossless,
lossless DNG, ...), MiniRaw stores the file *skeleton* (everything except the
compressed tiles: TIFF structure, maker notes, embedded previews), each tile's
JPEG header and trailing bytes, and the sensor area outside LibRaw's visible
region. Restoring re-encodes the tiles from the exact sensor data with the
original Huffman tables. Lossless JPEG is deterministic, so this reproduces the
original bytes; capture() checks that at encode time and declines otherwise.
Without the exact sensor data (no L3), write_near_lossless() produces a valid
file in the same format around the near-lossless pixels instead.
"""
import hashlib
import struct
from concurrent.futures import ThreadPoolExecutor

import msgpack
import numpy as np
import zstandard

from . import entropy

FORMAT = "tiff-ljpeg-tiles/2"


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
#
# capture() splits what it keeps in two:
#   container (hot):  the file skeleton without the sensor tiles and without large previews,
#                     tile headers and trailing bytes, the sensor margins outside the visible
#                     area, and where the TIFF tables point at tiles and previews. Enough to
#                     write a valid raw file around near-lossless pixels.
#   previews (cold):  the large embedded previews, repacked losslessly. With the container and
#                     the exact sensor data they restore the original file byte for byte.

BIG_PREVIEW = 256 * 1024     # embedded images above this size are kept in the cold record
_POINTER_TAGS = ((273, 279), (513, 514), (324, 325))   # strip, JPEG-interchange, tile pointers


def _first(v):
    return int(v[0] if isinstance(v, (tuple, list)) else v)


def _tag_loc(tag):
    return [int(tag.valueoffset), int(tag.dtype), int(tag.count)]


def _raw_ifd(path):
    """CFA tile layout and the pointer-tag locations of the CFA image and of embedded images."""
    import tifffile
    with tifffile.TiffFile(path) as tf:
        ifds = [s for p in tf.pages for s in [p] + list(p.pages or [])]
        cands = [s for s in ifds if getattr(s, "photometric", None) is not None and int(s.photometric) == 32803]
        if not cands:
            raise Unsupported("no CFA image in a TIFF structure")
        ifd = max(cands, key=lambda s: s.shape[0] * s.shape[1])
        if int(ifd.compression) != 7 or not ifd.is_tiled:
            raise Unsupported(f"raw data is not lossless-JPEG tiles (compression {int(ifd.compression)})")
        tile_tags = {"off": _tag_loc(ifd.tags[324]), "len": _tag_loc(ifd.tags[325])}
        previews = []
        for s in ifds:
            if s is ifd or len(s.dataoffsets) != 1:
                continue
            off, n = int(s.dataoffsets[0]), int(s.databytecounts[0])
            loc = None
            for ot, lt in _POINTER_TAGS:
                if ot in s.tags and lt in s.tags and s.tags[ot].count == 1 and _first(s.tags[ot].value) == off:
                    loc = (_tag_loc(s.tags[ot]), _tag_loc(s.tags[lt]))
                    break
            previews.append({"off": off, "len": n, "h": int(s.shape[0]), "w": int(s.shape[1]),
                             "off_tag": loc[0] if loc else None, "len_tag": loc[1] if loc else None})
        return (list(ifd.dataoffsets), list(ifd.databytecounts), ifd.shape,
                (int(ifd.tilelength), int(ifd.tilewidth)), previews, tile_tags, tf.byteorder)


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


def _pack(rec):
    return zstandard.ZstdCompressor(level=19).compress(msgpack.packb(rec, use_bin_type=True))


def _unpack(blob):
    rec = msgpack.unpackb(zstandard.ZstdDecompressor().decompress(blob), raw=False)
    if rec.get("format") != FORMAT:
        raise Unsupported(f"unknown source format {rec.get('format')}")
    return rec


def capture(path, visible, threads=None, data=None, previews=True):
    """(container section, previews section) for restoring `path`; see the note above.

    visible: the exact sensor mosaic MiniRaw encodes (LibRaw raw_image_visible, cropped to even
    size), at the top-left of LibRaw's raw_image. Raises Unsupported unless re-encoding the
    tiles reproduces the original bytes exactly. data: the file's bytes if already read.
    previews=False skips repacking the large previews (the cold record is then None); their
    locations are still recorded, which is all write_near_lossless() needs.
    """
    import imagecodecs
    if data is None:
        with open(path, "rb") as fh:
            data = fh.read()
    offs, cnts, shape, (th, tw), embedded, tile_tags, byteorder = _raw_ifd(path)
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
        tiles.append([o, c, headers.index(head), t[eoi:]])
    vh, vw = visible.shape
    if not np.array_equal(full[:vh, :vw], visible):
        raise Unsupported("decoded tiles do not match the sensor data LibRaw reports")
    hdrs = [parse_header(h) for h in headers]

    def check(i, pad):
        o, c, head, tail = tiles[i]
        ty, tx = divmod(i, ntx)
        tile = full[ty * th:(ty + 1) * th, tx * tw:(tx + 1) * tw]
        return headers[head] + encode_scan(to_samples(tile, layout), hdrs[head], pad) + tail == data[o:o + c]

    pad = next((p for p in (1, 0) if check(0, p)), None)
    if pad is None:
        raise Unsupported("re-encoding the tiles does not reproduce the original bytes")
    with ThreadPoolExecutor(threads or 8) as ex:
        if not all(ex.map(lambda i: check(i, pad), range(len(tiles)))):
            raise Unsupported("re-encoding the tiles does not reproduce the original bytes")

    tile_ranges = [(t[0], t[0] + t[1]) for t in tiles]
    big, cold = [], []
    for e in embedded:
        overlaps = any(e["off"] < b and e["off"] + e["len"] > a for a, b in tile_ranges)
        if e["len"] > BIG_PREVIEW and not overlaps and e["off_tag"]:
            if previews:
                blob = data[e["off"]:e["off"] + e["len"]]
                jxl = _repack_jpeg(blob)
                cold.append([jxl or blob, jxl is not None])
            big.append(e)
    cuts = sorted([(t[0], t[1]) for t in tiles] + [(e["off"], e["len"]) for e in big])
    skel, pos = [], 0
    for off, n in cuts:
        skel.append(data[pos:off])
        pos = off + n
    skel.append(data[pos:])
    container = {"format": FORMAT, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                 "byteorder": byteorder, "tile": [th, tw], "pad": pad, "grid": [full.shape[0] // th, ntx],
                 "layout": layout, "visible": [vh, vw], "shape": list(full.shape), "headers": headers,
                 "tiles": tiles, "tile_tags": tile_tags, "big_previews": big, "skeleton": b"".join(skel),
                 # sensor area outside the visible region: the right strip and the bottom strip
                 "right": entropy.jxl_encode(full[:vh, vw:], 3) if vw < full.shape[1] else b"",
                 "bottom": entropy.jxl_encode(full[vh:, :], 3) if vh < full.shape[0] else b""}
    return _pack(container), (_pack({"format": FORMAT, "previews": cold}) if previews else None)


def _sensor(rec, visible):
    vh, vw = rec["visible"]
    full = np.empty(rec["shape"], np.uint16)
    full[:vh, :vw] = visible
    if rec["right"]:
        full[:vh, vw:] = entropy.jxl_decode(rec["right"])
    if rec["bottom"]:
        full[vh:, :] = entropy.jxl_decode(rec["bottom"])
    return full


def _base(rec):
    """The original file layout with the cut regions (tiles, big previews) zero-filled."""
    out = bytearray(rec["size"])
    cuts = sorted([(t[0], t[1]) for t in rec["tiles"]] + [(e["off"], e["len"]) for e in rec["big_previews"]])
    skel, pos, sp = memoryview(rec["skeleton"]), 0, 0
    for off, n in cuts + [(rec["size"], 0)]:
        out[pos:off] = skel[sp:sp + off - pos]
        sp += off - pos
        pos = off + n
    return out


def _tiles(rec, full, threads, strict):
    """Encoded tile bodies; strict=False falls back to a full Huffman table if a category is missing."""
    th, tw = rec["tile"]
    ntx = rec["grid"][1]
    hdrs = [parse_header(h) for h in rec["headers"]]

    def body(i):
        _, _, head, tail = rec["tiles"][i]
        ty, tx = divmod(i, ntx)
        samples = to_samples(full[ty * th:(ty + 1) * th, tx * tw:(tx + 1) * tw], rec["layout"])
        try:
            return rec["headers"][head] + encode_scan(samples, hdrs[head], rec["pad"]) + tail
        except Unsupported:
            if strict:
                raise
            h = _with_full_table(rec["headers"][head])
            return h + encode_scan(samples, parse_header(h), rec["pad"]) + b"\xff\xd9"

    with ThreadPoolExecutor(threads or 8) as ex:
        return list(ex.map(body, range(len(rec["tiles"]))))


def _with_full_table(header):
    """Header copy whose Huffman tables cover all 17 difference categories (5-bit codes)."""
    segs, _, _ = _segments(header + b"\xff\xd9")
    seg = lambda m, p: bytes([0xFF, m]) + (len(p) + 2).to_bytes(2, "big") + p  # noqa: E731
    sos = bytearray(segs[0xDA])
    for i in range(sos[0]):
        sos[2 + 2 * i] = 0x00
    dht = bytes([0x00]) + bytes([0, 0, 0, 0, 17] + [0] * 11) + bytes(range(17))
    return b"\xff\xd8" + seg(0xC3, segs[0xC3]) + seg(0xC4, dht) + seg(0xDA, bytes(sos))


def restore(container, previews, visible, threads=None):
    """The original file, byte for byte, from both records and the exact sensor mosaic."""
    import imagecodecs
    rec, cold = _unpack(container), _unpack(previews)
    out = _base(rec)
    for (off, n, _, _), body in zip(rec["tiles"], _tiles(rec, _sensor(rec, visible), threads, True)):
        out[off:off + n] = body
    for e, (blob, repacked) in zip(rec["big_previews"], cold["previews"]):
        out[e["off"]:e["off"] + e["len"]] = bytes(imagecodecs.jpegxl_decode_jpeg(blob)) if repacked else blob
    out = bytes(out)
    if hashlib.sha256(out).hexdigest() != rec["sha256"]:
        raise ValueError("restored file does not match the original's SHA-256")
    return out


def _put(out, loc, value, byteorder):
    vo, dtype, count = loc
    fmt = {3: "H", 4: "I"}[dtype]
    out[vo:vo + struct.calcsize(fmt)] = struct.pack(byteorder + fmt, value)


def write_near_lossless(container, visible, render_preview, threads=None):
    """A valid raw file in the original's format around near-lossless sensor data.

    Tiles are re-encoded from `visible`. When the original tiles form one contiguous block they
    are packed back to back in it (the file grows or shrinks only if that block ends the file);
    otherwise each stays in its slot if it fits and is appended if not. The TIFF offset/length
    tables are updated either way. Large previews are regenerated with
    render_preview(h, w, max_bytes) -> JPEG bytes and placed the same way. Everything else
    (TIFF structure, maker notes, small previews) is the original's.
    """
    rec = _unpack(container)
    bo = rec["byteorder"]
    out = _base(rec)
    bodies = _tiles(rec, _sensor(rec, visible), threads, False)
    tail = bytearray()

    def place(off, slot, body):
        """Write body into its original slot if it fits, else append it (word-aligned)."""
        if len(body) <= slot:
            out[off:off + len(body)] = body
            out[off + len(body):off + slot] = bytes(slot - len(body))
            return off
        if (len(out) + len(tail)) & 1:
            tail.append(0)
        pos = len(out) + len(tail)
        tail.extend(body)
        return pos

    vo, dtype, count = rec["tile_tags"]["off"]
    lo, ldt, _ = rec["tile_tags"]["len"]
    size = {3: 2, 4: 4}
    order = sorted(range(len(rec["tiles"])), key=lambda i: rec["tiles"][i][0])
    first = rec["tiles"][order[0]][0]
    block_end = max(t[0] + t[1] for t in rec["tiles"])
    contiguous = all(rec["tiles"][a][0] + rec["tiles"][a][1] == rec["tiles"][b][0] for a, b in zip(order, order[1:]))
    total = sum(len(b) for b in bodies)
    new_offs = {}
    if contiguous and (block_end == rec["size"] or total <= block_end - first):
        # Pack the tiles back to back where the original block was.
        packed = b"".join(bodies[i] for i in order)
        pos = first
        for i in order:
            new_offs[i] = pos
            pos += len(bodies[i])
        if block_end == rec["size"]:
            del out[first:]
            out += packed
        else:
            out[first:first + len(packed)] = packed
            out[first + len(packed):block_end] = bytes(block_end - first - len(packed))
    else:
        for i, (off, n, _, _) in enumerate(rec["tiles"]):
            new_offs[i] = place(off, n, bodies[i])
    for i, body in enumerate(bodies):
        _put(out, [vo + i * size[dtype], dtype, 1], new_offs[i], bo)
        _put(out, [lo + i * size[ldt], ldt, 1], len(body), bo)
    for e in rec["big_previews"]:
        if e["off"] + e["len"] > len(out):
            raise Unsupported("a large preview overlaps the repacked tile block")
        jpg = render_preview(e["h"], e["w"], e["len"])
        new_off = place(e["off"], e["len"], jpg)
        _put(out, e["off_tag"], new_off, bo)
        _put(out, e["len_tag"], len(jpg), bo)
    return bytes(out) + bytes(tail)
