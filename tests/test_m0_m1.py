"""M0 (packing, JPEG XL round-trip) and M1 (noise estimator, transform tables)."""
import numpy as np
import pytest

from bench import synth
from miniraw import codec, entropy, io_raw, noise, vst
from conftest import OFFSETS


def test_pack_unpack_roundtrip():
    rng = np.random.default_rng(0)
    m = rng.integers(0, 2 ** 14, (64, 96)).astype(np.uint16)
    for offs in (OFFSETS, [(1, 1), (0, 1), (1, 0), (0, 0)]):
        assert np.array_equal(io_raw.unpack(io_raw.pack(m, offs), offs), m)


def test_cfa_offsets():
    assert io_raw.cfa_offsets(np.array([[0, 1], [3, 2]]), "RGBG") == [(0, 0), (0, 1), (1, 0), (1, 1)]
    with pytest.raises(io_raw.NotBayer):
        io_raw.cfa_offsets(np.array([[0, 0], [0, 0]]), "RGBG")


@pytest.mark.parametrize("hi", [3, 4095, 16383, 65535])
def test_jxl_16bit_lossless(hi):
    rng = np.random.default_rng(hi)
    a = rng.integers(0, hi + 1, (257, 311)).astype(np.uint16)
    assert np.array_equal(entropy.jxl_decode(entropy.jxl_encode(a)), a)
    s = rng.integers(-300, 300, (64, 64))
    assert np.array_equal(entropy.jxl_decode(entropy.jxl_encode(s)), s)


@pytest.mark.parametrize("shape", [(512, 512), (17, 9), (1, 5), (300, 211)])
@pytest.mark.parametrize("levels", [1, 2, 3])
def test_dwt53_perfect_reconstruction(shape, levels):
    from miniraw import layers
    t = np.random.default_rng(levels).integers(0, 4000, shape)
    c, _ = layers.dwt53(t, levels)
    assert np.array_equal(layers.idwt53(c, levels), t)


def test_estimator_accuracy(synth_image):
    for p in range(4):
        m, _ = noise.estimate(synth_image.planes[p], synth.B, synth.WHITE)
        assert abs(m["a"] / 2.0 - 1) <= 0.10
        assert abs(m["c"] - 16.0) <= max(0.25 * 16.0, 1.0)


def test_flat_sigma_after_transform(synth_image):
    for p in range(4):
        _, _, info = codec.fit_noise(synth_image.planes[p], synth.B, synth.WHITE)
        sig = np.array(info["validate_sigma"])
        assert len(sig) and np.all((sig >= 0.9) & (sig <= 1.1))


@pytest.mark.parametrize("model", [
    {"type": "affine", "a": 2.0, "b": 512.0, "c": 16.0},
    {"type": "affine", "a": 0.1, "b": 0.0, "c": 0.01},
    {"type": "isotonic", "b": 512.0, "knots_x": [0, 600, 5000, 16383], "knots_v": [9, 9, 200, 3000]},
])
@pytest.mark.parametrize("step", [0.5, 1.0, 2.0])
def test_forward_table(model, step):
    xmax = 16383
    T = vst.T_table(model, xmax)
    assert np.all(np.diff(T) > 0)
    fwd = vst.forward_table(T, step)
    assert fwd[0] == 0 and set(np.unique(np.diff(fwd))) <= {0, 1}
    lo = vst.lo_from_forward(fwd)
    assert np.array_equal(vst.decode_lo(vst.encode_lo(lo)), lo)
    assert np.array_equal(vst.forward_from_lo(lo, xmax), fwd)
    n, rec = vst.tables_from_lo(lo, xmax)
    assert n.sum() == xmax + 1 and np.all(rec >= lo) and np.all(rec < lo + n)
    assert np.all(vst.bound_check(T, fwd, rec, step) <= 1e-9)


def test_unestimable_plane_falls_back_to_lossless(synth_image):
    """A plane with no unclipped flat blocks (black 0, mostly zeros, like a dark Leica M8 frame)
    encodes with a lossless model instead of raising, and decodes exactly in every mode."""
    rng = np.random.default_rng(0)
    planes = synth_image.planes.copy()
    planes[3] = rng.integers(0, 3, planes[3].shape) * rng.integers(0, 2, planes[3].shape)
    img = io_raw.RawImage(planes, OFFSETS, synth_image.black[:3] + [0], synth_image.white, 14)
    data, info = codec.encode(img, step=1.0, tile=256)
    assert info["planes"][3]["fallback"] == "lossless"
    assert "fallback" not in info["planes"][0]
    orig = io_raw.unpack(planes, OFFSETS)
    assert np.array_equal(codec.decode(data, "exact"), orig)
    assert np.array_equal(codec.decode(data, "signal")[1::2, 1::2], planes[3])


@pytest.mark.parametrize("step", [1.0, 2.0, 3.0, 4.0])
def test_bound_holds_at_large_steps(step):
    """Bins are split where the middle DN would miss the bound (D27); the 5D Mark II-like model failed at step 2."""
    model = {"type": "affine", "a": 7.0, "b": 1021.0, "c": 350.0}
    T = vst.T_table(model, 16383)
    fwd = vst.forward_table(T, step, 15600)
    n, rec = vst.tables_from_lo(vst.lo_from_forward(fwd), 16383)
    assert vst.bound_check(T, fwd, rec, step).max() <= 1e-9


def test_header_stats_match_known_noise(synth_image):
    """stats.snr18 follows from the noise model relative to full scale (D28)."""
    from miniraw import container, stats
    model = {"type": "affine", "a": 2.0, "b": float(synth.B), "c": 16.0}
    st = stats.technical(synth_image, [model] * 4)
    full = synth.WHITE - synth.B
    assert st["snr18"] == pytest.approx(0.18 * full / np.sqrt(2.0 * 0.18 * full + 16.0), rel=1e-3)
    assert 0 <= st["clipped_frac"] < 1 and 0 < st["log_mean"] <= st["mean_level"] < 1
    data, _ = codec.encode(synth_image, step=1.0, tile=256)
    hd = container.RangeReader(data).header
    assert hd["stats"]["snr18"] is not None and len(hd["stats"]["snr18_planes"]) == 4
    assert len(data) - sum(container.layer_sizes(hd).values()) < 8192     # header stays under its 8 KB target


def test_header_stats_lossless_fallback_plane():
    from miniraw import stats
    rng = np.random.default_rng(1)
    planes = rng.integers(0, 3, (4, 64, 64)).astype(np.uint16)
    img = io_raw.RawImage(planes, OFFSETS, [0] * 4, 4095, 12)
    st = stats.technical(img, [noise.lossless_model(0, 4095)] * 4)
    assert st["snr18"] is None and st["clipped_frac"] == 0
