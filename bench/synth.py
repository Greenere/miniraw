"""Synthetic Poisson-Gaussian planes at known noise parameters (spec 7).

Clean planes come from dev images: box-downsample 2x2 then Gaussian blur
(sigma 2) to suppress the source noise, rescaled to 14 bits. See DEVIATIONS.md.
"""
import itertools

import numpy as np
from scipy.ndimage import gaussian_filter

B = 512
WHITE = 16383
A_VALUES = (0.3, 1.0, 3.0, 10.0)
C_VALUES = (1.0, 9.0, 100.0)


def clean_plane(plane, black):
    h, w = plane.shape
    p = plane[: h - h % 2, : w - w % 2].astype(np.float64) - black
    p = p.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
    p = gaussian_filter(np.maximum(p, 0), 2.0)
    return p * (0.9 * (WHITE - B) / max(np.percentile(p, 99.9), 1.0))


def noisy(clean, a, c, rng):
    x = B + a * rng.poisson(np.maximum(clean, 0) / a) + rng.normal(0, np.sqrt(c), clean.shape)
    return np.clip(np.rint(x), 0, WHITE).astype(np.uint16)


def settings():
    return list(itertools.product(A_VALUES, C_VALUES))


def generate(clean, a, c, seed=0):
    """Two independent noise draws at (a, c)."""
    rng = np.random.default_rng([seed, int(a * 10), int(c)])
    return noisy(clean, a, c, rng), noisy(clean, a, c, rng)
