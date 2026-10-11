"""DNG / image export and the CLI's extension dispatch."""
import io

import numpy as np
import pytest

from miniraw import cli, codec, container, export, io_raw

rawpy = pytest.importorskip("rawpy")


@pytest.fixture(scope="module")
def mraw(synth_image, tmp_path_factory):
    img = io_raw.RawImage(synth_image.planes, synth_image.offsets, synth_image.black, synth_image.white,
                          14, wb=[2.0, 1.0, 1.0, 1.5],
                          cam_xyz=[[0.75, -0.24, -0.06], [-0.57, 1.34, 0.25], [-0.06, 0.12, 0.66]],
                          crop=[4, 2, 1380, 1190],
                          exif={"Make": "TEST", "Model": "CAM-1", "ISO": 400, "ExposureTime": [1, 250],
                                "DateTime": "2026:10:10 12:00:00", "LensModel": "50mm"})
    data, _ = codec.encode(img, tile=256, effort=3)
    path = tmp_path_factory.mktemp("mraw") / "x.mraw"
    path.write_bytes(data)
    return img, path


@pytest.mark.parametrize("compress", [False, True])
def test_dng_roundtrip_is_bit_exact(mraw, compress):
    img, path = mraw
    hd = container.RangeReader(path).header
    mosaic = codec.decode(path, "exact")
    dng = export.dng_bytes(mosaic, hd, compress=compress)
    with rawpy.imread(io.BytesIO(dng)) as r:
        assert np.array_equal(r.raw_image_visible[:mosaic.shape[0], :mosaic.shape[1]], mosaic)
        assert list(r.black_level_per_channel) == [512] * 4
        assert r.white_level == 16383
        assert r.sizes.crop_width == 1380 and r.sizes.crop_height == 1190
        wb = np.array(r.camera_whitebalance)
        assert wb[0] / wb[1] == pytest.approx(2.0, rel=1e-4) and wb[2] / wb[1] == pytest.approx(1.5, rel=1e-4)


def test_develop_and_jpeg_exif(mraw, tmp_path):
    import tifffile
    _, path = mraw
    out = tmp_path / "x.jpg"
    cli.main(["decode", str(path), str(out), "--half-size"])
    b = out.read_bytes()
    i = b.find(b"Exif\0\0")
    assert b[:2] == b"\xff\xd8" and i > 0
    n = int.from_bytes(b[i - 2:i], "big")
    with tifffile.TiffFile(io.BytesIO(b[i + 6:i - 2 + n])) as tf:
        tags = tf.pages[0].tags
        assert tags["Make"].value == "TEST" and tags["Model"].value == "CAM-1"
        ex = tags["ExifTag"].value
        assert ex["ISOSpeedRatings"] == 400 and tuple(ex["ExposureTime"]) == (1, 250)
        assert ex["LensModel"] == "50mm"


@pytest.mark.parametrize("name,mode", [("x.dng", None), ("x.png", None), ("x.tif", None), ("x.npy", None),
                                       ("p.jpg", "preview"), ("c.png", None)])
def test_cli_outputs(mraw, tmp_path, name, mode):
    img, path = mraw
    out = tmp_path / name
    args = ["decode", str(path), str(out)] + (["--mode", mode] if mode else [])
    if name == "c.png":
        args += ["--region", "100,50,300,200"]
    cli.main(args)
    assert out.stat().st_size > 0
    if name == "x.npy":
        assert np.array_equal(np.load(out), io_raw.unpack(img.planes, img.offsets))


def test_cli_stripped_dng_needs_sidecar_for_exact(mraw, tmp_path):
    img, path = mraw
    hot, l3 = container.strip(path.read_bytes())
    (tmp_path / "s.mraw").write_bytes(hot)
    (tmp_path / "s.mraw.l3").write_bytes(l3)
    with pytest.raises(SystemExit):
        cli.main(["decode", str(tmp_path / "s.mraw"), str(tmp_path / "s.dng"), "--mode", "exact"])
    cli.main(["decode", str(tmp_path / "s.mraw"), str(tmp_path / "e.npy"), "--sidecar", str(tmp_path / "s.mraw.l3")])
    assert np.array_equal(np.load(tmp_path / "e.npy"), io_raw.unpack(img.planes, img.offsets))
