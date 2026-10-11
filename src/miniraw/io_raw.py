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
    cam_xyz: list = None          # 3x3 XYZ -> camera matrix (DNG ColorMatrix1), if known
    flip: int = 0                 # LibRaw orientation code: 0, 3 (180), 5 (90 CCW), 6 (90 CW)
    crop: list = None             # default crop [left, top, width, height] in sensor pixels
    exif: dict = field(default_factory=dict)

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


def read_exif(path):
    """Basic capture metadata from TIFF-based raws (ARW, NEF, CR2, DNG, ...); {} otherwise."""
    try:
        import tifffile
        with tifffile.TiffFile(path) as tf:
            tags = tf.pages[0].tags
            out = {}
            for name in ("Make", "Model", "DateTime"):
                t = tags.get(name)
                if t is not None:
                    out[name] = str(t.value).strip().strip("\x00")
            ex = tags.get("ExifTag")
            ex = ex.value if ex is not None else {}
            for name in ("ExposureTime", "FNumber", "FocalLength"):
                v = ex.get(name)
                if isinstance(v, tuple) and len(v) == 2 and v[1]:
                    out[name] = [int(v[0]), int(v[1])]
            iso = ex.get("ISOSpeedRatings")
            if iso is not None:
                out["ISO"] = int(iso[0] if isinstance(iso, (tuple, list)) else iso)
            for name in ("DateTimeOriginal", "LensModel"):
                if ex.get(name):
                    out[name] = str(ex[name]).strip().strip("\x00")
            return out
    except Exception:
        return {}


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
        cam_xyz = np.array(r.rgb_xyz_matrix)[:3, :3]
        cam_xyz = cam_xyz.tolist() if np.abs(cam_xyz).sum() > 0 else None
        sz = r.sizes
        flip = int(sz.flip)
        crop = None
        if sz.crop_width and sz.crop_height:
            crop = [int(sz.crop_left_margin), int(sz.crop_top_margin), int(sz.crop_width), int(sz.crop_height)]
    planes = pack(mosaic, offsets)
    bit_depth = int(max(white, int(planes.max()))).bit_length()
    return RawImage(planes, offsets, black, white, bit_depth, wb, cm,
                    read_noise_profile(path), meta, cam_xyz, flip, crop, read_exif(path))


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
