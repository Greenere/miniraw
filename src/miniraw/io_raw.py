"""rawpy ingest, CFA plane packing, metadata (spec 4.1)."""
from dataclasses import dataclass, field

import numpy as np

PLANE_NAMES = ("R", "G1", "G2", "B")


class NotBayer(Exception):
    pass


@dataclass
class RawImage:
    planes: np.ndarray            # (4, H/2, W/2) uint16, order R, G1, G2, B
    offsets: list                 # per plane (dy, dx) within the 2x2 CFA cell
    black: list                   # per plane black level (DN)
    white: int
    bit_depth: int
    wb: list = field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])
    color_matrix: list = None     # 3x3 camera->sRGB, if available
    noise_profile: list = None    # DNG NoiseProfile (S, O) pairs, if present
    meta: dict = field(default_factory=dict)

    @property
    def sensor_shape(self):
        return self.planes.shape[1] * 2, self.planes.shape[2] * 2


def pack(mosaic, offsets):
    return np.stack([mosaic[dy::2, dx::2] for dy, dx in offsets])


def unpack(planes, offsets):
    _, h, w = planes.shape
    m = np.empty((h * 2, w * 2), planes.dtype)
    for p, (dy, dx) in zip(planes, offsets):
        m[dy::2, dx::2] = p
    return m


def cfa_offsets(colors2x2, desc):
    """Map a 2x2 color-index cell to (dy, dx) of R, G1, G2, B (G1 first in raster order)."""
    names = [desc[c] for c in colors2x2.ravel()]
    if sorted(names) != ["B", "G", "G", "R"]:
        raise NotBayer(f"CFA cell {names}")
    cells = [(i // 2, i % 2) for i in range(4)]
    g = [cells[i] for i, n in enumerate(names) if n == "G"]
    return [cells[names.index("R")], g[0], g[1], cells[names.index("B")]]


def read_noise_profile(path):
    """DNG NoiseProfile tag (51041) via tifffile; None if absent."""
    try:
        import tifffile
    except ImportError:
        return None
    try:
        with tifffile.TiffFile(path) as tf:
            for page in tf.pages:
                t = page.tags.get(51041)
                if t is not None:
                    v = list(t.value)
                    return [(v[i], v[i + 1]) for i in range(0, len(v), 2)]
    except Exception:
        return None
    return None


def read(path):
    import rawpy

    with rawpy.imread(str(path)) as r:
        if r.raw_pattern is None or np.asarray(r.raw_pattern).shape != (2, 2):
            raise NotBayer(f"raw_pattern {r.raw_pattern}")
        desc = r.color_desc.decode()
        mosaic = np.array(r.raw_image_visible, dtype=np.uint16)
        colors = np.array(r.raw_colors_visible[:2, :2])
        h, w = mosaic.shape
        mosaic = mosaic[: h - h % 2, : w - w % 2]
        offsets = cfa_offsets(colors, desc)
        bl = list(r.black_level_per_channel)
        black = [int(bl[colors[dy, dx]]) for dy, dx in offsets]
        white = int(r.white_level)
        wbc = list(r.camera_whitebalance)
        wb = [float(wbc[colors[dy, dx]]) for dy, dx in offsets]
        cm = None
        try:
            m = np.array(r.color_matrix)[:3, :3]
            if np.abs(m).sum() > 0:
                cm = m.tolist()
        except Exception:
            pass
        meta = {"color_desc": desc, "raw_type": str(r.raw_type)}
    planes = pack(mosaic, offsets)
    bit_depth = int(max(white, int(planes.max()))).bit_length()
    return RawImage(planes, offsets, black, white, bit_depth, wb, cm,
                    read_noise_profile(path), meta)


GAP_THRESHOLD = 0.05


def histogram_gaps(planes, black, white):
    """Fraction of empty DN codes among well-populated codes (local mean >= 20 pixels).

    Tone-curve-compressed sources (pitfall 2) show many empty codes; genuine
    linear raw shows almost none. Sparse highlight codes are ignored.
    """
    fr = []
    for p in planes:
        h = np.bincount(p.ravel()).astype(np.float64)
        local = np.convolve(h, np.ones(33) / 33, mode="same")
        dense = local >= 20
        fr.append(float((h[dense] == 0).mean()) if dense.any() else 0.0)
    return max(fr)
