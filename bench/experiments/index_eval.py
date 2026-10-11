"""Index benchmark (docs/INDEX_BENCHMARK_PLAN.md): is MiniRaw's base layer (L1) a good search index?

    python -m bench.experiments.index_eval prepare [--split index500] [--limit N]
    python -m bench.experiments.index_eval embed
    python -m bench.experiments.index_eval metrics

prepare  renders every arm to results/cache/index/<split>/<key>/<arm>.png and writes per-image facts
         (sizes, bytes read, noise parameters) to results/experiments/index_<split>_images.csv.
embed    embeds every arm with each model into results/cache/index/<split>/emb_<model>.npz.
metrics  writes results/experiments/index_<split>_metrics.csv and prints the report tables.

Arms (all oriented for display, 8-bit sRGB):
  R          full render with LibRaw (camera white balance, half_size: >= 1000 px, far above the models' input)
  R-small    R resized to L1-128's demosaicked size (control: size without the pipeline difference)
  R-plane    R resized to L1-128's per-plane size, its real colour resolution (control)
  E          the thumbnail LibRaw extracts from the original file (what photo apps index)
  L1-k       L1 at k = k0/2, k0, 2*k0, as a mosaic developed by the same LibRaw call as R
  L1-k-half  the same mosaic developed at half size: one RGB pixel per 2x2 cell, no demosaic interpolation
  L1-spec    L1 at k0 through the spec 4.7 numpy render (Decoder.preview)
"""
import argparse
import io
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

from bench.datasets import camera_of, load_meta, path_for, read_split
from miniraw import codec, container, entropy, export, io_raw, layers, vst

ROOT = Path(__file__).resolve().parents[2]
L1_EFFORT = 7                     # the benchmark configuration's effort (configs/phase1.toml)
MODELS = {"clip-b32": ("ViT-B-32", "laion2b_s34b_b79k"), "siglip-b16-256": ("ViT-B-16-SigLIP-256", "webli")}
AXES = {
    "location": {"indoor": ["a photo taken indoors", "an indoor scene"],
                 "outdoor": ["a photo taken outdoors", "an outdoor scene"]},
    "time": {"day": ["a photo taken during the day", "a daytime scene"],
             "dusk": ["a photo taken at dusk", "a scene at sunset or twilight"],
             "night": ["a photo taken at night", "a night scene"]},
    "light": {"sun_sky": ["a photo lit by sunlight", "a scene in natural daylight"],
              "artificial": ["a photo lit by artificial light", "a scene under lamps or indoor lighting"],
              "mixed": ["a photo lit by both daylight and artificial light",
                        "a scene with mixed natural and artificial lighting"]},
    "subject": {"people": ["a photo of people", "a portrait of a person"],
                "nature": ["a photo of nature", "a landscape photo"],
                "man_made": ["a photo of buildings or man-made objects", "a photo of architecture"],
                "animals": ["a photo of animals", "a photo of an animal"],
                "abstract": ["an abstract photo", "an abstract pattern"]},
}


def cache_dir(split):
    return ROOT / "results" / "cache" / "index" / split


def orient(img, flip):
    """LibRaw flip code to display orientation: 3 = 180, 5 = 90 CCW, 6 = 90 CW."""
    return np.ascontiguousarray({0: img, 3: np.rot90(img, 2), 5: np.rot90(img, 1), 6: np.rot90(img, -1)}
                                .get(flip, img))


def save_png(path, img):
    import imagecodecs
    path.write_bytes(imagecodecs.png_encode(np.ascontiguousarray(img, np.uint8)))


def l1_mosaic(raw, k):
    """The L1 mosaic MiniRaw would store at block size k (rec of the floor-mean of q), and L1's coded bytes."""
    planes, nbytes = [], 0
    for p in range(4):
        x = raw.planes[p]
        _, T, _ = codec.fit_noise(x, raw.black[p], raw.white, raw.noise_profile, p, False)
        fwd = vst.forward_table(T, 1.0, raw.white)
        _, rec = vst.tables_from_lo(vst.lo_from_forward(fwd), len(fwd) - 1)
        l1 = layers.l1_from_q(fwd[x].astype(np.int64), k)
        nbytes += len(entropy.jxl_encode(l1, L1_EFFORT))
        planes.append(rec[np.clip(l1, 0, len(rec) - 1)])
    return io_raw.unpack(np.stack(planes).astype(np.uint16), raw.offsets), nbytes


