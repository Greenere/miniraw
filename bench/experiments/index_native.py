"""Index cost on native camera raws (H3 and H2 of docs/INDEX_BENCHMARK_PLAN.md).

    python -m bench.experiments.index_native DIR [OUT_CSV]

For each raw file in DIR: the embedded previews (exiftool: offset and length where the format exposes them),
the thumbnail LibRaw extracts, and the same file encoded as MiniRaw (hot tier only): bytes read for L0 and for
L0 + L1, L1's size and rendered dimensions. Also the cosine similarity to a full render, under both models, of
the camera's small thumbnail, its large preview and L1 (half-size render, as in the index benchmark).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from bench.experiments.index_eval import MODELS, load_model, orient
from miniraw import codec, container, export, io_raw

ROOT = Path(__file__).resolve().parents[2]
RAW_EXT = {".arw", ".cr2", ".cr3", ".nef", ".rw2", ".orf", ".dng", ".pef"}


def previews(path):
    """[(name, offset or None, length)] for every embedded JPEG exiftool reports."""
    tags = json.loads(subprocess.run(["exiftool", "-j", "-a", "-G1", "-n", str(path)], capture_output=True,
                                     text=True).stdout)[0]
    found = {}
    for k, v in tags.items():
        m = re.match(r"(.*):(PreviewImage|Thumbnail|JpgFromRaw|OtherImage)(Start|Offset|Length)$", k)
        if m and isinstance(v, (int, float)):
            found.setdefault((m.group(1), m.group(2)), {})["len" if m.group(3) == "Length" else "off"] = int(v)
    out = [(f"{g}:{n}", d.get("off"), d["len"]) for (g, n), d in found.items() if d.get("len")]
    if not out:      # e.g. CR3: previews live in ISO-BMFF boxes; take lengths from extraction
        for tag in ("ThumbnailImage", "PreviewImage", "JpgFromRaw"):
            b = subprocess.run(["exiftool", "-b", f"-{tag}", str(path)], capture_output=True).stdout
            if b:
                out.append((tag, None, len(b)))
    return sorted(out, key=lambda t: t[2])


def decode_jpeg(path, name):
    tag = name.split(":")[-1]
    tag = {"Thumbnail": "ThumbnailImage", "OtherImage": "OtherImage"}.get(tag, tag)
    b = subprocess.run(["exiftool", "-b", f"-{tag}", str(path)], capture_output=True).stdout
    if not b:
        return None
    import imagecodecs
    try:
        return imagecodecs.jpeg8_decode(b)
    except Exception:
        return None


def main(src, out=None):
    import rawpy
    import torch
    from PIL import Image
    out = Path(out or ROOT / "results" / "experiments" / "index_native.csv")
    files = sorted(p for p in Path(src).iterdir() if p.suffix.lower() in RAW_EXT)
    rows, imgs = [], {}
    for path in files:
        row = {"file": path.name, "file_bytes": path.stat().st_size}
        pv = previews(path)
        row["previews"] = "; ".join(f"{n} {l / 1e3:.0f} KB" + (f" @ {o / 1e3:.0f} KB" if o is not None else "")
                                    for n, o, l in pv)
        row["thumb_bytes"], row["preview_bytes"] = (pv[0][2], pv[-1][2]) if pv else (None, None)
        row["preview_end_bytes"] = max((o + l for _, o, l in pv if o is not None), default=None)
        raw = io_raw.read(path)
        with rawpy.imread(str(path)) as r:
            ref = r.postprocess(use_camera_wb=True, half_size=True, output_bps=8)
        small = decode_jpeg(path, pv[0][0]) if pv else None
        large = decode_jpeg(path, pv[-1][0]) if pv else None
        data, _ = codec.encode(raw, step=1.0, effort=4, with_l3=False, keep_original=False)
        reader = container.RangeReader(data)
        row["L0_read_bytes"] = reader.bytes_read()
        dec = codec.Decoder(reader)
        mos, hd = dec.preview_mosaic()
        row["L0L1_read_bytes"] = reader.bytes_read()
        row["mraw_hot_bytes"] = len(data)
        l1 = export.develop(mos, hd, half_size=True)
        row["L1_half_h"], row["L1_half_w"] = l1.shape[:2]
        row["k"] = reader.header["layout"]["k"]
        row["thumb_dims"] = f"{small.shape[1]}x{small.shape[0]}" if small is not None else ""
        row["preview_dims"] = f"{large.shape[1]}x{large.shape[0]}" if large is not None else ""
        # previews are stored in sensor orientation in most formats; orient like the render if the shape says so
        fix = lambda im: orient(im, raw.flip) if im is not None and (im.shape[0] > im.shape[1]) != \
            (ref.shape[0] > ref.shape[1]) else im  # noqa: E731
        imgs[path.name] = {"R": ref, "thumb": fix(small), "preview": fix(large), "L1": l1}
        rows.append(row)
        print(path.name, row["previews"], flush=True)
    for name in MODELS:
        model, pre, _ = load_model(name)
        with torch.no_grad():
            for row in rows:
                e = {}
                for arm, im in imgs[row["file"]].items():
                    if im is not None:
                        v = model.encode_image(pre(Image.fromarray(np.ascontiguousarray(im[..., :3]))).unsqueeze(0))
                        e[arm] = (v / v.norm()).float().numpy()[0]
                for arm in ("thumb", "preview", "L1"):
                    row[f"cos_{arm}_{name}"] = float(e[arm] @ e["R"]) if arm in e else None
    df = pd.DataFrame(rows)
    df.to_csv(out, index=False)
    print(f"wrote {out}")
    with pd.option_context("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 60):
        print(df.drop(columns=["previews"]).to_string(index=False))


if __name__ == "__main__":
    main(*sys.argv[1:3])
