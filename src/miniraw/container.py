""".mraw container: 16-byte preamble, zstd-MessagePack header, sections (spec 5).

Section order on disk is IDX, L1, L2, CTR, L3, SRC. IDX (format version 2) is a small index
thumbnail placed first, so the header and IDX come back in one short read. Truncating at the first L3 byte
drops the cold layers: L3 (exact sensor data) and SRC (large embedded previews of
the original raw file). CTR, the original file's container, stays hot so a valid
file in the original format can still be written around near-lossless pixels.
All offsets are absolute.
"""
import os
import struct
import threading
import zlib
from pathlib import Path

import msgpack
import zstandard

MAGIC = b"MRAW"
VERSION = 2             # 1: v1.0 files; 2: adds IDX, the stored embedding and rec "csafe" (D29, D30)
FLAG_L3 = 1
PREAMBLE = struct.Struct("<4sHHII")
FIRST_READ = 16 * 1024
LAYER_ORDER = {"IDX": 0, "L1": 1, "L2": 2, "CTR": 3, "L3": 4, "SRC": 5}
COLD = ("L3", "SRC")


def write(header, sections, version=VERSION):
    """sections: list of ((layer, plane, ty, tx), payload bytes). Returns file bytes."""
    if not 1 <= version <= VERSION:
        raise ValueError(f"format version {version} not supported (1..{VERSION})")
    sections = sorted(sections, key=lambda s: (LAYER_ORDER[s[0][0]], s[0][1:]))
    has_l3 = any(s[0][0] == "L3" for s in sections)
    crcs = [zlib.crc32(data) for _, data in sections]
    reserve = 0
    for _ in range(8):
        off = PREAMBLE.size + reserve
        table = []
        for ((layer, plane, ty, tx), data), crc in zip(sections, crcs):
            table.append([layer, plane, ty, tx, off, len(data), crc])
            off += len(data)
        hb = zstandard.ZstdCompressor(level=19).compress(
            msgpack.packb({**header, "sections": table}, use_bin_type=True))
        if len(hb) <= reserve:
            break
        reserve = len(hb) + 64
    else:
        raise RuntimeError("header size did not converge")
    pre = PREAMBLE.pack(MAGIC, version, FLAG_L3 if has_l3 else 0, len(hb), zlib.crc32(hb))
    return b"".join([pre, hb, b"\0" * (reserve - len(hb))] + [d for _, d in sections])


class _Bytes:
    """In-memory backing store."""

    def __init__(self, b):
        self.b = bytes(b)
        self.size = len(self.b)

    def read_at(self, offset, length):
        return self.b[offset:offset + length]

    def close(self):
        pass


class _File:
    """File backing store using positional reads (safe across concurrent tile jobs).

    Windows has no os.pread: there, reads seek and read under a lock instead.
    """

    def __init__(self, path):
        self.path = str(path)
        self.fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        self.size = os.fstat(self.fd).st_size
        self._lock = None if hasattr(os, "pread") else threading.Lock()

    def _pread(self, n, offset):
        if self._lock is None:
            return os.pread(self.fd, n, offset)
        with self._lock:
            os.lseek(self.fd, offset, os.SEEK_SET)
            return os.read(self.fd, n)

    def read_at(self, offset, length):
        length = max(0, min(length, self.size - offset))
        parts, got = [], 0
        while got < length:
            b = self._pread(length - got, offset + got)
            if not b:
                break
            parts.append(b)
            got += len(b)
        return b"".join(parts)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __del__(self):
        self.close()


def _source(src):
    if src is None or isinstance(src, (_Bytes, _File)):
        return src
    if isinstance(src, (bytes, bytearray, memoryview)):
        return _Bytes(src)
    return _File(src)


class RangeReader:
    """Byte-range reader over a file (positional reads, nothing loaded up front) or bytes.

    `log` records every read actually issued: (offset, length) on the main file and
    ("sidecar", offset, length) on the sidecar. Initialisation reads the first FIRST_READ bytes
    (preamble + header) and, only if the header is longer, the rest of it.
    """

    def __init__(self, src, sidecar=None):
        self._src = _source(src)
        self._sidecar = sidecar          # opened on first use
        self.log = []
        self._parse()

    def read(self, offset, length):
        self.log.append((offset, length))
        return self._src.read_at(offset, length)

    @property
    def sidecar(self):
        if self._sidecar is not None and not isinstance(self._sidecar, (_Bytes, _File)):
            self._sidecar = _source(self._sidecar)
        return self._sidecar

    def close(self):
        self._src.close()
        if isinstance(self._sidecar, (_Bytes, _File)):
            self._sidecar.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False

    def _parse(self):
        head = self.read(0, FIRST_READ)
        if len(head) < PREAMBLE.size:
            raise ValueError("not a .mraw file")
        magic, ver, self.flags, hlen, crc = PREAMBLE.unpack_from(head)
        if magic != MAGIC:
            raise ValueError("not a .mraw file")
        if ver > VERSION:
            raise ValueError(f"written by a newer MiniRaw (format version {ver}); this reader supports up to {VERSION}")
        self.version = ver
        if PREAMBLE.size + hlen > len(head):
            head = head + self.read(len(head), PREAMBLE.size + hlen - len(head))
        hb = head[PREAMBLE.size:PREAMBLE.size + hlen]
        if zlib.crc32(hb) != crc:
            raise ValueError("the file is damaged or truncated (header CRC mismatch)")
        self.header = msgpack.unpackb(zstandard.ZstdDecompressor().decompress(hb), raw=False,
                                      strict_map_key=False)
        self.sections = {tuple(s[:4]): s[4:] for s in self.header["sections"]}
        self.l3_start = min((s[4] for s in self.header["sections"] if s[0] in COLD), default=None)

    @property
    def has_l3(self):
        return bool(self.flags & FLAG_L3) or self._sidecar is not None

    def section(self, layer, plane, ty=0, tx=0):
        off, length, crc = self.sections[(layer, plane, ty, tx)]
        if layer in COLD and not (self.flags & FLAG_L3):
            if self.sidecar is None:
                raise ValueError("L3 not present (stripped); supply the .mraw.l3 sidecar")
            self.log.append(("sidecar", off - self.l3_start, length))
            data = self.sidecar.read_at(off - self.l3_start, length)
        else:
            data = self.read(off, length)
        if zlib.crc32(data) != crc:
            raise ValueError(f"the file is damaged or truncated (CRC mismatch in section {layer} {plane} {ty} {tx})")
        return data

    @property
    def has_container(self):
        """The original raw file's container is stored (hot)."""
        return ("CTR", 0, 0, 0) in self.sections

    @property
    def has_original(self):
        """The original raw file can be restored byte for byte."""
        return self.has_container and ("SRC", 0, 0, 0) in self.sections and self.has_l3

    def bytes_read(self):
        """Bytes requested from the main file and the sidecar."""
        return sum(e[-1] for e in self.log)


def layer_sizes(header):
    out = {"IDX": 0, "L1": 0, "L2": 0, "CTR": 0, "L3": 0, "SRC": 0}
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
