"""B2: JPEG XL lossless on the four raw planes, same tiling/packing/effort as L2."""
import numpy as np

from miniraw import entropy, layers


def encode_bytes(planes, tile=512, effort=7, packing="P1"):
    total = 0
    for _, _, y0, x0, th, tw in layers.tile_grid(planes.shape[1:], tile):
        for g in layers.PACKINGS[packing]:
            t = planes[g, y0:y0 + th, x0:x0 + tw]
            total += len(entropy.jxl_encode(t[0] if len(g) == 1 else np.moveaxis(t, 0, -1), effort))
    return total
