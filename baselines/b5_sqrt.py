"""B5: fixed square-root curve to N bits, then JPEG XL lossless (camera-style tone curve).

Uncalibrated: the curve spans the full raw range 0..xmax (black level included,
so nothing below black is clipped). Reconstruction uses the centre of each
code's preimage, as MiniRaw does.
"""
import numpy as np

from miniraw import vst
from . import b2_jxl


def tables(b, white, xmax, bits):
    x = np.arange(xmax + 1, dtype=np.float64)
    fwd = np.rint(np.sqrt(x / xmax) * (2 ** bits - 1)).astype(np.int64)
    # Re-index codes so they are contiguous (some codes are unused at the bottom).
    _, fwd = np.unique(fwd, return_inverse=True)
    lo = vst.lo_from_forward(fwd)
    _, rec = vst.tables_from_lo(lo, xmax)
    return fwd, rec


def run(raw, bits, tile=512, effort=7, packing="P1"):
    """Returns (bytes, decoded planes)."""
    q, dec = [], []
    for p in range(4):
        x = raw.planes[p]
        fwd, rec = tables(raw.black[p], raw.white, int(max(raw.white, int(x.max()))), bits)
        q.append(fwd[x])
        dec.append(rec[fwd[x]])
    return b2_jxl.encode_bytes(np.stack(q), tile, effort, packing), np.stack(dec).astype(np.uint16)
