"""Evaluate MiniRaw on one of your own raw files.

    python -m bench.eval PATH/TO/FILE.NEF [--out OUT.mraw] [--render DIR] [--quick]

Prints sizes per layer against the source file and JPEG XL lossless, checks
exact recovery and the error bound, sweeps the quantisation step, and with
--render writes side-by-side PNG crops (original / signal-mode decode) at +3 EV
with render PSNR.
"""
import argparse
import hashlib
import time
import tomllib
from pathlib import Path

import numpy as np

from baselines import b1_native, b2_jxl, b5_sqrt
from miniraw import codec, container, io_raw
from . import metrics

ROOT = Path(__file__).resolve().parent.parent


def camera_name(path):
    try:
        import tifffile
        with tifffile.TiffFile(path) as tf:
            t = tf.pages[0].tags
            make, model = t.get("Make"), t.get("Model")
            if model is not None:
                mk = str(make.value).strip() if make is not None else ""
                md = str(model.value).strip()
                return md if md.startswith(mk) else f"{mk} {md}".strip()
    except Exception:
        pass
    return "unknown camera"


def fmt_row(name, nbytes, npx, ref, note=""):
    if nbytes is None:
        return f"  {name:34s} {'n/a':>9s}"
    return (f"  {name:34s} {nbytes / 1e6:8.2f} MB  {8 * nbytes / npx:6.2f} bpp  "
            f"{nbytes / ref:6.3f}x B2  {note}")


def render(path, mosaic, exp_shift=1.0):
    import rawpy
    with rawpy.imread(str(path)) as r:
        vis = r.raw_image_visible
        vis[:mosaic.shape[0], :mosaic.shape[1]] = mosaic
        return r.postprocess(use_camera_wb=True, exp_shift=exp_shift, no_auto_bright=True,
                             output_bps=8, user_flip=0)


