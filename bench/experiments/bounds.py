"""How far is JPEG XL from what is achievable on noisy raw data?

For each image, in two domains (raw DN = lossless; q at step 1.0 = MiniRaw's L2):
  floor     noise entropy from the fitted noise model: no coder can go below it
  jxl       actual JPEG XL lossless bytes (effort 7, same tiles and P3 packing as the benchmark)
  W, MED    classic causal predictors (left; LOCO-I median edge detector)
  avg4      noise-aware: mean of the 4 causal same-plane neighbours
  cross     avg4 plus already-decoded planes: G1 causal; G2 adds its 4 diagonal G1 neighbours;
            R and B add local green plus a causal colour-difference correction
Predictor rates are the ideal cost of the residuals under an adaptive context model (16 local-activity
classes, empirical distribution per class): what a static two-pass arithmetic coder reaches, within
~1-2% for an adaptive one.

    python -m bench.experiments.bounds data/personal_raws [N]
"""
import sys
from pathlib import Path

import numpy as np

from miniraw import codec, io_raw, noise, vst
from bench.run import RAW_EXT


def cond_entropy_bits(res, ctx):
    """Sum of -log2 p(res | ctx) with the empirical distribution per context."""
    key = ctx.astype(np.int64) * (1 << 20) + (res.astype(np.int64) + (1 << 19))
    _, joint = np.unique(key, return_counts=True)
    _, marg = np.unique(ctx, return_counts=True)
    return float(-(joint * np.log2(joint)).sum() + (marg * np.log2(marg)).sum())


def shifted(a, dy, dx):
    """a[r+dy, c+dx] with edge replication."""
    h, w = a.shape
    r = np.clip(np.arange(h) + dy, 0, h - 1)
    c = np.clip(np.arange(w) + dx, 0, w - 1)
    return a[r][:, c]


def activity_ctx(p):
    W, N, NW, NE = shifted(p, 0, -1), shifted(p, -1, 0), shifted(p, -1, -1), shifted(p, -1, 1)
    act = np.abs(W - NW) + np.abs(N - NW) + np.abs(N - NE)
    return np.minimum(np.floor(np.log2(act + 1) * 2), 15).astype(np.int64)


