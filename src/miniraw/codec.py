"""encode() / decode() public API.

Decoding uses only integer arithmetic and tables stored in the file (spec 4).
"""
import hashlib

import numpy as np

from . import container, entropy, io_raw, layers, noise, vst

DEFAULTS = dict(step=1.0, tile=512, effort=7, use_l1=True, upsampler="bilinear", with_l3=True)


def mosaic_sha256(planes, offsets):
    return hashlib.sha256(io_raw.unpack(planes, offsets).astype("<u2").tobytes()).hexdigest()


def fit_noise(plane, b, white, noise_profile=None, plane_index=0):
    """Noise model for one plane, with validation and isotonic fallback (spec 4.2)."""
    xmax = int(max(white, int(plane.max())))
    model, (xs, vs, ws) = noise.estimate(plane, b, white)
    info = {"estimated": dict(model)}
    if noise_profile:
        npm = noise.from_noise_profile(noise_profile, plane_index, b, white)
        info["noise_profile_disagreement"] = noise.disagreement(npm, model, white)
        model = npm
    T = vst.T_table(model, xmax)
    _, sig, _ = noise.stabilized_sigma_by_bin(plane, model, white, T)
    info["validate_sigma"] = sig.tolist()
    ok = len(sig) > 0 and bool(np.all((sig >= 0.9) & (sig <= 1.1)))
    info["validated"] = ok
    if not ok:
        model = noise.isotonic_model(xs, vs, ws, b, xmax)
        T = vst.T_table(model, xmax)
        _, sig2, _ = noise.stabilized_sigma_by_bin(plane, model, white, T)
        info["validate_sigma_isotonic"] = sig2.tolist()
    info["model"] = model
    return model, T, info


