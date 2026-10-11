"""Per-image benchmark runner. One CSV per image under results/per_image/<split>/.

Resumable: images whose CSV exists are skipped.

    python -m bench.run --config configs/phase1.toml --split dev
"""
import argparse
import csv
import hashlib
import json
import time
import tomllib
import traceback
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from baselines import b1_native, b2_jxl, b5_sqrt
from miniraw import codec, container, io_raw, vst
from . import metrics, synth
from .datasets import camera_of, path_for, read_split

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"


def _sizes(data):
    r = container.RangeReader(data)
    s = container.layer_sizes(r.header)
    s["L0"] = len(data) - sum(s.values())
    return s


def _signal_quality(raw, data, info, step, prefix):
    dec = codec.Decoder(container.RangeReader(data))
    sig = dec.planes("signal")
    checker = dec.layout.get("rec") == "checker"
    sig_alt = dec.planes("signal", rec="floor" if checker else "checker")
    row = {}
    viol, viol_alt, rmse_sq, maxerr, bias, bias_alt = 0, 0, 0.0, 0.0, 0.0, 0.0
    for p in range(4):
        pi = info["planes"][p]
        T, fwd = pi["T"], pi["fwd"]
        rec = dec.tables[p][2]
        viol += metrics.bound_violations(raw.planes[p], T, fwd, rec, step, checker)
        viol_alt += metrics.bound_violations(raw.planes[p], T, fwd, rec, step, not checker)
        r, m = metrics.stabilized_errors(raw.planes[p], sig[p], T)
        rmse_sq += r * r / 4
        maxerr = max(maxerr, m)
        bias = max(bias, metrics.bias_by_decile(raw.planes[p], sig[p], pi["model"]))
        bias_alt = max(bias_alt, metrics.bias_by_decile(raw.planes[p], sig_alt[p], pi["model"]))
    row[f"{prefix}bound_violations"] = viol
    row[f"{prefix}stab_rmse"] = float(np.sqrt(rmse_sq))
    row[f"{prefix}stab_maxerr"] = maxerr
    row[f"{prefix}bias_max"] = bias
    alt = "floor" if checker else "checker"
    row[f"{prefix}bias_max_{alt}"] = bias_alt
    row[f"{prefix}bound_violations_{alt}"] = viol_alt
    return row, sig


