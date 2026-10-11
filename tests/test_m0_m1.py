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
