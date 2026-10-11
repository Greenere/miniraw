"""Does MiniRaw's noise-calibrated quantization help on JPEG/PNG inputs?

Compares, per image:
  - JPEG XL lossless JPEG transcoding (byte-exact JPEG restore)
  - JPEG XL lossless on the decoded pixels (pixel-exact)
  - MiniRaw-style: per-channel noise fit + VST quantization at step 1, then JPEG XL lossless
and for PNG: PNG vs JPEG XL lossless on the same pixels.

    python -m bench.experiments.jpeg_png DIR_OF_JPEGS
"""
import sys
from pathlib import Path

import imagecodecs
import numpy as np

from miniraw import codec, vst


def miniraw_style(rgb):
    total, fallbacks, qs = 0, 0, []
    for c in range(3):
        ch = rgb[..., c].astype(np.uint16)
        model, T, info = codec.fit_noise(ch, 0, 255, diagnostics=False)
        fallbacks += model["type"] == "isotonic"
        fwd = vst.forward_table(T, 1.0, 255)
        qs.append(fwd[ch])
    q = np.stack(qs, -1).astype(np.uint16)
    return len(imagecodecs.jpegxl_encode(q, lossless=True, effort=7)), fallbacks


def main(folder):
    rows = []
    for p in sorted(Path(folder).glob("*.jpg")):
        jpg = p.read_bytes()
        jxl = imagecodecs.jpegxl_encode_jpeg(jpg)
        exact = bytes(imagecodecs.jpegxl_decode_jpeg(jxl)) == jpg
        rgb = imagecodecs.jpeg8_decode(jpg)
        px = len(imagecodecs.jpegxl_encode(rgb, lossless=True, effort=7))
        png = len(imagecodecs.png_encode(rgb, level=9))
        try:
            mr, fb = miniraw_style(rgb)
        except ValueError:
            mr, fb = float("nan"), -1
        rows.append((p.name, rgb.shape, len(jpg), len(jxl), exact, px, mr, fb, png))
    print(f"{'file':28s} {'size':>11s} {'JPEG':>8s} {'JXL-repack':>11s} {'exact':>5s} {'JXL pixels':>11s} "
          f"{'MiniRaw-style':>13s} {'PNG':>9s} {'PNG->JXL':>9s}")
    for n, sh, j, x, e, px, mr, fb, png in rows:
        print(f"{n:28s} {sh[1]:5d}x{sh[0]:<5d} {j / 1e3:7.0f}K {x / j:10.3f}x {str(e):>5s} {px / j:10.3f}x "
              f"{mr / j:12.3f}x {png / 1e3:8.0f}K {px / png:8.3f}x")
    r = np.array([[x / j, px / j, mr / j, px / png] for _, _, j, x, _, px, mr, _, png in rows])
    print(f"{'median':28s} {'':11s} {'':8s} {np.median(r[:, 0]):10.3f}x {'':5s} {np.median(r[:, 1]):10.3f}x "
          f"{np.nanmedian(r[:, 2]):12.3f}x {'':9s} {np.median(r[:, 3]):8.3f}x")
    print(f"byte-exact JPEG restore: {sum(r[4] for r in rows)}/{len(rows)}")


if __name__ == "__main__":
    main(sys.argv[1])
