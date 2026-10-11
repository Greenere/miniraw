"""Exactly decodable L1 quantized perceptually (8- or 6-bit gamma 1/2.4 per plane, JPEG XL lossless): bytes and
neighbour overlap on the first 200 cached index500 images. Follow-up to bench/experiments/l1_index.py.

    python -m bench.experiments.l1_index_gamma
"""
import sys
from multiprocessing import Pool
import numpy as np, pandas as pd, torch
from PIL import Image
from miniraw import codec, entropy, export, io_raw, layers, vst
from bench.datasets import path_for, read_split
from bench.experiments.index_eval import MODELS, cache_dir, load_model, overlap_at, save_png

def one(key):
    raw = io_raw.read(path_for(key)); out = {}
    k0 = layers.choose_k(raw.planes.shape[1:], 128)
    hd = {"sensor": {"width": raw.sensor_shape[1], "height": raw.sensor_shape[0], "cfa": [list(o) for o in raw.offsets],
                     "black": list(raw.black), "white": raw.white, "wb": raw.wb, "color_matrix": raw.color_matrix,
                     "bit_depth": raw.bit_depth, "cam_xyz": raw.cam_xyz, "flip": raw.flip, "crop": None},
          "source": {"exif": raw.exif}}
    for size, k in (("256", k0 // 2), ("128", k0)):
        for bits in (8, 6):
            planes, nb = [], 0
            for p in range(4):
                _, T, _ = codec.fit_noise(raw.planes[p], raw.black[p], raw.white, None, p, False)
                fwd = vst.forward_table(T, 1.0, raw.white); _, rec = vst.tables_from_lo(vst.lo_from_forward(fwd), len(fwd) - 1)
                lin = rec[np.clip(layers.l1_from_q(fwd[raw.planes[p]].astype(np.int64), k), 0, len(rec) - 1)].astype(float)
                full = raw.white - raw.black[p]; levels = 2 ** bits - 1
                g = np.round(levels * np.clip((lin - raw.black[p]) / full, 0, 1) ** (1 / 2.4)).astype(np.uint8)
                nb += len(entropy.jxl_encode(g, 7))
                planes.append(raw.black[p] + full * (g / levels) ** 2.4)
            mos = io_raw.unpack(np.stack(planes).round().astype(np.uint16), raw.offsets)
            save_png(cache_dir("index500") / key / f"L1g-{size}-{bits}.png", export.develop(mos, hd, half_size=True))
            out[f"{size}-{bits}"] = nb
    return key, out

if __name__ == "__main__":
    keys = [k for k in read_split("index500") if (cache_dir("index500") / k / "R.png").exists()][:200]
    with Pool(6) as pool:
        res = dict(pool.map(one, keys))
    cfgs = list(next(iter(res.values())))
    print("median bytes:", {c: f"{np.median([res[k][c] for k in keys]) / 1e3:.0f} KB" for c in cfgs})
    for name in MODELS:
        model, pre, _ = load_model(name); z = np.load(cache_dir("index500") / f"emb_{name}.npz")
        idx = {k: i for i, k in enumerate(z["keys"])}; R = z["R"][[idx[k] for k in keys]]
        with torch.no_grad():
            for c in cfgs:
                X = np.concatenate([(lambda e: (e / e.norm(dim=-1, keepdim=True)).numpy())(model.encode_image(torch.stack(
                    [pre(Image.open(cache_dir("index500") / k / f"L1g-{c}.png").convert("RGB")) for k in keys[i:i + 32]])).float())
                    for i in range(0, len(keys), 32)])
                print(name, c, "overlap@10 %.3f" % overlap_at(X, R, 10).mean(), "cos %.3f" % np.median((X * R).sum(1)))
