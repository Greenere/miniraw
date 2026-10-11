"""Byte-exact restore of the original raw file (source.py) and its CLI paths."""
import glob
import hashlib
from pathlib import Path

import imagecodecs
import numpy as np
import pytest

from miniraw import cli, codec, container, export, io_raw, source

ROOT = Path(__file__).resolve().parent.parent


def _ljpeg_header(h, w, predictor, precision=14):
    """Single-component lossless-JPEG header with a Huffman table covering all 17 categories."""
    seg = lambda m, p: bytes([0xFF, m]) + (len(p) + 2).to_bytes(2, "big") + p  # noqa: E731
    sof = bytes([precision]) + h.to_bytes(2, "big") + w.to_bytes(2, "big") + bytes([1, 1, 0x11, 0])
    bits = bytes([0, 0, 0, 0, 17] + [0] * 11)                          # 17 codes of length 5
    dht = bytes([0x00]) + bits + bytes(range(17))
    sos = bytes([1, 1, 0x00, predictor, 0, 0])
    return b"\xff\xd8" + seg(0xC3, sof) + seg(0xC4, dht) + seg(0xDA, sos)


@pytest.mark.parametrize("predictor", range(1, 8))
def test_encode_scan_all_predictors_decode_with_reference(predictor):
    rng = np.random.default_rng(predictor)
    tile = (np.cumsum(rng.integers(-40, 41, (64, 96)), 1) + 4000).clip(0, 16383).astype(np.uint16)
    head = _ljpeg_header(64, 96, predictor)
    ours = head + source.encode_scan(tile[..., None], source.parse_header(head)) + b"\xff\xd9"
    assert np.array_equal(imagecodecs.ljpeg_decode(ours), tile)


def test_encode_scan_matches_reference_encoder_bytes():
    rng = np.random.default_rng(0)
    tile = (np.cumsum(rng.integers(-40, 41, (128, 128)), 0) + 6000).clip(0, 16383).astype(np.uint16)
    ref = bytes(imagecodecs.ljpeg_encode(tile, bitspersample=14))
    _, _, scan0 = source._segments(ref)
    ours = ref[:scan0] + source.encode_scan(tile[..., None], source.parse_header(ref[:scan0]), pad=0)
    assert ours + b"\xff\xd9" == ref


@pytest.fixture()
def ljpeg_dng(synth_image, tmp_path):
    hd = {"sensor": {"cfa": [list(o) for o in synth_image.offsets], "black": synth_image.black,
                     "white": synth_image.white, "wb": [2.0, 1.0, 1.0, 1.5], "bit_depth": 14},
          "source": {"exif": {"Make": "TEST", "Model": "CAM-1"}}}
    mosaic = io_raw.unpack(synth_image.planes, synth_image.offsets)
    p = tmp_path / "IMG_0001.dng"
    p.write_bytes(export.dng_bytes(mosaic, hd, compress=True))
    unc = tmp_path / "IMG_0002.dng"
    unc.write_bytes(export.dng_bytes(mosaic, hd, compress=False))
    return p, unc


def test_cli_restores_original_byte_for_byte(ljpeg_dng, tmp_path):
    src, _ = ljpeg_dng
    mr = tmp_path / "a.mraw"
    cli.main(["encode", str(src), str(mr), "--effort", "3"])
    r = container.RangeReader(mr)
    assert r.has_original and r.header["source"]["original"]["restorable"]
    cli.main(["decode", str(mr), str(tmp_path / "back.DNG")])        # extension match is case-insensitive
    assert (tmp_path / "back.DNG").read_bytes() == src.read_bytes()
    # split: the original needs the sidecar, which carries the SRC section along with L3
    hot, l3 = container.strip(mr.read_bytes())
    (tmp_path / "h.mraw").write_bytes(hot)
    (tmp_path / "h.mraw.l3").write_bytes(l3)
    assert not container.RangeReader(tmp_path / "h.mraw").has_original
    cli.main(["decode", str(tmp_path / "h.mraw"), str(tmp_path / "back2.dng"),
              "--sidecar", str(tmp_path / "h.mraw.l3")])
    assert (tmp_path / "back2.dng").read_bytes() == src.read_bytes()
    # without the sidecar: a valid file in the original format around the near-lossless pixels
    cli.main(["decode", str(tmp_path / "h.mraw"), str(tmp_path / "near.dng")])
    import rawpy
    near = codec.decode(tmp_path / "h.mraw", "signal")
    with rawpy.imread(str(tmp_path / "near.dng")) as r:
        assert np.array_equal(r.raw_image_visible[:near.shape[0], :near.shape[1]], near)
    with pytest.raises(SystemExit):
        cli.main(["decode", str(mr), str(tmp_path / "x.ARW")])          # cannot change raw format


def test_unsupported_original_falls_back_to_dng_export(ljpeg_dng, tmp_path):
    _, unc = ljpeg_dng
    mr = tmp_path / "u.mraw"
    cli.main(["encode", str(unc), str(mr), "--effort", "3"])
    orig = container.RangeReader(mr).header["source"]["original"]
    assert orig["restorable"] is False and "lossless-JPEG" in orig["reason"]
    cli.main(["decode", str(mr), str(tmp_path / "out.dng")])            # exports a DNG instead
    import rawpy
    with rawpy.imread(str(tmp_path / "out.dng")) as r:
        m = codec.decode(mr, "exact")
        assert np.array_equal(r.raw_image_visible[:m.shape[0], :m.shape[1]], m)


def test_no_original_flag(ljpeg_dng, tmp_path):
    src, _ = ljpeg_dng
    cli.main(["encode", str(src), str(tmp_path / "n.mraw"), "--effort", "3", "--no-original"])
    assert not container.RangeReader(tmp_path / "n.mraw").has_original


ARWS = sorted(glob.glob(str(ROOT / "data" / "personal_raws" / "*.ARW")))


@pytest.mark.skipif(not ARWS, reason="no real ARW files in data/personal_raws")
def test_real_arw_restores_byte_for_byte_and_near_lossless_without_l3(tmp_path):
    import rawpy
    src = Path(ARWS[0])
    mr = tmp_path / "r.mraw"
    cli.main(["encode", str(src), str(mr)])
    cli.main(["decode", str(mr), str(tmp_path / src.name)])
    assert hashlib.sha256((tmp_path / src.name).read_bytes()).digest() == hashlib.sha256(src.read_bytes()).digest()
    cli.main(["encode", str(src), str(tmp_path / "n.mraw"), "--no-l3"])
    cli.main(["decode", str(tmp_path / "n.mraw"), str(tmp_path / "near.ARW")])
    near = codec.decode(tmp_path / "n.mraw", "signal")
    with rawpy.imread(str(tmp_path / "near.ARW")) as r:
        assert np.array_equal(r.raw_image_visible[:near.shape[0], :near.shape[1]], near)