def run_image(key, cfg, split):
    out = RESULTS / "per_image" / split / f"{key}.csv"
    if out.exists():
        return key, "skip"
    t0 = time.time()
    path = path_for(key)
    row = {"key": key, "camera": camera_of(key)}
    try:
        raw = io_raw.read(path)
    except io_raw.NotBayer as e:
        row.update(status="skipped_non_bayer", reason=str(e))
        _write(out, [row])
        return key, "non-bayer"
    h, w = raw.sensor_shape
    npx = h * w
    gaps = io_raw.histogram_gaps(raw.planes, raw.black, raw.white)
    row.update(status="ok", height=h, width=w, bit_depth=raw.bit_depth, gap_frac=gaps,
               lossy_source=gaps > io_raw.GAP_THRESHOLD)
    E, T = cfg["effort"], cfg["tile"]
    step = cfg["step"]

    # --- MiniRaw default configuration
    te = time.time()
    enc = dict(tile=T, effort=E, upsampler=cfg["upsampler"], l1_mode=cfg["l1_mode"], rec=cfg["rec"],
               packing=cfg["packing"], l1_min_side=cfg["l1_min_side"])
    data, info = codec.encode(raw, step=step, source={"file": path.name, "camera": row["camera"]}, **enc)
    row["encode_s"] = time.time() - te
    models = [pi["model"] for pi in info["planes"]]
    sz = _sizes(data)
    for k, v in sz.items():
        row[f"{k}_bytes"] = v
    row["fallbacks"] = sum(pi["model"]["type"] == "isotonic" for pi in info["planes"])
    row["noise"] = json.dumps([{k: v for k, v in pi["estimated"].items()} for pi in info["planes"]])
    row["validate_sigma_range"] = json.dumps(
        [[min(pi["validate_sigma"], default=0), max(pi["validate_sigma"], default=0)] for pi in info["planes"]])
    row["validate_iso_range"] = json.dumps(
        [[min(pi.get("validate_sigma_isotonic", [0])), max(pi.get("validate_sigma_isotonic", [0]))]
         for pi in info["planes"]])
    row["L3_ideal_bytes"] = sum(
        metrics.l3_ideal_bits(vst.tables_from_lo(vst.lo_from_forward(pi["fwd"]), len(pi["fwd"]) - 1)[0], pi["q"])
        for pi in info["planes"]) / 8
    row["outliers_6sigma"] = sum(metrics.outlier_count(raw.planes[p], models[p]) for p in range(4))
    row["outlier_frac"] = row["outliers_6sigma"] / npx

    # Exactness (8.4): full decode hash and five random regions
    td = time.time()
    ex = codec.decode(data, "exact")
    row["decode_exact_s"] = time.time() - td
    sha = hashlib.sha256(ex.astype("<u2").tobytes()).hexdigest()
    orig = io_raw.unpack(raw.planes, raw.offsets)
    row["exact_ok"] = sha == container.RangeReader(data).header["source"]["sha256"]
    rng = np.random.default_rng([0, int(hashlib.md5(key.encode()).hexdigest()[:8], 16)])
    reg_ok, reads_ok = True, True
    for _ in range(5):
        rw, rh = int(rng.integers(64, min(2048, w))), int(rng.integers(64, min(2048, h)))
        rx, ry = int(rng.integers(0, w - rw)), int(rng.integers(0, h - rh))
        reader = container.RangeReader(data)
        d = codec.Decoder(reader)
        crop = d.mosaic("exact", (rx, ry, rw, rh))
        reg_ok &= bool(np.array_equal(crop, orig[ry:ry + rh, rx:rx + rw]))
        reads_ok &= metrics.region_read_check(reader, d.layout, d.pshape, (rx, ry, rw, rh))
    row["region_exact_ok"] = reg_ok
    row["region_reads_ok"] = reads_ok
    # G4: 1024x1024 sensor crop reads
    reader = container.RangeReader(data)
    d = codec.Decoder(reader)
    d.mosaic("signal", (min(1000, w - 1024), min(1000, h - 1024), 1024, 1024))
    row["crop1024_bytes_read"] = reader.bytes_read()
    row["crop1024_reads_ok"] = metrics.region_read_check(
        reader, d.layout, d.pshape, (min(1000, w - 1024), min(1000, h - 1024), 1024, 1024))
    # Stripped file still decodes in signal mode
    hot, l3 = container.strip(data)
    row["strip_signal_ok"] = bool(np.array_equal(codec.decode(hot, "signal"), codec.decode(data, "signal")))
    row["sidecar_exact_ok"] = bool(np.array_equal(codec.decode(hot, "exact", sidecar=l3), orig))

    # Signal-mode fidelity (8.2, non-render)
    q, _ = _signal_quality(raw, data, info, step, "")
    row.update(q)

    # --- Variants for G3 (no L1), G4 (untiled), upsampler choice
    d_nol1, _ = codec.encode(raw, step=step, tile=T, effort=E, use_l1=False, with_l3=False, models=models)
    row["L2_direct_bytes"] = _sizes(d_nol1)["L2"]
    d_untiled, _ = codec.encode(raw, step=step, with_l3=False, models=models, **{**enc, "tile": None})
    s = _sizes(d_untiled)
    row["L1L2_untiled_bytes"] = s["L1"] + s["L2"]
    for mode, ups in [("mean", "bilinear"), ("mean", "nearest"), ("wavelet", "bilinear")]:
        dv, _ = codec.encode(raw, step=step, with_l3=False, models=models,
                             **{**enc, "l1_mode": mode, "upsampler": ups})
        s = _sizes(dv)
        row[f"L1L2_{mode}_{ups}_bytes"] = s["L1"] + s["L2"]
    # G3 versus base-layer size: construction A with k doubled and quadrupled
    from miniraw import layers as _layers
    k0 = _layers.choose_k(raw.planes.shape[1:], cfg["l1_min_side"])
    row["k"] = k0
    for mult in (2, 4):
        dv, _ = codec.encode(raw, step=step, with_l3=False, models=models,
                             **{**enc, "l1_mode": "mean", "k": k0 * mult})
        s = _sizes(dv)
        row[f"L1L2_mean_k{mult}x_bytes"] = s["L1"] + s["L2"]
        row[f"L1_mean_k{mult}x_bytes"] = s["L1"]

    # --- Baselines
    row["B0_bpp"] = raw.bit_depth
    row["B1_bytes"] = b1_native.raw_bytes(path)
    row["B2_bytes"] = b2_jxl.encode_bytes(raw.planes, T, E, cfg["packing"])
    row["B2_P1_bytes"] = b2_jxl.encode_bytes(raw.planes, T, E, "P1")
    if cfg.get("b2_effort9"):
        row["B2e9_bytes"] = b2_jxl.encode_bytes(raw.planes, T, 9, cfg["packing"])
    for bits in cfg["b5_bits"]:
        if bits >= raw.bit_depth:
            continue
        nb, dec = b5_sqrt.run(raw, bits, T, E, cfg["packing"])
        errs = [metrics.stabilized_errors(raw.planes[p], dec[p], info["planes"][p]["T"]) for p in range(4)]
        row[f"B5_{bits}_bytes"] = nb
        row[f"B5_{bits}_stab_rmse"] = float(np.sqrt(np.mean([e[0] ** 2 for e in errs])))
        row[f"B5_{bits}_stab_maxerr"] = max(e[1] for e in errs)

    # --- Step sweep (hot tier only)
    for st in cfg["steps"]:
        if st == step:
            continue
        ds, inf = codec.encode(raw, step=st, with_l3=False, models=models, **enc)
        s = _sizes(ds)
        row[f"step{st}_L0L1L2_bytes"] = s["L0"] + s["L1"] + s["L2"]
        qq, _ = _signal_quality(raw, ds, inf, st, f"step{st}_")
        row.update(qq)

    row["npx"] = npx
    row["total_s"] = time.time() - t0
    _write(out, [row])
    return key, f"{row['total_s']:.0f}s"


