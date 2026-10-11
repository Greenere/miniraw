"""encode() / decode() public API.

Decoding uses only integer arithmetic and tables stored in the file (spec 4).
"""
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import container, entropy, io_raw, layers, noise, vst
from . import stats as stats_mod

def mosaic_sha256(planes, offsets, rows=256):
    """SHA-256 of the mosaic in raster order (little-endian uint16), hashed in row strips."""
    h = hashlib.sha256()
    for r0 in range(0, planes.shape[1], rows):
        h.update(io_raw.unpack(planes[:, r0:r0 + rows], offsets).astype("<u2", copy=False).tobytes())
    return h.hexdigest()


def fit_noise(plane, b, white, noise_profile=None, plane_index=0, diagnostics=True):
    """Noise model for one plane, with validation and a lower-only isotonic correction (spec 4.2, D26).

    diagnostics=False skips re-validating the isotonic fallback (report-only data).
    """
    xmax = int(max(white, int(plane.max())))
    try:
        model, (xs, vs, ws), blocks = noise.estimate(plane, b, white, return_blocks=True)
    except noise.TooFewBlocks:
        # Very dark or sparse planes (e.g. Leica M8 companded raws): use the DNG profile if there is
        # one, else keep every value exact. Neither can be validated without flat blocks.
        if noise_profile:
            model, fallback = noise.from_noise_profile(noise_profile, plane_index, b, white), "noise_profile"
        else:
            model, fallback = noise.lossless_model(b, xmax), "lossless"
        info = {"estimated": {}, "fallback": fallback, "validate_sigma": [], "validated": None, "model": model}
        return model, vst.T_table(model, xmax), info
    info = {"estimated": dict(model)}
    if noise_profile:
        npm = noise.from_noise_profile(noise_profile, plane_index, b, white)
        info["noise_profile_disagreement"] = noise.disagreement(npm, model, white)
        model = npm
    T = vst.T_table(model, xmax)
    _, sig, _ = noise.stabilized_sigma_by_bin(plane, model, white, T, blocks)
    info["validate_sigma"] = sig.tolist()
    ok = len(sig) > 0 and bool(np.all((sig >= 0.9) & (sig <= 1.1)))
    info["validated"] = ok
    # Lower-only correction (D26): sigma below 0.9 means the model over-estimates the noise there, which would
    # discard signal. Sigma above 1.1 is left alone: it is usually texture, and an under-estimate only costs bits.
    if len(sig) and np.any(sig < 0.9):
        lowered = noise.lowered_model(model, xs, vs, ws, b, xmax)
        if lowered is not model:
            model = lowered
            T = vst.T_table(model, xmax)
            if diagnostics:
                _, sig2, _ = noise.stabilized_sigma_by_bin(plane, model, white, T, blocks)
                info["validate_sigma_isotonic"] = sig2.tolist()
    info["model"] = model
    return model, T, info


def default_threads():
    return min(8, os.cpu_count() or 1)