def psnr(a, b):
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return float("inf") if mse == 0 else 10 * np.log10(255 ** 2 / mse)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--config", default=str(ROOT / "configs" / "phase1.toml"))
    ap.add_argument("--step", type=float)
    ap.add_argument("--out", help="write the .mraw file here")
    ap.add_argument("--render", metavar="DIR", help="write +3 EV comparison crops (PNG) here")
    ap.add_argument("--quick", action="store_true", help="skip the step sweep and B5")
    a = ap.parse_args()
    cfg = tomllib.loads(Path(a.config).read_text())
    step = a.step or cfg["step"]
    enc = dict(tile=cfg["tile"], effort=cfg["effort"], upsampler=cfg["upsampler"],
               l1_mode=cfg["l1_mode"], rec=cfg["rec"], packing=cfg.get("packing"),
               l1_min_side=cfg.get("l1_min_side", 256), l2_pred=cfg.get("l2_pred", "cross"))
    b2p = cfg.get("b2_packing", cfg.get("packing", "P1"))
    path = Path(a.path)

    try:
        raw = io_raw.read(path)
    except io_raw.NotBayer as e:
        raise SystemExit(f"{path.name}: not a 2x2 Bayer sensor ({e}); Phase 1 covers Bayer only.")
    h, w = raw.sensor_shape
    npx = h * w
    gaps = io_raw.histogram_gaps(raw.planes, raw.black, raw.white)
    print(f"{path.name}: {camera_name(path)}, {w}x{h} ({npx / 1e6:.1f} MP), {raw.bit_depth}-bit, "
          f"black {raw.black[0]}, white {raw.white}")
    if gaps > io_raw.GAP_THRESHOLD:
        print(f"  WARNING: {gaps:.0%} empty codes in the histogram: this file was tone-curve compressed or "
              "gain-scaled in camera (lossy/compressed raw mode?). MiniRaw's lossless layer is inefficient "
              "on such files; use the camera's lossless mode for a fair test.")

    t = time.time()
    data, info = codec.encode(raw, step=step, source={"file": path.name}, **enc)
    t_enc = time.time() - t
    models = [pi["model"] for pi in info["planes"]]
    r = container.RangeReader(data)
    sz = container.layer_sizes(r.header)
    sz["L0"] = len(data) - sum(sz.values())

    print("\nNoise model per plane (R, G1, G2, B):")
    for name, pi in zip(io_raw.PLANE_NAMES, info["planes"]):
        e = pi["estimated"]
        sig = pi["validate_sigma"]
        print(f"  {name:2s} a={e['a']:.3f} DN/e-, read var c={e['c']:.1f} DN^2; "
              f"flat-block sigma {min(sig, default=0):.2f}-{max(sig, default=0):.2f} -> "
              f"{'affine OK' if pi['validated'] else 'isotonic fallback (non-affine noise, e.g. in-camera NR)'}")

    print("\nSizes:")
    t = time.time()
    b2 = b2_jxl.encode_bytes(raw.planes, cfg["tile"], cfg["effort"], b2p)
    t_b2 = time.time() - t
    b1 = b1_native.raw_bytes(path)
    hot = sz["L0"] + sz["L1"] + sz["L2"]
    print(fmt_row("source file (incl. previews, EXIF)", path.stat().st_size, npx, b2))
    print(fmt_row("source raw data only (B1)", b1, npx, b2))
    print(fmt_row("JPEG XL lossless planes (B2)", b2, npx, b2))
    print(fmt_row("MiniRaw L0 header", sz["L0"], npx, b2))
    print(fmt_row("MiniRaw L1 base / preview", sz["L1"], npx, b2))
    print(fmt_row("MiniRaw L2 signal", sz["L2"], npx, b2))
    print(fmt_row("MiniRaw L3 noise (cold)", sz["L3"], npx, b2))
    print(fmt_row(f"MiniRaw hot tier (L0-L2, step {step})", hot, npx, b2,
                  f"<- {1 - hot / path.stat().st_size:.0%} smaller than the source file"))
    print(fmt_row("MiniRaw total (bit-exact)", len(data), npx, b2))

    print("\nChecks:")
    t = time.time()
    ex = codec.decode(data, "exact")
    t_dec = time.time() - t
    orig = io_raw.unpack(raw.planes, raw.offsets)
    sha_ok = hashlib.sha256(ex.astype("<u2").tobytes()).hexdigest() == r.header["source"]["sha256"]
    print(f"  exact decode bit-identical: {sha_ok}")
    dec = codec.Decoder(container.RangeReader(data))
    sig = dec.planes("signal")
    viol, rms, mx = 0, [], 0.0
    for p in range(4):
        pi = info["planes"][p]
        viol += metrics.bound_violations(raw.planes[p], pi["T"], pi["fwd"], dec.tables[p][2], step,
                                         enc["rec"] == "checker")
        e = metrics.stabilized_errors(raw.planes[p], sig[p], pi["T"])
        rms.append(e[0]); mx = max(mx, e[1])
    print(f"  signal-mode error bound violations: {viol} pixels")
    print(f"  signal-mode error: RMS {np.sqrt(np.mean(np.square(rms))):.3f} sigma, max {mx:.3f} sigma "
          f"(theory RMS {step / np.sqrt(12):.3f})")
    print(f"  time: encode {t_enc:.1f} s, exact decode {t_dec:.1f} s, JPEG XL lossless {t_b2:.1f} s "
          "(single-thread Python prototype)")

    if not a.quick:
        print("\nStep sweep (hot tier):")
        for st in cfg["steps"]:
            d, inf = codec.encode(raw, step=st, with_l3=False, models=models, **enc) if st != step else (data, info)
            s = container.layer_sizes(container.RangeReader(d).header)
            hb = (len(d) - sum(s.values())) + s["L1"] + s["L2"] if st != step else hot
            dd = codec.Decoder(container.RangeReader(d)).planes("signal")
            e = [metrics.stabilized_errors(raw.planes[p], dd[p], inf["planes"][p]["T"]) for p in range(4)]
            print(f"  step {st:3.1f}: {hb / 1e6:7.2f} MB  {8 * hb / npx:5.2f} bpp  {hb / b2:5.3f}x B2   "
                  f"RMS {np.sqrt(np.mean([x[0] ** 2 for x in e])):.3f} sigma, max {max(x[1] for x in e):.2f} sigma")
        print("\nB5, uncalibrated sqrt curve + JPEG XL (what in-camera lossy raw modes do):")
        for bits in cfg["b5_bits"]:
            if bits >= raw.bit_depth:
                continue
            nb, dd = b5_sqrt.run(raw, bits, cfg["tile"], cfg["effort"], b2p)
            e = [metrics.stabilized_errors(raw.planes[p], dd[p], info["planes"][p]["T"]) for p in range(4)]
            print(f"  {bits:2d} bits: {nb / 1e6:7.2f} MB  {8 * nb / npx:5.2f} bpp  {nb / b2:5.3f}x B2   "
                  f"RMS {np.sqrt(np.mean([x[0] ** 2 for x in e])):.3f} sigma, max {max(x[1] for x in e):.2f} sigma")

    if a.out:
        Path(a.out).write_bytes(data)
        print(f"\nWrote {a.out}")
    if a.render:
        import imagecodecs
        out = Path(a.render)
        out.mkdir(parents=True, exist_ok=True)
        sm = codec.decode(data, "signal")
        ref, got = render(path, orig, 8.0), render(path, sm, 8.0)
        ref0, got0 = render(path, orig, 1.0), render(path, sm, 1.0)
        print(f"\nRender PSNR (rawpy, camera WB): default {psnr(ref0, got0):.2f} dB, +3 EV {psnr(ref, got):.2f} dB")
        hh, ww = ref.shape[:2]
        # three 512x512 crops: centre, darkest and most detailed (by local luma std)
        lum = ref0.astype(np.float64).mean(-1)
        cs = 512
        cand = [(y, x) for y in range(0, hh - cs, cs // 2) for x in range(0, ww - cs, cs // 2)]
        dark = min(cand, key=lambda c: lum[c[0]:c[0] + cs, c[1]:c[1] + cs].mean())
        busy = max(cand, key=lambda c: lum[c[0]:c[0] + cs, c[1]:c[1] + cs].std())
        for name, (y, x) in {"centre": ((hh - cs) // 2, (ww - cs) // 2), "darkest": dark, "detail": busy}.items():
            for tag, (A, B) in {"default": (ref0, got0), "plus3ev": (ref, got)}.items():
                ca, cb = A[y:y + cs, x:x + cs], B[y:y + cs, x:x + cs]
                diff = np.clip(np.abs(ca.astype(int) - cb.astype(int)) * 8, 0, 255).astype(np.uint8)
                sheet = np.concatenate([ca, cb, diff], 1)
                (out / f"{path.stem}_{name}_{tag}.png").write_bytes(imagecodecs.png_encode(sheet))
        print(f"Wrote crops to {out}/ (each: original | MiniRaw signal mode | |difference| x8)")


if __name__ == "__main__":
    main()