def prepare_one(args):
    key, split = args
    out = cache_dir(split) / key
    facts = out / "facts.json"
    if facts.exists():
        return json.loads(facts.read_text())
    import rawpy
    out.mkdir(parents=True, exist_ok=True)
    path = path_for(key)
    meta = load_meta()[key]
    row = {"key": key, "camera": camera_of(key), **{f"cat_{a}": meta["categories"].get(a) for a in AXES}}
    try:
        raw = io_raw.read(path)
    except io_raw.NotBayer as e:
        row["status"] = f"skipped: {e}"
        facts.write_text(json.dumps(row))
        return row
    row.update(status="ok", flip=raw.flip, iso=raw.exif.get("ISO"), file_bytes=path.stat().st_size,
               lossy_source=bool(io_raw.histogram_gaps(raw.planes, raw.black, raw.white) > io_raw.GAP_THRESHOLD))

    # R: the reference render
    with rawpy.imread(str(path)) as r:
        ref = r.postprocess(use_camera_wb=True, half_size=True, output_bps=8)
        try:
            th = r.extract_thumb()
            if th.format == rawpy.ThumbFormat.JPEG:
                import imagecodecs
                thumb, row["E_format"], row["E_bytes"] = imagecodecs.jpeg8_decode(th.data), "jpeg", len(th.data)
            else:
                thumb, row["E_format"] = np.asarray(th.data), "bitmap"
                row["E_bytes"] = int(thumb.nbytes)
            thumb = orient(thumb[..., :3], raw.flip)
        except Exception as e:          # no thumbnail: E is missing for this file
            thumb, row["E_format"] = None, f"none: {e}"
    if thumb is not None:
        save_png(out / "E.png", thumb)
        row["E_h"], row["E_w"] = thumb.shape[:2]

    # L1 arms. k0 is the format default; the real encoder's L1 is checked against the direct one at k0.
    k0 = layers.choose_k(raw.planes.shape[1:], 128)
    data, _ = codec.encode(raw, step=1.0, effort=4, with_l3=False, keep_original=False, threads=2)
    reader = container.RangeReader(data)
    row["L0_read_bytes"] = reader.bytes_read()
    dec = codec.Decoder(reader, threads=2)
    mos0, hd = dec.preview_mosaic()
    row["L0L1_read_bytes"] = reader.bytes_read()
    row["mraw_hot_bytes"] = len(data)
    spec = orient(dec.preview(), raw.flip)
    save_png(out / "L1-spec.png", spec)
    crop = raw.crop
    for name, k in (("L1-256", k0 // 2), ("L1-128", k0), ("L1-64", k0 * 2)):
        mos, nbytes = l1_mosaic(raw, k)
        if k == k0:
            row["L1_matches_encoder"] = bool(np.array_equal(mos, mos0))
        h = {**hd, "sensor": {**hd["sensor"],
                              "crop": [-(-v // k) if i < 2 else v // k for i, v in enumerate(crop)] if crop else None}}
        img = export.develop(mos, h)
        save_png(out / f"{name}.png", img)
        row[f"{name}_k"], row[f"{name}_bytes"], row[f"{name}_h"], row[f"{name}_w"] = k, nbytes, *img.shape[:2]
        half = export.develop(mos, h, half_size=True)    # one RGB pixel per 2x2 cell: no demosaic interpolation
        save_png(out / f"{name}-half.png", half)
        row[f"{name}-half_h"], row[f"{name}-half_w"] = half.shape[:2]
    small = export.resize(ref, row["L1-128_h"], row["L1-128_w"])
    save_png(out / "R-small.png", small)
    save_png(out / "R-plane.png", export.resize(ref, row["L1-128-half_h"], row["L1-128-half_w"]))
    big = export.resize(ref, *(np.array(ref.shape[:2]) * 448 // min(ref.shape[:2])))
    save_png(out / "R.png", big)
    row["R_h"], row["R_w"] = ref.shape[:2]
    # header-only facts for technical search (H4)
    models = reader.header["noise"]["planes"]
    row["noise_a"] = float(np.mean([m.get("a", np.nan) for m in models if m["type"] == "affine"] or [np.nan]))
    row["mean_level"] = float(np.mean([np.mean(mos0[dy::2, dx::2]) - raw.black[i] for i, (dy, dx) in
                                        enumerate(raw.offsets)]) / (raw.white - np.mean(raw.black)))
    facts.write_text(json.dumps(row))
    return row


ARMS = ["R", "R-small", "R-plane", "E", "L1-256", "L1-128", "L1-64", "L1-256-half", "L1-128-half", "L1-64-half",
        "L1-spec"]


def prepare(split, limit=None, workers=6):
    keys = read_split(split)[:limit]
    t0 = time.time()
    with Pool(workers) as pool:
        rows = []
        for i, r in enumerate(pool.imap_unordered(prepare_one, [(k, split) for k in keys])):
            rows.append(r)
            if (i + 1) % 25 == 0:
                print(f"{i + 1}/{len(keys)} prepared, {time.time() - t0:.0f} s", flush=True)
    df = pd.DataFrame(rows).sort_values("key")
    out = ROOT / "results" / "experiments" / f"index_{split}_images.csv"
    df.to_csv(out, index=False)
    print(f"wrote {out}: {int((df.status == 'ok').sum())} ok of {len(df)}; "
          f"L1 at k0 matches the encoder on {int(df.get('L1_matches_encoder', pd.Series(dtype=bool)).sum())}")


def load_model(name):
    import open_clip
    import torch
    arch, tag = MODELS[name]
    model, _, pre = open_clip.create_model_and_transforms(arch, pretrained=tag)
    model.eval()
    torch.set_num_threads(8)
    return model, pre, open_clip.get_tokenizer(arch)


def embed(split):
    import torch
    from PIL import Image
    df = pd.read_csv(ROOT / "results" / "experiments" / f"index_{split}_images.csv")
    keys = df[df.status == "ok"].key.tolist()
    for name in MODELS:
        dst = cache_dir(split) / f"emb_{name}.npz"
        if dst.exists():
            continue
        model, pre, tok = load_model(name)
        out = {}
        t0 = time.time()
        with torch.no_grad():
            for arm in ARMS:
                vecs = np.full((len(keys), 0), np.nan)
                feats = []
                for i in range(0, len(keys), 32):
                    batch = []
                    for k in keys[i:i + 32]:
                        f = cache_dir(split) / k / f"{arm}.png"
                        batch.append(pre(Image.open(f).convert("RGB")) if f.exists() else None)
                    ok = [b for b in batch if b is not None]
                    e = model.encode_image(torch.stack(ok)).float() if ok else torch.zeros(0)
                    e = (e / e.norm(dim=-1, keepdim=True)).numpy() if ok else e
                    it = iter(e)
                    feats += [next(it) if b is not None else None for b in batch]
                dim = next(f for f in feats if f is not None).shape[0]
                vecs = np.stack([f if f is not None else np.full(dim, np.nan) for f in feats])
                out[arm] = vecs.astype(np.float32)
                print(f"{name} {arm}: {time.time() - t0:.0f} s", flush=True)
            for axis, labels in AXES.items():
                for label, prompts in labels.items():
                    t = model.encode_text(tok(prompts)).float()
                    t = t / t.norm(dim=-1, keepdim=True)
                    t = t.mean(0)
                    out[f"text:{axis}:{label}"] = (t / t.norm()).numpy().astype(np.float32)
        np.savez(dst, keys=np.array(keys), **out)
        print(f"wrote {dst}")


# ---------------------------------------------------------------- metrics

def overlap_at(X, R, k):
    """Mean share of each image's k nearest neighbours under X that are also its k nearest under R."""
    def knn(V):
        s = V @ V.T
        np.fill_diagonal(s, -np.inf)
        return np.argsort(-s, axis=1)[:, :k]
    a, b = knn(X), knn(R)
    return np.array([len(set(a[i]) & set(b[i])) / k for i in range(len(X))])


def average_precision(scores, relevant):
    order = np.argsort(-scores)
    rel = relevant[order]
    hits = np.cumsum(rel)
    return float((hits / np.arange(1, len(rel) + 1))[rel].mean()) if rel.any() else np.nan


def boot(fn, n, reps=10_000, seed=0):
    """Point value and 95% bootstrap interval of fn(index array) over n images."""
    rng = np.random.default_rng(seed)
    vals = np.array([fn(rng.integers(0, n, n)) for _ in range(reps)])
    return float(fn(np.arange(n))), float(np.nanpercentile(vals, 2.5)), float(np.nanpercentile(vals, 97.5))


def metrics(split):
    df = pd.read_csv(ROOT / "results" / "experiments" / f"index_{split}_images.csv")
    df = df[df.status == "ok"].set_index("key")
    rows = []
    for name in MODELS:
        z = np.load(cache_dir(split) / f"emb_{name}.npz")
        keys = list(z["keys"])
        has_e = ~np.isnan(z["E"]).any(1)
        sel = np.flatnonzero(has_e)          # every arm is compared on images where every arm exists
        R = z["R"][sel]
        labels = df.loc[[keys[i] for i in sel]]
        for arm in ARMS:
            X = z[arm][sel]
            row = {"model": name, "arm": arm, "n": len(sel)}
            cos = (X * R).sum(1)
            row["cos_median"], row["cos_p10"] = float(np.median(cos)), float(np.percentile(cos, 10))
            for k in (5, 10, 50):
                row[f"overlap@{k}"] = float(overlap_at(X, R, k).mean()) if arm != "R" else 1.0
            for axis, lab in AXES.items():
                names = list(lab)
                T = np.stack([z[f"text:{axis}:{l}"] for l in names])
                truth = labels[f"cat_{axis}"].to_numpy()
                known = np.isin(truth, names)
                pred = np.array(names)[np.argmax(X @ T.T, 1)]
                correct = (pred == truth)[known]
                correct_r = (np.array(names)[np.argmax(R @ T.T, 1)] == truth)[known]
                E = z["E"][sel]
                correct_e = (np.array(names)[np.argmax(E @ T.T, 1)] == truth)[known]
                row[f"acc_{axis}"] = float(correct.mean())
                for ref_name, ref in (("R", correct_r), ("E", correct_e)):
                    d = boot(lambda i: correct[i].mean() - ref[i].mean(), len(correct))
                    row[f"acc_{axis}_minus_{ref_name}"], row[f"acc_{axis}_minus_{ref_name}_lo"], \
                        row[f"acc_{axis}_minus_{ref_name}_hi"] = d
                aps, r10 = [], []
                for j, l in enumerate(names):
                    rel = truth[known] == l
                    if rel.sum() < 5:
                        continue
                    s = (X @ T[j])[known]
                    aps.append(average_precision(s, rel))
                    r10.append(rel[np.argsort(-s)[:10]].mean())
                row[f"map_{axis}"], row[f"p10_{axis}"] = float(np.mean(aps)), float(np.mean(r10))
            rows.append(row)
    m = pd.DataFrame(rows)
    out = ROOT / "results" / "experiments" / f"index_{split}_metrics.csv"
    m.to_csv(out, index=False)
    print(f"wrote {out}")
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["prepare", "embed", "metrics"])
    ap.add_argument("--split", default="index500")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    if a.stage == "prepare":
        prepare(a.split, a.limit, a.workers)
    elif a.stage == "embed":
        embed(a.split)
    else:
        m = metrics(a.split)
        with pd.option_context("display.width", 250, "display.max_columns", 40):
            print(m[["model", "arm", "cos_median", "overlap@10"] + [c for c in m if c.startswith("acc_") and
                                                                    c.count("_") == 1]].round(3).to_string())


if __name__ == "__main__":
    sys.exit(main())