def encode(raw, step=1.0, tile=512, effort=7, use_l1=True, upsampler="bilinear",
           with_l3=True, models=None, source=None, l1_mode="mean", rec="floor", k=None):
    """Encode a RawImage. `models` (per-plane noise model dicts) skips estimation.

    l1_mode "mean" is construction A (floor-mean L1, L2 codes q - up(L1));
    "wavelet" is construction B (per-tile 5/3 lifting, L1 = LL bands, L2 = detail bands).
    rec selects the signal-mode reconstruction: "floor" is spec 4.3's lo + (n-1)//2;
    "checker" alternates floor/ceil of the bin centre on a pixel checkerboard (deviation D17).

    Returns (file bytes, info dict). info holds per-plane T tables and q planes
    for the benchmark's metrics; they are not part of the file.
    """
    planes = raw.planes
    pshape = planes.shape[1:]
    rec_mode = rec
    k = (k or layers.choose_k(pshape)) if use_l1 else 0
    levels = k.bit_length() - 1 if use_l1 else 0
    if use_l1 and l1_mode == "wavelet" and tile is not None:
        assert tile % k == 0
    sections, plane_hdr, info = [], [], {"planes": []}
    for p in range(4):
        x = planes[p]
        b, white = raw.black[p], raw.white
        xmax = int(max(white, int(x.max())))
        if models is None:
            model, T, pinfo = fit_noise(x, b, white, raw.noise_profile, p)
        else:
            model, pinfo = models[p], {"model": models[p], "validated": None}
            T = vst.T_table(model, xmax)
        fwd = vst.forward_table(T, step, white)
        lo = vst.lo_from_forward(fwd)
        n, rec = vst.tables_from_lo(lo, xmax)
        q = fwd[x]
        deltas = vst.encode_lo(lo)
        assert deltas.max(initial=0) < 65536
        plane_hdr.append({"xmax": xmax, "lo": deltas.astype("<u2").tobytes(), "model": model})
        pinfo.update(T=T, fwd=fwd, q=q, nq=len(lo))
        info["planes"].append(pinfo)

        grid = layers.tile_grid(pshape, tile)
        if use_l1 and l1_mode == "mean":
            l1 = layers.l1_from_q(q, k)
        elif use_l1:
            l1 = np.zeros((-(-pshape[0] // k), -(-pshape[1] // k)), np.int64)
            coeffs = {}
            for ty, tx, y0, x0, th, tw in grid:
                c, (lh, lw) = layers.dwt53(q[y0:y0 + th, x0:x0 + tw], levels)
                l1[y0 // k:y0 // k + lh, x0 // k:x0 // k + lw] = c[:lh, :lw]
                c[:lh, :lw] = 0
                coeffs[ty, tx] = c
        if use_l1:
            sections.append((("L1", p, 0, 0), entropy.jxl_encode(l1, effort)))
        for ty, tx, y0, x0, th, tw in grid:
            qt = q[y0:y0 + th, x0:x0 + tw]
            if not use_l1:
                payload = qt
            elif l1_mode == "mean":
                up = layers.upsample(l1, k, np.arange(y0, y0 + th), np.arange(x0, x0 + tw), upsampler)
                payload = qt - up
            else:
                payload = coeffs[ty, tx]
            sections.append((("L2", p, ty, tx), entropy.jxl_encode(payload, effort)))
            if with_l3:
                kappa = x[y0:y0 + th, x0:x0 + tw].astype(np.int64) - lo[qt]
                sections.append((("L3", p, ty, tx), entropy.kappa_encode(kappa, n[qt])))

    h, w = raw.sensor_shape
    header = {
        "sensor": {"width": w, "height": h, "cfa": [list(o) for o in raw.offsets],
                   "black": list(raw.black), "white": raw.white, "wb": raw.wb,
                   "color_matrix": raw.color_matrix, "bit_depth": raw.bit_depth},
        "source": {**(source or {}), "sha256": mosaic_sha256(planes, raw.offsets)},
        "noise": {"step": step, "planes": [ph["model"] for ph in plane_hdr]},
        "tables": [{"xmax": ph["xmax"], "lo": ph["lo"]} for ph in plane_hdr],
        "layout": {"tile": tile, "k": k, "upsampler": upsampler, "packing": "P1",
                   "plane_shape": list(pshape), "l1": use_l1,
                   "l1_mode": l1_mode, "rec": rec_mode},
        "embed": None,
    }
    return container.write(header, sections), info


class Decoder:
    def __init__(self, reader):
        self.r = reader
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

    def l1(self, p):
        if p not in self._l1:
            self._l1[p] = entropy.jxl_decode(self.r.section("L1", p))
        return self._l1[p]

    def q_tile(self, p, t):
        ty, tx, y0, x0, th, tw = t
        d = entropy.jxl_decode(self.r.section("L2", p, ty, tx))
        if self.layout["l1"] and self.layout.get("l1_mode") == "wavelet":
            k = self.layout["k"]
            levels = k.bit_length() - 1
            lh, lw = -(-th // k), -(-tw // k)
            d[:lh, :lw] = self.l1(p)[y0 // k:y0 // k + lh, x0 // k:x0 // k + lw]
            return layers.idwt53(d, levels)
        if self.layout["l1"]:
            d = d + layers.upsample(self.l1(p), self.layout["k"], np.arange(y0, y0 + th),
                                    np.arange(x0, x0 + tw), self.layout["upsampler"])
        return d

    def planes(self, mode, prect=None, dither=False, seed=0, rec=None):
        """Decode plane rectangle [y0,y1) x [x0,x1) of all four planes."""
        rec_mode = rec or self.layout.get("rec", "floor")
        y0, x0, y1, x1 = prect or (0, 0, *self.pshape)
        out = np.empty((4, y1 - y0, x1 - x0), np.uint16)
        rng = np.random.default_rng(seed)
        for p in range(4):
            lo, n, rec = self.tables[p]
            for t in layers.tiles_touching(self.pshape, self.layout["tile"], y0, x0, y1, x1):
                ty, tx, ty0, tx0, th, tw = t
                q = self.q_tile(p, t)
                if mode == "exact":
                    x = lo[q] + entropy.kappa_decode(self.r.section("L3", p, ty, tx), n[q])
                elif dither:
                    x = lo[q] + rng.integers(0, n[q])
                elif rec_mode == "checker":
                    parity = (np.arange(ty0, ty0 + th)[:, None] + np.arange(tx0, tx0 + tw)[None, :]) & 1
                    x = rec[q] + ((n[q] - 1) & 1) * parity
                else:
                    x = rec[q]
                sy0, sx0 = max(y0, ty0), max(x0, tx0)
                sy1, sx1 = min(y1, ty0 + th), min(x1, tx0 + tw)
                out[p, sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = \
                    x[sy0 - ty0:sy1 - ty0, sx0 - tx0:sx1 - tx0]
        return out

    def mosaic(self, mode, region=None, dither=False, seed=0):
        """Sensor mosaic; region = (x, y, w, h) in sensor coordinates."""
        if region is None:
            return io_raw.unpack(self.planes(mode, dither=dither, seed=seed), self.offsets)
        x, y, w, h = region
        py0, px0 = y // 2, x // 2
        py1, px1 = -(-(y + h) // 2), -(-(x + w) // 2)
        m = io_raw.unpack(self.planes(mode, (py0, px0, py1, px1), dither, seed), self.offsets)
        return m[y - 2 * py0:y - 2 * py0 + h, x - 2 * px0:x - 2 * px0 + w]

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


def decode(src, mode="signal", region=None, dither=False, seed=0, sidecar=None):
    r = container.RangeReader(src, sidecar)
    dec = Decoder(r)
    if mode == "index":
        return r.header
    if mode == "preview":
        return dec.preview()
    return dec.mosaic(mode, region, dither, seed)
