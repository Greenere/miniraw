"""How much can any context save on L3 (the in-bin remainder) on real data?

Contexts (all: where a local estimate falls relative to the pixel's bin, 16 classes, per bin size):
  l2      3x3 mean of signal-mode values (decoder has L2): the earlier l3_context experiment
  causal  mean of the exact W, N, NW, NE samples (decoder can have them, decoding each tile in order)
  cheat   mean of the exact 3x3 neighbours on all sides (not decodable: an optimistic upper bound)
Savings are empirical conditional entropy versus the uniform model (tables ignored).

    python -m bench.experiments.l3_bound data/personal_raws [N]
"""
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter

from miniraw import codec, io_raw, vst
from bench.experiments.bounds import shifted
from bench.experiments.l3_context import cond_entropy
from bench.run import RAW_EXT


def main(folder, n=3, step=1.0):
    files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in RAW_EXT)[:int(n)]
    print(f"step {step}: L3 bits per photosite, uniform vs best context (savings)")
    for f in files:
        raw = io_raw.read(f)
        tot = {"uniform": 0.0, "l2": 0.0, "causal": 0.0, "cheat": 0.0}
        for k in range(4):
            x = raw.planes[k].astype(np.int64)
            _, T, _ = codec.fit_noise(raw.planes[k], raw.black[k], raw.white, diagnostics=False)
            fwd = vst.forward_table(T, float(step), raw.white)
            lo = vst.lo_from_forward(fwd)
            nn_, rec = vst.tables_from_lo(lo, len(fwd) - 1)
            q = fwd[x]
            nb, kap, base = nn_[q], x - lo[q], lo[q]
            sel = nb > 1
            tot["uniform"] += np.log2(nb[sel]).sum()
            xf = x.astype(np.float64)
            est = {
                "l2": uniform_filter(rec[q].astype(np.float64), 3, mode="nearest"),
                "causal": (shifted(xf, 0, -1) + shifted(xf, -1, 0) + shifted(xf, -1, -1) + shifted(xf, -1, 1)) / 4,
                "cheat": (uniform_filter(xf, 3, mode="nearest") * 9 - xf) / 8,
            }
            for name, mu in est.items():
                pos = np.clip(np.floor((mu - base) / nb * 4), -6, 9).astype(np.int64)
                big = nb > 32
                sym = np.where(big, kap * 16 // nb, kap)
                nkey = np.where(big, 1000 + np.minimum(nb // 8, 50), nb)
                ctx = (nkey * 32 + pos + 6)[sel]
                bits = cond_entropy(sym[sel], ctx) + np.log2(nb[sel & big] / 16).sum()
                tot[name] += bits
        npx = raw.planes.size
        u = tot["uniform"] / npx
        print(f"  {f.name}: uniform {u:.3f}  " + "  ".join(
            f"{k} -{(u - v / npx):.3f} ({(u - v / npx) / u * 100:.1f}%)" for k, v in tot.items() if k != "uniform"))


if __name__ == "__main__":
    main(*sys.argv[1:])
