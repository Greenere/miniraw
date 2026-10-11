"""B2: JPEG XL lossless on the four raw planes, same tiling/packing/effort as L2."""
from miniraw import entropy, layers


def encode_bytes(planes, tile=512, effort=7):
    total = 0
    for p in planes:
        for _, _, y0, x0, th, tw in layers.tile_grid(p.shape, tile):
            total += len(entropy.jxl_encode(p[y0:y0 + th, x0:x0 + tw], effort))
    return total
