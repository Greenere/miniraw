import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench import synth  # noqa: E402
from miniraw import io_raw  # noqa: E402

OFFSETS = [(0, 0), (0, 1), (1, 0), (1, 1)]


def smooth_scene(shape, seed):
    rng = np.random.default_rng(seed)
    f = gaussian_filter(rng.normal(size=shape), 12)
    f = (f - f.min()) / (f.max() - f.min())
    y = np.linspace(0, 1, shape[0])[:, None]
    # Rows near the top are near black so c (read noise) is identifiable.
    return 0.9 * f * y ** 2 * (synth.WHITE - synth.B)


@pytest.fixture(scope="session")
def synth_image():
    """Four 600x700 planes with Poisson-Gaussian noise, a=2, c=16 (known truth)."""
    rng = np.random.default_rng(0)
    planes = np.stack([synth.noisy(smooth_scene((600, 700), p), 2.0, 16.0, rng) for p in range(4)])
    return io_raw.RawImage(planes, OFFSETS, [synth.B] * 4, synth.WHITE, 14)
