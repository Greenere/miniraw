"""JPEG XL lossless wrapper and range coder wrapper (spec 4.4, 4.6)."""
import struct

import constriction
import imagecodecs
import numpy as np

JXL_THREADS = 1


def jxl_version():
    return imagecodecs.jpegxl_version()


def jxl_encode(img, effort=7):
    """Lossless JPEG XL of a 2-D (or HxWxC) integer array.

    Signed or wide data is shifted by its minimum; the shift is stored as an
    int32 prefix so the payload is self-describing.
    """
    img = np.asarray(img)
    off = int(img.min()) if img.size else 0
    u = (img.astype(np.int64) - off)
    if u.size and u.max() > 65535:
        raise ValueError("range exceeds 16 bits")
    payload = imagecodecs.jpegxl_encode(np.ascontiguousarray(u.astype(np.uint16)), lossless=True,
                                        effort=effort, numthreads=JXL_THREADS)
    return struct.pack("<i", off) + payload


def jxl_decode(buf):
    off = struct.unpack_from("<i", buf)[0]
    img = imagecodecs.jpegxl_decode(buf[4:], numthreads=JXL_THREADS)
    return img.astype(np.int64) + off


_UNIFORM = constriction.stream.model.Uniform()


def kappa_encode(kappa, n):
    """Range-code bin positions under a uniform model per pixel; size-1 bins cost nothing."""
    kappa = np.asarray(kappa).ravel()
    n = np.asarray(n).ravel()
    sel = n > 1
    if not sel.any():
        return b""
    enc = constriction.stream.queue.RangeEncoder()
    enc.encode(kappa[sel].astype(np.int32), _UNIFORM, n[sel].astype(np.int32))
    return enc.get_compressed().tobytes()


def kappa_decode(buf, n):
    n = np.asarray(n)
    out = np.zeros(n.shape, np.int64)
    sel = n > 1
    if not sel.any():
        return out
    dec = constriction.stream.queue.RangeDecoder(np.frombuffer(buf, np.uint32).copy())
    out[sel] = dec.decode(_UNIFORM, n[sel].astype(np.int32))
    return out