def run_synth(cfg, split, n_images=3):
    """Estimator accuracy and ground-truth fidelity on synthetic Poisson-Gaussian planes."""
    out = RESULTS / "per_image" / f"synth_{split}.csv"
    if out.exists():
        return
    rows = []
    keys = read_split(split)[:n_images]
    for key in keys:
        raw = io_raw.read(path_for(key))
        cleans = [synth.clean_plane(raw.planes[p], raw.black[p]) for p in range(4)]
        for a, c in synth.settings():
            draws = [synth.generate(cl, a, c, seed=p) for p, cl in enumerate(cleans)]
            x1 = np.stack([d[0] for d in draws])
            img = io_raw.RawImage(x1, raw.offsets, [synth.B] * 4, synth.WHITE, 14)
            data, info = codec.encode(img, step=cfg["step"], tile=cfg["tile"], effort=cfg["effort"],
                                      l1_mode=cfg["l1_mode"], rec=cfg["rec"], packing=cfg["packing"],
                                      l1_min_side=cfg["l1_min_side"])
            dec = codec.Decoder(container.RangeReader(data)).planes("signal")
            true = {"type": "affine", "a": a, "b": float(synth.B), "c": c}
            Tt = vst.T_table(true, synth.WHITE)
            num = den = 0.0
            for p in range(4):
                tc = np.interp(cleans[p] + synth.B, np.arange(len(Tt)), Tt)
                num += np.mean((Tt[dec[p].astype(np.int64)] - tc) ** 2)
                den += np.mean((Tt[x1[p].astype(np.int64)] - tc) ** 2)
            for p in range(4):
                pi = info["planes"][p]
                est = pi["estimated"]
                # c is identifiable only if it is >= 20% of the variance in the darkest populated bin
                s_min = max(np.percentile(cleans[p], 1), 0)
                rows.append({
                    "key": key, "a": a, "c": c, "plane": p, "a_est": est["a"], "c_est": est["c"],
                    "a_relerr": est["a"] / a - 1, "c_err": est["c"] - c,
                    "c_identifiable": c >= 0.25 * a * s_min,
                    "validated": pi["validated"],
                    "val_sigma_min": min(pi["validate_sigma"], default=0),
                    "val_sigma_max": max(pi["validate_sigma"], default=0),
                    "gt_ratio": float(np.sqrt(num / den)),
                    "exact_ok": bool(np.array_equal(
                        codec.Decoder(container.RangeReader(data)).planes("exact"), x1)) if p == 0 else None,
                })
        print("synth", key, flush=True)
    _write(out, rows)


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=keys)
        wr.writeheader()
        wr.writerows(rows)
    tmp.rename(path)


def _worker(args):
    key, cfg, split = args
    try:
        return run_image(key, cfg, split)
    except Exception:
        return key, "ERROR\n" + traceback.format_exc()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "configs" / "phase1.toml"))
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-synth", action="store_true")
    a = ap.parse_args()
    cfg = tomllib.loads(Path(a.config).read_text())
    keys = read_split(a.split)[: a.limit]
    with Pool(cfg.get("workers", 4)) as pool:
        for key, status in pool.imap_unordered(_worker, [(k, cfg, a.split) for k in keys]):
            print(f"{key}: {status}", flush=True)
    if not a.no_synth and a.split == "dev":
        run_synth(cfg, a.split)


if __name__ == "__main__":
    main()