def predictors(planes, offsets, sigma_of=None):
    """Residuals per predictor for planes coded in the order G1, G2, R, B."""
    P = {k: planes[k].astype(np.int64) for k in range(4)}
    name = {0: "R", 1: "G1", 2: "G2", 3: "B"}
    pos = {name[k]: offsets[k] for k in range(4)}
    out = {"W": [], "MED": [], "avg4": [], "cross": []}
    ctxs = []
    green_at = {}
    for k in (1, 2, 0, 3):
        p = P[k]
        W, N, NW, NE = shifted(p, 0, -1), shifted(p, -1, 0), shifted(p, -1, -1), shifted(p, -1, 1)
        med = np.where(NW >= np.maximum(W, N), np.minimum(W, N),
                       np.where(NW <= np.minimum(W, N), np.maximum(W, N), W + N - NW))
        avg4 = (W + N + NW + NE + 2) >> 2
        if k == 1:
            cross = avg4
        else:
            # mean of the green samples around this plane's sites (already decoded)
            dy, dx = pos[name[k]]
            gs = []
            for g in (1, 2) if k != 2 else (1,):
                gy, gx = pos[name[g]]
                for ry in (-1, 0, 1):
                    for rx in (-1, 0, 1):
                        # neighbouring sites in mosaic units: (2r+dy)+ (2*ry + gy - dy)
                        oy, ox = 2 * ry + gy - dy, 2 * rx + gx - dx
                        if abs(oy) <= 1 and abs(ox) <= 1 and (oy, ox) != (0, 0):
                            gs.append(shifted(P[g], (dy + oy - gy) // 2, (dx + ox - gx) // 2))
            gmean = sum(gs) / len(gs)
            if k == 2:   # G2: blend its causal neighbours with the surrounding G1 (all decoded)
                cross = np.rint((W + N + NW + NE + 4 * gmean) / 8).astype(np.int64)
            else:        # R, B: local green plus the causal colour difference
                d = p - gmean
                dW, dN, dNW, dNE = shifted(d, 0, -1), shifted(d, -1, 0), shifted(d, -1, -1), shifted(d, -1, 1)
                cross = np.rint(gmean + (dW + dN + dNW + dNE) / 4).astype(np.int64)
        ctx = activity_ctx(p)
        for nm, pred in (("W", W), ("MED", med), ("avg4", avg4), ("cross", cross)):
            out[nm].append((p - pred, ctx))
        # Self-correcting blend (in the spirit of JPEG XL's weighted predictor) over candidates that
        # include the noise-averaging and cross-plane predictors JPEG XL does not have. Each
        # candidate is weighted by 1 / (its squared error on the causal neighbours W, N, NW, NE).
        cands = [W, N, W + N - NW, med, avg4, cross]
        preds, weights = [], []
        for c in cands:
            e = np.abs(p - c).astype(np.float64)
            err = shifted(e, 0, -1) + shifted(e, -1, 0) + shifted(e, -1, -1) + shifted(e, -1, 1)
            preds.append(c)
            weights.append(1.0 / (err + 0.5) ** 2)
        wsum = sum(weights)
        blend = np.rint(sum(w * c for w, c in zip(weights, preds)) / wsum).astype(np.int64)
        # Context: blend's own causal error (how predictable this neighbourhood is), plus the known
        # noise level at the predicted value (raw domain; ~constant after the VST)
        eb = np.abs(p - blend).astype(np.float64)
        berr = shifted(eb, 0, -1) + shifted(eb, -1, 0) + shifted(eb, -1, -1) + shifted(eb, -1, 1)
        bctx = np.minimum(np.floor(np.log2(berr + 1) * 3), 23).astype(np.int64)
        out.setdefault("blend", []).append((p - blend, bctx))
        if sigma_of is not None:
            sig = sigma_of[k](blend)
            sctx = np.minimum(np.floor(np.log2(sig + 1) * 3), 31).astype(np.int64)
            out.setdefault("blend+noise", []).append((p - blend, bctx * 32 + sctx))
    return out


def noise_floor_bits(planes, models, T, step=None):
    """Entropy of the noise alone: Gaussian with the model's sigma, quantised to 1 DN (or to the
    q bins at `step`, which is ~2.05 - log2(step) bits for every bin wider than one DN)."""
    bits = 0.0
    for p, m in zip(planes, models):
        x = p.astype(np.float64)
        sigma = np.sqrt(np.maximum(noise.variance_fn(m)(x), 1e-6))
        if step is None:
            h = np.log2(sigma * np.sqrt(2 * np.pi * np.e))
        else:   # in q units the noise is ~1/step; bins narrower than the noise can't go below 0 bits
            h = np.log2(np.sqrt(2 * np.pi * np.e) / step) * np.ones_like(x)
        bits += np.maximum(h, 0).sum()
    return bits


def main(folder, n=3):
    import imagecodecs
    files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in RAW_EXT)[:int(n)]
    rows = []
    for f in files:
        raw = io_raw.read(f)
        npx = raw.planes.size
        models, Ts, qs = [], [], []
        for k in range(4):
            m, T, _ = codec.fit_noise(raw.planes[k], raw.black[k], raw.white, diagnostics=False)
            models.append(m)
            Ts.append(T)
            qs.append(vst.forward_table(T, 1.0, raw.white)[raw.planes[k]])
        q = np.stack(qs)
        from baselines import b2_jxl
        r = {"file": f.name, "npx": npx}
        for dom, planes in (("raw", raw.planes), ("q", q)):
            r[f"{dom}_jxl"] = 8 * b2_jxl.encode_bytes(planes.astype(np.uint16), 512, 7, "P3") / npx
            r[f"{dom}_floor"] = noise_floor_bits(raw.planes, models, Ts, None if dom == "raw" else 1.0) / npx
            sig = [(lambda m: (lambda v: np.sqrt(np.maximum(noise.variance_fn(m)(v), 1e-6))))(m) for m in models] \
                if dom == "raw" else None
            for nm, lst in predictors(planes, raw.offsets, sig).items():
                r[f"{dom}_{nm}"] = sum(cond_entropy_bits(res.ravel(), ctx.ravel()) for res, ctx in lst) / npx
        rows.append(r)
        print(f.name, {k: round(v, 3) for k, v in r.items() if isinstance(v, float)}, flush=True)
    for dom, title in (("raw", "LOSSLESS (raw DN)"), ("q", "HOT TIER L2 (q at step 1.0)")):
        print(f"\n{title}, bits per photosite, median of {len(rows)} files")
        for nm in ("floor", "jxl", "W", "MED", "avg4", "cross", "blend", "blend+noise"):
            if f"{dom}_{nm}" not in rows[0]:
                continue
            v = np.median([r[f"{dom}_{nm}"] for r in rows])
            j = np.median([r[f"{dom}_jxl"] for r in rows])
            print(f"  {nm:6s} {v:6.3f}   ({(v / j - 1) * 100:+5.1f}% vs JPEG XL)")


if __name__ == "__main__":
    main(*sys.argv[1:])
