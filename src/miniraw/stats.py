"""Technical image statistics for the header (`stats`), comparable across cameras.

The noise model is stored in raw DN, which depends on bit depth and gain, so the same ISO can differ 34x between
cameras (results/index_benchmark.md, H4). These figures are relative to each plane's full scale (white - black):

  snr18        signal-to-noise ratio at 18% of full scale (green planes, mean)
  clipped_frac share of pixels at or above the white level (all planes)
  mean_level   mean signal as a fraction of full scale (all planes)
  log_mean     geometric-mean signal as a fraction of full scale (green planes; floor 1e-4), an exposure measure

Per-plane SNR is kept too. A plane whose model is the lossless fallback (no noise estimate) contributes None.

Dynamic range (full scale over noise at black) is deliberately left out: it depends on the read-noise term c,
which one image often can't identify (D4), and on index500 it spread 19 stops between cameras at ISO 100.
"""
import numpy as np

from . import noise

GREEN = (1, 2)
SUBSAMPLE = 4          # levels are computed on every 4th row and column: plenty for a mean, 16x less work


def _snr18(model, b, white):
    full = white - b
    if full <= 0 or (model["type"] == "isotonic" and max(model["knots_v"]) <= 1e-5):
        return None
    s18 = 0.18 * full
    return s18 / float(np.sqrt(max(noise.variance_fn(model)(b + s18), 1e-12)))


def technical(raw, models):
    snr = [_snr18(m, raw.black[p], raw.white) for p, m in enumerate(models)]
    green = lambda v: [v[p] for p in GREEN if v[p] is not None]  # noqa: E731
    levels, logs, clipped, n = [], [], 0, 0
    for p in range(4):
        x = raw.planes[p][::SUBSAMPLE, ::SUBSAMPLE].astype(np.float64)
        full = max(raw.white - raw.black[p], 1)
        rel = np.clip((x - raw.black[p]) / full, 0, None)
        levels.append(rel.mean())
        if p in GREEN:
            logs.append(np.log(np.maximum(rel, 1e-4)).mean())
        clipped += int((raw.planes[p] >= raw.white).sum())
        n += raw.planes[p].size
    r = lambda v: None if v is None else round(float(v), 4)  # noqa: E731
    return {
        "snr18": r(np.mean(green(snr))) if green(snr) else None,
        "snr18_planes": [r(v) for v in snr],
        "clipped_frac": r(clipped / n),
        "mean_level": r(np.mean(levels)),
        "log_mean": r(np.exp(np.mean(logs))),
    }