def _plane_state(raw, p, step, models, use_l1, l1_mode, k, levels, grid, diagnostics):
    """Noise model, tables, q and L1 for one plane (runs in a worker thread)."""
    x = raw.planes[p]
    b, white = raw.black[p], raw.white
    xmax = int(max(white, int(x.max())))
    if models is None:
        model, T, pinfo = fit_noise(x, b, white, raw.noise_profile, p, diagnostics)
    else:
        model, pinfo = models[p], {"model": models[p], "validated": None}
        T = vst.T_table(model, xmax)
    fwd = vst.forward_table(T, step, white)
    lo = vst.lo_from_forward(fwd)
    n, _ = vst.tables_from_lo(lo, xmax)
    q = fwd[x]
    deltas = vst.encode_lo(lo)
    assert deltas.max(initial=0) < 65536
    pinfo.update(T=T, fwd=fwd, q=q, nq=len(lo))
    st = {"x": x, "q": q, "lo": lo, "n": n, "info": pinfo, "l1": None, "coeffs": None,
          "hdr": {"xmax": xmax, "lo": deltas.astype("<u2").tobytes(), "model": model}}
    if use_l1 and l1_mode == "mean":
        st["l1"] = layers.l1_from_q(q, k)
    elif use_l1:
        st["l1"] = np.zeros((-(-x.shape[0] // k), -(-x.shape[1] // k)), np.int64)
        st["coeffs"] = {}
        for ty, tx, y0, x0, th, tw in grid:
            c, (lh, lw) = layers.dwt53(q[y0:y0 + th, x0:x0 + tw], levels)
            st["l1"][y0 // k:y0 // k + lh, x0 // k:x0 // k + lw] = c[:lh, :lw]
            c[:lh, :lw] = 0
            st["coeffs"][ty, tx] = c
    return st


def encode(raw, step=1.0, tile=512, effort=4, use_l1=True, upsampler="bilinear",
           with_l3=True, models=None, source=None, l1_mode="mean", rec="floor", k=None,
           packing=None, l1_min_side=128, threads=None, diagnostics=False, keep_original=False,
           l2_pred="cross", l1_step=1, g1_pred="l1"):
    """Encode a RawImage. `models` (per-plane noise model dicts) skips estimation.

    l1_mode "mean" is construction A (floor-mean L1, L2 codes q - up(L1));
    "wavelet" is construction B (per-tile 5/3 lifting, L1 = LL bands, L2 = detail bands).
    packing names a plane grouping in layers.PACKINGS for the L2 JPEG XL images (default: P1 with
    cross-plane prediction, P3 otherwise).
    l2_pred "cross" codes each plane's L2 relative to a prediction from already-decoded planes in the
    tile plus L1 (layers.cross_predict); "l1" codes q - up(L1) for every plane (the earlier format).
    l1_min_side sets k: the largest power of two keeping L1's shorter side >= it.
    rec selects the signal-mode reconstruction: "floor" is spec 4.3's lo + (n-1)//2;
    "checker" alternates floor/ceil of the bin centre on a pixel checkerboard (deviation D17).
    threads: worker threads for planes and tiles (output is identical for any value).
    diagnostics: also re-validate isotonic fallbacks (benchmark reporting only).
    keep_original: also store the source raw file's container (hot CTR section) and its large
    previews (cold SRC, only with L3). With L3 the original file restores byte for byte; without
    it a valid file in the same format is written around the near-lossless pixels (source.py).

    Returns (file bytes, info dict). info holds per-plane T tables and q planes
    for the benchmark's metrics; they are not part of the file.
    """
    planes = raw.planes
    pshape = planes.shape[1:]
    if l2_pred == "cross" and use_l1 and l1_mode != "mean":
        l2_pred = "l1"                      # cross-plane prediction is defined on construction A
    packing = packing or ("P1" if l2_pred == "cross" else "P3")
    k = (k or layers.choose_k(pshape, l1_min_side)) if use_l1 else 0
    levels = k.bit_length() - 1 if use_l1 else 0
    if use_l1 and l1_mode == "wavelet" and tile is not None:
        assert tile % k == 0
    groups = layers.PACKINGS[packing]
    grid = layers.tile_grid(pshape, tile)
    with ThreadPoolExecutor(threads or default_threads()) as ex:
        st = list(ex.map(lambda p: _plane_state(raw, p, step, models, use_l1, l1_mode, k, levels, grid, diagnostics),
                         range(4)))
        if use_l1 and l1_mode == "mean" and l1_step > 1:     # store L1 coarser; predict from what is stored
            for s in st:
                s["l1_stored"] = s["l1"] // l1_step
                s["l1"] = layers.l1_dequantize(s["l1_stored"], l1_step)

        def tile_job(t):
            ty, tx, y0, x0, th, tw = t
            out, payload = [], []
            rows, cols = np.arange(y0, y0 + th), np.arange(x0, x0 + tw)
            if l2_pred == "cross":
                qt = [s["q"][y0:y0 + th, x0:x0 + tw].astype(np.int64) for s in st]
                up = [layers.upsample(s["l1"], k, rows, cols, upsampler) if use_l1 else np.zeros((th, tw), np.int64)
                      for s in st]
                payload = [qt[p] - layers.cross_predict(p, qt, up, raw.offsets, g1_pred == "l1") for p in range(4)]
            else:
                for s in st:
                    qt = s["q"][y0:y0 + th, x0:x0 + tw]
                    if not use_l1:
                        payload.append(qt)
                    elif l1_mode == "mean":
                        payload.append(qt - layers.upsample(s["l1"], k, rows, cols, upsampler))
                    else:
                        payload.append(s["coeffs"][ty, tx])
            for p, s in enumerate(st):
                if with_l3:
                    qt = s["q"][y0:y0 + th, x0:x0 + tw]
                    kappa = s["x"][y0:y0 + th, x0:x0 + tw].astype(np.int64) - s["lo"][qt]
                    out.append((("L3", p, ty, tx), entropy.kappa_encode(kappa, s["n"][qt])))
            for g, members in enumerate(groups):
                img = payload[members[0]] if len(members) == 1 else np.stack([payload[p] for p in members], -1)
                out.append((("L2", g, ty, tx), entropy.jxl_encode(img, effort)))
            return out

        sections = [sec for secs in ex.map(tile_job, grid) for sec in secs]
        if use_l1:
            sections += list(ex.map(lambda p: (("L1", p, 0, 0), entropy.jxl_encode(st[p].get("l1_stored", st[p]["l1"]),
                                                                                  effort)), range(4)))

    original = None
    if keep_original and raw.meta.get("path"):
        from . import source as source_mod
        path = raw.meta["path"]
        with open(path, "rb") as fh:
            blob = fh.read()
        original = {"name": os.path.basename(path), "ext": os.path.splitext(path)[1], "size": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest()}
        try:
            ctr, previews = source_mod.capture(path, io_raw.unpack(planes, raw.offsets), threads,
                                               data=blob, previews=with_l3)
            sections.append((("CTR", 0, 0, 0), ctr))
            if with_l3:
                sections.append((("SRC", 0, 0, 0), previews))
            original.update(container=True, restorable=with_l3)
        except source_mod.Unsupported as e:
            original.update(container=False, restorable=False, reason=str(e))

    h, w = raw.sensor_shape
    header = {
        "sensor": {"width": w, "height": h, "cfa": [list(o) for o in raw.offsets],
                   "black": list(raw.black), "white": raw.white, "wb": raw.wb,
                   "color_matrix": raw.color_matrix, "bit_depth": raw.bit_depth,
                   "cam_xyz": raw.cam_xyz, "flip": raw.flip, "crop": raw.crop},
        "source": {**(source or {}), "exif": raw.exif, "sha256": mosaic_sha256(planes, raw.offsets),
                   **({"original": original} if original else {})},
        "noise": {"step": step, "planes": [s["hdr"]["model"] for s in st]},
        "tables": [{"xmax": s["hdr"]["xmax"], "lo": s["hdr"]["lo"]} for s in st],
        "layout": {"tile": tile, "k": k, "upsampler": upsampler, "packing": packing,
                   "plane_shape": list(pshape), "l1": use_l1,
                   "l1_mode": l1_mode, "rec": rec, "l2_pred": l2_pred,
                   **({"l1_step": l1_step} if l1_step != 1 else {}), **({"g1_pred": g1_pred} if g1_pred != "l1" else {})},
        "stats": stats_mod.technical(raw, [s["hdr"]["model"] for s in st]),
        "embed": None,
    }
    return container.write(header, sections), {"planes": [s["info"] for s in st]}


class Decoder:
    def __init__(self, reader, threads=None):
        self.r = reader
        self.threads = threads or default_threads()
        hd = reader.header
        self.layout = hd["layout"]
        self.pshape = tuple(self.layout["plane_shape"])
        self.offsets = [tuple(o) for o in hd["sensor"]["cfa"]]
        self.tables = []
        for t in hd["tables"]:
            lo = vst.decode_lo(np.frombuffer(t["lo"], "<u2"))
            n, rec = vst.tables_from_lo(lo, t["xmax"])
            self.tables.append((lo, n, rec))
        self._l1 = {}
        self.groups = layers.PACKINGS[self.layout.get("packing", "P1")]

    def l1(self, p):
        if p not in self._l1:
            self._l1[p] = layers.l1_dequantize(entropy.jxl_decode(self.r.section("L1", p)).astype(np.int64),
                                               self.layout.get("l1_step", 1))
        return self._l1[p]

    def q_tiles(self, t):
        """q for all four planes of one tile."""
        ty, tx, y0, x0, th, tw = t
        d = [None] * 4
        for g, members in enumerate(self.groups):
            arr = entropy.jxl_decode(self.r.section("L2", g, ty, tx))
            for i, p in enumerate(members):
                d[p] = arr if arr.ndim == 2 else arr[..., i]
        if self.layout.get("l2_pred") == "cross":
            k = self.layout["k"]
            rows, cols = np.arange(y0, y0 + th), np.arange(x0, x0 + tw)
            up = [layers.upsample(self.l1(p), k, rows, cols, self.layout["upsampler"]) if self.layout["l1"]
                  else np.zeros((th, tw), np.int64) for p in range(4)]
            q = [None] * 4
            for p in layers.CROSS_ORDER:
                q[p] = d[p].astype(np.int64) + layers.cross_predict(p, q, up, self.offsets,
                                                                     self.layout.get("g1_pred", "l1") == "l1")
            return q
        if not self.layout["l1"]:
            return d
        k = self.layout["k"]
        if self.layout.get("l1_mode") == "wavelet":
            lh, lw = -(-th // k), -(-tw // k)
            out = []
            for p in range(4):
                c = d[p].copy()
                c[:lh, :lw] = self.l1(p)[y0 // k:y0 // k + lh, x0 // k:x0 // k + lw]
                out.append(layers.idwt53(c, k.bit_length() - 1))
            return out
        rows, cols = np.arange(y0, y0 + th), np.arange(x0, x0 + tw)
        return [d[p] + layers.upsample(self.l1(p), k, rows, cols, self.layout["upsampler"]) for p in range(4)]

    def q_tile(self, p, t):
        return self.q_tiles(t)[p]

    def planes(self, mode, prect=None, dither=False, seed=0, rec=None, out=None):
        """Decode plane rectangle [y0,y1) x [x0,x1) of all four planes.

        out: optional four writable (y1-y0, x1-x0) arrays (e.g. strided views of a mosaic).
        """
        rec_mode = rec or self.layout.get("rec", "floor")
        y0, x0, y1, x1 = prect or (0, 0, *self.pshape)
        if out is None:
            out = np.empty((4, y1 - y0, x1 - x0), np.uint16)
        if self.layout["l1"]:
            for p in range(4):
                self.l1(p)

        def tile_job(t):
            ty, tx, ty0, tx0, th, tw = t
            rng = np.random.default_rng([seed, ty, tx])
            qs = self.q_tiles(t)
            sy0, sx0 = max(y0, ty0), max(x0, tx0)
            sy1, sx1 = min(y1, ty0 + th), min(x1, tx0 + tw)
            for p in range(4):
                lo, n, rec_t = self.tables[p]
                q = qs[p]
                if mode == "exact":
                    x = lo[q] + entropy.kappa_decode(self.r.section("L3", p, ty, tx), n[q])
                elif dither:
                    x = lo[q] + rng.integers(0, n[q])
                elif rec_mode == "checker":
                    parity = (np.arange(ty0, ty0 + th)[:, None] + np.arange(tx0, tx0 + tw)[None, :]) & 1
                    x = rec_t[q] + ((n[q] - 1) & 1) * parity
                else:
                    x = rec_t[q]
                out[p][sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = x[sy0 - ty0:sy1 - ty0, sx0 - tx0:sx1 - tx0]

        tiles = layers.tiles_touching(self.pshape, self.layout["tile"], y0, x0, y1, x1)
        with ThreadPoolExecutor(self.threads) as ex:
            list(ex.map(tile_job, tiles))
        return out

    def mosaic(self, mode, region=None, dither=False, seed=0):
        """Sensor mosaic; region = (x, y, w, h) in sensor coordinates."""
        if region is None:
            h, w = self.pshape
            m = np.empty((h * 2, w * 2), np.uint16)
            self.planes(mode, dither=dither, seed=seed, out=[m[dy::2, dx::2] for dy, dx in self.offsets])
            return m
        x, y, w, h = region
        py0, px0 = y // 2, x // 2
        py1, px1 = -(-(y + h) // 2), -(-(x + w) // 2)
        m = io_raw.unpack(self.planes(mode, (py0, px0, py1, px1), dither, seed), self.offsets)
        return m[y - 2 * py0:y - 2 * py0 + h, x - 2 * px0:x - 2 * px0 + w]

    def original(self):
        """The source raw file, byte for byte (needs the SRC and L3 sections)."""
        from . import source as source_mod
        if not self.r.has_original:
            raise ValueError("this file cannot restore its original raw file: "
                             + self.original_unavailable_reason())
        return source_mod.restore(self.r.section("CTR", 0), self.r.section("SRC", 0), self.mosaic("exact"),
                                  self.threads)

    def near_lossless_original(self, dither=False):
        """A valid file in the source raw format holding the near-lossless (signal-mode) pixels."""
        from . import export, source as source_mod
        if not self.r.has_container:
            raise ValueError("this file cannot write its original raw format: " + self.original_unavailable_reason())
        mosaic = self.mosaic("signal", dither=dither)
        render = export.preview_renderer(mosaic, self.r.header)
        return source_mod.write_near_lossless(self.r.section("CTR", 0), mosaic, render, self.threads), mosaic

    def original_unavailable_reason(self):
        orig = self.r.header["source"].get("original")
        if not orig:
            return "it was encoded without the original container (--no-original)"
        if not orig.get("container", orig.get("restorable")):
            return f"the source format is not supported ({orig.get('reason')})"
        if not self.r.has_l3:
            return "the cold layer (L3) is not present"
        return "the original's large previews were not kept"

    def preview_mosaic(self):
        """Low-resolution mosaic from L0 + L1 only, plus a header adjusted to its size,
        for developing a proper-colour thumbnail (export.develop)."""
        planes = np.stack([self.tables[p][2][np.clip(self.l1(p), 0, len(self.tables[p][2]) - 1)]
                           for p in range(4)]).astype(np.uint16)
        hd = {**self.r.header, "sensor": dict(self.r.header["sensor"])}
        k = self.layout["k"]
        if hd["sensor"].get("crop"):
            hd["sensor"]["crop"] = [-(-v // k) if i < 2 else v // k for i, v in enumerate(hd["sensor"]["crop"])]
        return io_raw.unpack(planes, self.offsets), hd

    def preview(self):
        """8-bit RGB thumbnail from L0 + L1 only (float allowed: not an exact path)."""
        hd = self.r.header["sensor"]
        ch = []
        for p in range(4):
            rec = self.tables[p][2]
            v = rec[np.clip(self.l1(p), 0, len(rec) - 1)].astype(np.float64) - hd["black"][p]
            ch.append(np.maximum(v, 0) * (hd["wb"][p] or 1.0))
        rgb = np.stack([ch[0], (ch[1] + ch[2]) / 2, ch[3]], -1)
        if hd["color_matrix"]:
            rgb = rgb @ np.array(hd["color_matrix"]).T
        lum = rgb @ np.array([0.2126, 0.7152, 0.0722])
        rgb *= 0.18 / np.exp(np.mean(np.log(np.maximum(lum, 1e-6))))
        rgb = np.clip(rgb, 0, 1)
        srgb = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * rgb ** (1 / 2.4) - 0.055)
        return (srgb * 255 + 0.5).astype(np.uint8)


def decode(src, mode="signal", region=None, dither=False, seed=0, sidecar=None, threads=None):
    r = container.RangeReader(src, sidecar)
    if mode == "index":
        return r.header
    dec = Decoder(r, threads)
    if mode == "preview":
        return dec.preview()
    return dec.mosaic(mode, region, dither, seed)
