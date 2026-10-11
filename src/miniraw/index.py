"""Index thumbnail (IDX section) and stored embedding (format version 2, D29).

The index benchmark found that the L1 base layer misses the retrieval bar at its default size, and that a
thumbnail rendered from the full image scores far better at a fraction of L1's bytes
(results/index_benchmark.md). IDX is that thumbnail:

1. Each plane is box-averaged in linear light (raw DN) until its long side is about 2 x LONG_SIDE.
2. The reduced mosaic is developed by LibRaw at half size (one RGB pixel per 2x2 cell, camera white balance,
   orientation applied), the same pipeline as the benchmark's full render.
3. It is resized to LONG_SIDE on the long side and coded as lossy JPEG XL.

The embedding (header `embed`) is a list of {model, dim, dtype, scale, vec}: int8 values of an L2-normalized
OpenCLIP image embedding of IDX, so a file can carry embeddings from more than one model. Computing one needs the
[index] extra (open_clip_torch, torch); reading one doesn't.
"""
from functools import lru_cache

import numpy as np

from . import export, io_raw

LONG_SIDE = 512
DISTANCE = 1.0           # JPEG XL distance: CLIP cosine to a full render 0.975 at 33 KB on dev (1.5: 0.956, 25 KB; D29)
EFFORT = 7
DEFAULT_MODEL = "ViT-B-32/laion2b_s34b_b79k"


def _box(plane, f):
    h, w = plane.shape[0] // f * f, plane.shape[1] // f * f
    return plane[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3))


def thumbnail(raw, header, long_side=LONG_SIDE):
    """8-bit RGB index thumbnail, oriented for display, from a RawImage and its (partial) header."""
    ph, pw = raw.planes.shape[1:]
    f = max(1, max(ph, pw) // (2 * long_side))
    planes = np.stack([np.rint(_box(p.astype(np.float64), f)) for p in raw.planes]).astype(np.uint16)
    mosaic = io_raw.unpack(planes, raw.offsets)
    crop = header["sensor"].get("crop")
    hd = {**header, "sensor": {**header["sensor"], "width": mosaic.shape[1], "height": mosaic.shape[0],
                               "crop": [-(-v // f) if i < 2 else v // f for i, v in enumerate(crop)] if crop else None}}
    img = export.develop(mosaic, hd, half_size=True)
    s = long_side / max(img.shape[:2])
    if s < 1:
        img = export.resize(img, max(1, round(img.shape[0] * s)), max(1, round(img.shape[1] * s)))
    return np.ascontiguousarray(img[..., :3])


def encode_thumbnail(img):
    import imagecodecs
    return bytes(imagecodecs.jpegxl_encode(img, distance=DISTANCE, effort=EFFORT))


def decode_thumbnail(data):
    import imagecodecs
    return np.asarray(imagecodecs.jpegxl_decode(data))


@lru_cache(maxsize=2)
def _model(model_id):
    import open_clip
    import torch
    arch, tag = model_id.split("/")
    model, _, pre = open_clip.create_model_and_transforms(arch, pretrained=tag)
    model.eval()
    torch.set_grad_enabled(False)
    return model, pre


def embed(img, model_id=DEFAULT_MODEL):
    """Header embedding record for an RGB image: int8, scale 1/127 (values of a unit vector lie in [-1, 1])."""
    from PIL import Image
    model, pre = _model(model_id)
    v = model.encode_image(pre(Image.fromarray(img)).unsqueeze(0)).float().numpy()[0]
    v = v / np.linalg.norm(v)
    q = np.clip(np.rint(v * 127), -127, 127).astype(np.int8)
    return {"model": model_id, "dim": int(len(v)), "dtype": "int8", "scale": 1 / 127, "vec": q.tobytes()}


def embedding(record):
    """Unit float32 vector from a header embedding record."""
    v = np.frombuffer(record["vec"], np.int8).astype(np.float32) * record["scale"]
    return v / max(np.linalg.norm(v), 1e-12)
