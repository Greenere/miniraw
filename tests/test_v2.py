"""Format version 2 (D29, D30) and compatibility with files written by v1.0.0."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from miniraw import codec, container, io_raw, vst
from conftest import OFFSETS

DATA = Path(__file__).parent / "data"


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(a).astype("<u2").tobytes()).hexdigest()


def test_v1_0_0_file_decodes_bit_identically():
    """A file written by the v1.0.0 release decodes exactly as v1.0.0 decoded it (tests/data, made with git
    worktree at tag v1.0.0)."""
    want = json.loads((DATA / "v1_0_0_synthetic.json").read_text())
    data = (DATA / "v1_0_0_synthetic.mraw").read_bytes()
    assert container.RangeReader(data).version == 1
    assert sha(codec.decode(data, "exact")) == want["exact"]
    assert sha(codec.decode(data, "signal")) == want["signal"]
    assert list(codec.decode(data, "preview").shape) == want["preview_shape"]


@pytest.fixture(scope="module")
def v2_file(synth_image):
    return codec.encode(synth_image, step=1.0, tile=256)


def test_v2_defaults(v2_file, synth_image):
    data, _ = v2_file
    r = container.RangeReader(data)
    lay = r.header["layout"]
    assert r.version == 2 and not lay["l1"] and lay["rec"] == "csafe"
    assert ("IDX", 0, 0, 0) in r.sections and not any(k[0] == "L1" for k in r.sections)
    assert np.array_equal(codec.decode(data, "exact"), io_raw.unpack(synth_image.planes, synth_image.offsets))


def test_idx_is_first_and_survives_strip(v2_file):
    data, _ = v2_file
    r = container.RangeReader(data)
    first = min(v[0] for v in r.sections.values())
    assert r.sections[("IDX", 0, 0, 0)][0] == first
    hot, _ = container.strip(data)
    img = codec.Decoder(container.RangeReader(hot)).preview()
    assert img.ndim == 3 and max(img.shape[:2]) <= 512 and img.dtype == np.uint8


def test_format_version_1_writes_a_v1_file(synth_image):
    data, _ = codec.encode(synth_image, step=1.0, tile=256, format_version=1)
    r = container.RangeReader(data)
    assert r.version == 1 and r.header["layout"]["l1"] and r.header["layout"]["rec"] == "floor"
    assert ("IDX", 0, 0, 0) not in r.sections
    with pytest.raises(ValueError):
        codec.encode(synth_image, format_version=1, idx=True)


def test_newer_format_is_rejected(v2_file):
    data = bytearray(v2_file[0])
    data[4:6] = (container.VERSION + 1).to_bytes(2, "little")
    with pytest.raises(ValueError, match="newer"):
        container.RangeReader(bytes(data))


def test_csafe_keeps_the_bound(v2_file, synth_image):
    """rec 'csafe': both reconstructions a bin can use (floor, and ceiling where flagged) stay within the bound
    on every DN, only even bins are flagged, and the signal decode stays inside each pixel's bin."""
    data, info = v2_file
    dec = codec.Decoder(container.RangeReader(data))
    sig = dec.planes("signal")
    for p in range(4):
        T, fwd = info["planes"][p]["T"], info["planes"][p]["fwd"]
        lo, n, rec = dec.tables[p]
        bits = dec.rec_bits[p]
        assert vst.bound_check(T, fwd, rec, 1.0).max() <= 1e-9
        assert vst.bound_check(T, fwd, rec + bits, 1.0).max() <= 1e-9
        assert not bits[n % 2 == 1].any() and bits.any()
        q = info["planes"][p]["q"]
        assert ((sig[p] >= lo[q]) & (sig[p] < lo[q] + n[q])).all()


def test_csafe_removes_the_floor_offset():
    """On a low-noise plane where most bins hold 2 values, floor reconstruction is biased about -0.5 DN;
    the checkerboard of csafe brings the mean error near zero."""
    rng = np.random.default_rng(3)
    clean = np.linspace(300, 900, 256 * 256).reshape(256, 256)
    planes = np.stack([np.rint(clean + rng.normal(0, 1.6, clean.shape)) for _ in range(4)]).astype(np.uint16)
    img = io_raw.RawImage(planes, OFFSETS, [256] * 4, 4095, 12)
    model = {"type": "affine", "a": 1e-4, "b": 256.0, "c": 2.56}
    err = {}
    for rec in ("floor", "csafe"):
        data, _ = codec.encode(img, step=1.0, tile=128, models=[model] * 4, rec=rec)
        err[rec] = float((codec.decode(data, "signal").astype(float) - io_raw.unpack(planes, OFFSETS)).mean())
    assert err["floor"] < -0.2 and abs(err["csafe"]) < 0.1


def test_embedding_roundtrip(synth_image):
    pytest.importorskip("open_clip")
    from miniraw import index
    data, _ = codec.encode(synth_image, step=1.0, tile=256, embed=index.DEFAULT_MODEL)
    rec = container.RangeReader(data).header["embed"][0]
    v = index.embedding(rec)
    assert rec["dtype"] == "int8" and rec["dim"] == len(v) == 512 and abs(np.linalg.norm(v) - 1) < 1e-5
    thumb = codec.Decoder(container.RangeReader(data)).preview()
    w = index.embedding(index.embed(thumb))        # re-embedding the decoded thumbnail gives nearly the same vector
    assert float(v @ w) > 0.97
