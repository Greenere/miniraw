"""Can a non-redundant base layer be a good search index? (results/index_benchmark.md, follow-up)

    python -m bench.experiments.l1_index sizes [--split dev]
    python -m bench.experiments.l1_index quality [--split index500] [--limit N] CONFIG...

sizes    For every image: L1 and per-plane L2 bytes with no L1, and with L1 at k0 and k0/2 ("128" and "256" px),
         stored at step 1, 4 and 16, with G1 predicted from L1 or coded directly. G3 = (L1 + L2) / L2 without L1.
quality  Renders L1 for each CONFIG (e.g. 256:4) at half size, embeds it with both models and reports neighbour
         overlap@10 and median cosine against the cached full renders of the index benchmark.
"""
import argparse
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

from bench.datasets import camera_of, path_for, read_split
from miniraw import codec, container, export, io_raw, layers, vst

ROOT = Path(__file__).resolve().parents[2]
EFFORT = 7
VARIANTS = [("none", 0, 1, "l1")] + [(size, None, s, g) for size in ("128", "256") for s in (1, 4, 16)
                                       for g in ("l1", "none")]


def sizes_one(key):
    raw = io_raw.read(path_for(key))
    models = [codec.fit_noise(raw.planes[p], raw.black[p], raw.white, None, p, False)[0] for p in range(4)]
    k0 = layers.choose_k(raw.planes.shape[1:], 128)
    rows = []
    for size, _, step, g1 in VARIANTS:
        kw = dict(use_l1=False) if size == "none" else dict(k=k0 if size == "128" else k0 // 2, l1_step=step,
                                                            g1_pred=g1)
        data, _ = codec.encode(raw, step=1.0, effort=EFFORT, with_l3=False, models=models, threads=2, **kw)
        hd = container.RangeReader(data).header
        l2 = [0] * 4
        l1 = 0
        for layer, plane, _, _, _, length, _ in hd["sections"]:
            if layer == "L2":
                l2[plane] += length
            elif layer == "L1":
                l1 += length
        rows.append({"key": key, "camera": camera_of(key), "size": size, "l1_step": step, "g1_pred": g1,
                     "linear": io_raw.histogram_gaps(raw.planes, raw.black, raw.white) <= io_raw.GAP_THRESHOLD,
                     "L1": l1, "L2": sum(l2), **{f"L2_{n}": v for n, v in zip(io_raw.PLANE_NAMES, l2)}})
    return rows


def sizes(split):
    with Pool(5) as pool:
        df = pd.DataFrame([r for rs in pool.map(sizes_one, read_split(split)) for r in rs])
    base = df[df["size"] == "none"].set_index("key")
    for c in ["L2"] + [f"L2_{n}" for n in io_raw.PLANE_NAMES]:
        df[f"{c}_vs_none"] = df[c] / df.key.map(base[c]) - 1
    df["G3"] = (df.L1 + df.L2) / df.key.map(base.L2)
    out = ROOT / "results" / "experiments" / f"l1_index_sizes_{split}.csv"
    df.to_csv(out, index=False)
    lin = df[df.linear]
    print(f"{lin.key.nunique()} linear images -> {out}")
    print(lin.groupby(["size", "l1_step", "g1_pred"]).agg(
        L1_KB=("L1", lambda v: np.median(v) / 1e3), G3=("G3", "median"),
        L2=("L2_vs_none", "median"), R=("L2_R_vs_none", "median"), G1=("L2_G1_vs_none", "median"),
        G2=("L2_G2_vs_none", "median"), B=("L2_B_vs_none", "median")).round(4).to_string())


def quality_one(args):
    key, split, configs = args
    from bench.experiments.index_eval import cache_dir, save_png
    out = cache_dir(split) / key
    raw = io_raw.read(path_for(key))
    k0 = layers.choose_k(raw.planes.shape[1:], 128)
    hd = {"sensor": {"width": raw.sensor_shape[1], "height": raw.sensor_shape[0],
                     "cfa": [list(o) for o in raw.offsets], "black": list(raw.black), "white": raw.white,
                     "wb": raw.wb, "color_matrix": raw.color_matrix, "bit_depth": raw.bit_depth,
                     "cam_xyz": raw.cam_xyz, "flip": raw.flip, "crop": None}, "source": {"exif": raw.exif}}
    tabs = []
    for p in range(4):
        _, T, _ = codec.fit_noise(raw.planes[p], raw.black[p], raw.white, None, p, False)
        fwd = vst.forward_table(T, 1.0, raw.white)
        _, rec = vst.tables_from_lo(vst.lo_from_forward(fwd), len(fwd) - 1)
        tabs.append((fwd, rec))
    for cfg in configs:
        size, step = cfg.split(":")
        k, step = (k0 if size == "128" else k0 // 2), int(step)
        planes = []
        for p, (fwd, rec) in enumerate(tabs):
            l1 = layers.l1_dequantize(layers.l1_from_q(fwd[raw.planes[p]].astype(np.int64), k) // step, step)
            planes.append(rec[np.clip(l1, 0, len(rec) - 1)])
        mos = io_raw.unpack(np.stack(planes).astype(np.uint16), raw.offsets)
        save_png(out / f"L1x-{size}-s{step}.png", export.develop(mos, hd, half_size=True))
    return key


def quality(split, limit, configs):
    import torch
    from PIL import Image
    from bench.experiments.index_eval import MODELS, cache_dir, load_model, overlap_at
    keys = [k for k in read_split(split) if (cache_dir(split) / k / "R.png").exists()][:limit]
    with Pool(6) as pool:
        list(pool.imap_unordered(quality_one, [(k, split, configs) for k in keys]))
    rows = []
    for name in MODELS:
        model, pre, _ = load_model(name)
        z = np.load(cache_dir(split) / f"emb_{name}.npz")
        idx = {k: i for i, k in enumerate(z["keys"])}
        sel = [idx[k] for k in keys]
        R = z["R"][sel]
        with torch.no_grad():
            for cfg in configs:
                size, step = cfg.split(":")
                feats = []
                for i in range(0, len(keys), 32):
                    b = torch.stack([pre(Image.open(cache_dir(split) / k / f"L1x-{size}-s{step}.png").convert("RGB"))
                                     for k in keys[i:i + 32]])
                    e = model.encode_image(b).float()
                    feats.append((e / e.norm(dim=-1, keepdim=True)).numpy())
                X = np.concatenate(feats)
                rows.append({"model": name, "config": cfg, "n": len(keys), "cos_median": float(np.median((X * R).sum(1))),
                             "overlap@10": float(overlap_at(X, R, 10).mean())})
        for ref in ("L1-128-half", "L1-256-half"):
            X = z[ref][sel]
            rows.append({"model": name, "config": f"{ref} (step 1, cached)", "n": len(keys),
                         "cos_median": float(np.median((X * R).sum(1))), "overlap@10": float(overlap_at(X, R, 10).mean())})
    df = pd.DataFrame(rows)
    out = ROOT / "results" / "experiments" / f"l1_index_quality_{split}.csv"
    df.to_csv(out, index=False)
    print(df.round(3).to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["sizes", "quality"])
    ap.add_argument("configs", nargs="*")
    ap.add_argument("--split")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    if a.stage == "sizes":
        sizes(a.split or "dev")
    else:
        quality(a.split or "index500", a.limit, a.configs)


if __name__ == "__main__":
    sys.exit(main())
