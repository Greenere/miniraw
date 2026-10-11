""".mraw container: 16-byte preamble, zstd-MessagePack header, sections (spec 5).

Section order on disk is L1, L2, L3 so truncating at the first L3 byte drops the
cold layer. All offsets are absolute.
"""
import struct
import zlib
from pathlib import Path

import msgpack
import zstandard

MAGIC = b"MRAW"
VERSION = 1
FLAG_L3 = 1
PREAMBLE = struct.Struct("<4sHHII")
FIRST_READ = 16 * 1024
LAYER_ORDER = {"L1": 1, "L2": 2, "L3": 3}


def write(header, sections):
    """sections: list of ((layer, plane, ty, tx), payload bytes). Returns file bytes."""
    sections = sorted(sections, key=lambda s: (LAYER_ORDER[s[0][0]], s[0][1:]))
    has_l3 = any(s[0][0] == "L3" for s in sections)
    reserve = 0
    for _ in range(8):
        off = PREAMBLE.size + reserve
        table = []
        for (layer, plane, ty, tx), data in sections:
            table.append([layer, plane, ty, tx, off, len(data), zlib.crc32(data)])
            off += len(data)
        hb = zstandard.ZstdCompressor(level=19).compress(
            msgpack.packb({**header, "sections": table}, use_bin_type=True))
        if len(hb) <= reserve:
            break
        reserve = len(hb) + 64
    else:
        raise RuntimeError("header size did not converge")
    pre = PREAMBLE.pack(MAGIC, VERSION, FLAG_L3 if has_l3 else 0, len(hb), zlib.crc32(hb))
    return b"".join([pre, hb, b"\0" * (reserve - len(hb))] + [d for _, d in sections])


class RangeReader:
    """Byte-range reader over a file or bytes that logs every (offset, length) read."""

    def __init__(self, src, sidecar=None):
        self.data = Path(src).read_bytes() if not isinstance(src, (bytes, bytearray)) else bytes(src)
        self.sidecar = Path(sidecar).read_bytes() if isinstance(sidecar, (str, Path)) else sidecar
        self.log = []
        self._parse()

    def read(self, offset, length):
        self.log.append((offset, length))
        return self.data[offset:offset + length]

    def _parse(self):
        head = self.read(0, FIRST_READ)
        magic, ver, self.flags, hlen, crc = PREAMBLE.unpack_from(head)
        if magic != MAGIC:
            raise ValueError("not a .mraw file")
        if PREAMBLE.size + hlen > len(head):
            head = head + self.read(len(head), PREAMBLE.size + hlen - len(head))
        hb = head[PREAMBLE.size:PREAMBLE.size + hlen]
        if zlib.crc32(hb) != crc:
            raise ValueError("header CRC mismatch")
        self.header = msgpack.unpackb(zstandard.ZstdDecompressor().decompress(hb), raw=False,
                                      strict_map_key=False)
        self.sections = {tuple(s[:4]): s[4:] for s in self.header["sections"]}
        self.l3_start = min((s[4] for s in self.header["sections"] if s[0] == "L3"), default=None)

    @property
    def has_l3(self):
        return bool(self.flags & FLAG_L3) or self.sidecar is not None

    def section(self, layer, plane, ty=0, tx=0):
        off, length, crc = self.sections[(layer, plane, ty, tx)]
        if layer == "L3" and not (self.flags & FLAG_L3):
            if self.sidecar is None:
                raise ValueError("L3 not present (stripped); supply the .mraw.l3 sidecar")
            self.log.append(("sidecar", off - self.l3_start, length))
            data = self.sidecar[off - self.l3_start: off - self.l3_start + length]
        else:
            data = self.read(off, length)
        if zlib.crc32(data) != crc:
            raise ValueError(f"CRC mismatch in section {layer} {plane} {ty} {tx}")
        return data

    def bytes_read(self):
        return sum(e[-1] for e in self.log)


def layer_sizes(header):
    out = {"L1": 0, "L2": 0, "L3": 0}
    for layer, _, _, _, _, length, _ in header["sections"]:
        out[layer] += length
    return out


def strip(data):
    """Truncate at the first L3 byte and clear flag bit 0. Returns (hot bytes, L3 bytes)."""
    r = RangeReader(data)
    if r.l3_start is None or not (r.flags & FLAG_L3):
        return data, b""
    hot = bytearray(data[:r.l3_start])
    magic, ver, flags, hlen, crc = PREAMBLE.unpack_from(hot)
    PREAMBLE.pack_into(hot, 0, magic, ver, flags & ~FLAG_L3, hlen, crc)
    return bytes(hot), data[r.l3_start:]
