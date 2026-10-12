"""bundle / unbundle round trips."""
import hashlib
import io
import os
import zipfile

import imagecodecs
import numpy as np
import pytest

from miniraw import bundle, cli, codec, container, export, io_raw

rawpy = pytest.importorskip("rawpy")


@pytest.fixture()
def folder(synth_image, tmp_path):
    src = tmp_path / "shoot"
    (src / "day2").mkdir(parents=True)
    hd = {"sensor": {"cfa": [list(o) for o in synth_image.offsets], "black": synth_image.black,
                     "white": synth_image.white, "wb": [2.0, 1.0, 1.0, 1.5], "bit_depth": 14,
                     "cam_xyz": [[0.75, -0.24, -0.06], [-0.57, 1.34, 0.25], [-0.06, 0.12, 0.66]]},
          "source": {"exif": {"Make": "TEST", "Model": "CAM-1"}}}
    mosaic = io_raw.unpack(synth_image.planes, synth_image.offsets)
    (src / "IMG_0001.dng").write_bytes(export.dng_bytes(mosaic, hd, compress=True))
    rng = np.random.default_rng(0)
    rgb = np.clip(np.cumsum(rng.normal(0, 3, (240, 320, 3)), axis=1) + 128, 0, 255).astype(np.uint8)
    (src / "day2" / "photo.jpg").write_bytes(bytes(imagecodecs.jpeg8_encode(rgb, level=90)))
    (src / "day2" / "graphic.png").write_bytes(bytes(imagecodecs.png_encode(rgb)))
    (src / "day2" / "IMG_0001.xmp").write_text("<x:xmpmeta>rating=5</x:xmpmeta>")
    (src / "broken.ARW").write_bytes(b"not really a raw file" * 100)
    (src / ".DS_Store").write_bytes(b"junk")
    os.utime(src / "day2" / "photo.jpg", (1_600_000_000, 1_600_000_000))
    return src, mosaic


def out_member(zip_path, name):
    with zipfile.ZipFile(zip_path) as zf:
        return zf.read(name)


def _hash(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.parametrize("cold", ["keep", "split", "drop"])
def test_roundtrip(folder, tmp_path, cold):
    src, mosaic = folder
    out = tmp_path / "shoot.zip"
    m = bundle.bundle(src, out, effort=3, cold=cold, log=lambda *a: None)
    kinds = {e["path"]: e["kind"] for e in m["files"]}
    assert kinds == {"IMG_0001.dng": "raw", "broken.ARW": "file", "day2/IMG_0001.xmp": "file",
                     "day2/graphic.png": "file", "day2/photo.jpg": "jpeg-jxl"}   # .DS_Store skipped
    assert (tmp_path / "shoot.l3.zip").exists() == (cold == "split")

    restored = tmp_path / "restored"
    stats = bundle.unbundle(out, restored, log=lambda *a: None)
    for rel in ["broken.ARW", "day2/IMG_0001.xmp", "day2/graphic.png", "day2/photo.jpg"]:
        assert _hash(restored / rel) == _hash(src / rel), rel
    assert os.path.getmtime(restored / "day2" / "photo.jpg") == 1_600_000_000
    # IMG_0001.dng comes back byte for byte unless L3 was dropped; then it is a near-lossless DNG
    if cold != "drop":
        assert _hash(restored / "IMG_0001.dng") == _hash(src / "IMG_0001.dng")
        assert stats["raw_original"] == 1
    else:   # no L3: a valid file in the original format holding the near-lossless pixels
        near = codec.decode(out_member(out, "IMG_0001.dng.mraw"), "signal")
        with rawpy.imread(str(restored / "IMG_0001.dng")) as r:
            got = r.raw_image_visible[:mosaic.shape[0], :mosaic.shape[1]]
            assert np.array_equal(got, near) and not np.array_equal(got, mosaic)
        assert stats["raw_near_lossless"] == 1


def test_split_without_l3_falls_back_to_signal(folder, tmp_path):
    src, _ = folder
    out = tmp_path / "b.zip"
    bundle.bundle(src, out, effort=3, cold="split", log=lambda *a: None)
    (tmp_path / "b.l3.zip").rename(tmp_path / "elsewhere.zip")
    stats = bundle.unbundle(out, tmp_path / "r1", log=lambda *a: None)
    assert stats["raw_near_lossless"] == 1 and stats["raw_original"] == 0
    stats = bundle.unbundle(out, tmp_path / "r2", l3=tmp_path / "elsewhere.zip", log=lambda *a: None)
    assert stats["raw_original"] == 1
    assert _hash(tmp_path / "r2" / "IMG_0001.dng") == _hash(src / "IMG_0001.dng")
    stats = bundle.unbundle(out, tmp_path / "r3", raw_as="dng", l3=tmp_path / "elsewhere.zip", log=lambda *a: None)
    assert stats["raw_exact"] == 1


def test_cli_bundle_list_and_raw_as(folder, tmp_path, capsys):
    src, _ = folder
    out = tmp_path / "c.zip"
    cli.main(["bundle", str(src), str(out), "--effort", "3"])
    cli.main(["unbundle", str(out), "--list"])
    assert "day2/photo.jpg" in capsys.readouterr().out
    cli.main(["unbundle", str(out), str(tmp_path / "jpgs"), "--raw-as", "jpg"])
    assert (tmp_path / "jpgs" / "IMG_0001.jpg").stat().st_size > 0
    cli.main(["unbundle", str(out), str(tmp_path / "mr"), "--raw-as", "mraw"])
    assert container.RangeReader(tmp_path / "mr" / "IMG_0001.dng.mraw").has_l3


def test_png_repack_is_pixel_identical_and_skips_metadata_pngs(folder, tmp_path):
    src, _ = folder
    rgb = imagecodecs.png_decode((src / "day2" / "graphic.png").read_bytes())
    tagged = bytearray(imagecodecs.png_encode(rgb))
    chunk = b"tEXt" + b"Comment\x00hello"
    import zlib
    tagged[33:33] = len(chunk[4:]).to_bytes(4, "big") + chunk + zlib.crc32(chunk).to_bytes(4, "big")
    (src / "day2" / "tagged.png").write_bytes(bytes(tagged))
    out = tmp_path / "p.zip"
    m = bundle.bundle(src, out, effort=3, png="repack", log=lambda *a: None)
    kinds = {e["path"]: e["kind"] for e in m["files"]}
    assert kinds["day2/graphic.png"] == "png-jxl" and kinds["day2/tagged.png"] == "file"
    bundle.unbundle(out, tmp_path / "r", log=lambda *a: None)
    assert np.array_equal(imagecodecs.png_decode((tmp_path / "r" / "day2" / "graphic.png").read_bytes()), rgb)
    assert _hash(tmp_path / "r" / "day2" / "tagged.png") == _hash(src / "day2" / "tagged.png")


def test_cli_bundle_encode_flags_take_effect(folder, tmp_path):
    src, _ = folder
    cli.main(["bundle", str(src), str(tmp_path / "a.zip"), "--effort", "3", "--threads", "1"])
    cli.main(["bundle", str(src), str(tmp_path / "b.zip"), "--effort", "3", "--step", "2", "--no-l3"])
    with zipfile.ZipFile(tmp_path / "a.zip") as za, zipfile.ZipFile(tmp_path / "b.zip") as zb:
        ma, mb = bundle.read_manifest(za), bundle.read_manifest(zb)
        assert ma["settings"]["cold"] == "keep" and mb["settings"]["cold"] == "drop"
        assert mb["settings"]["step"] == 2.0
        ra = container.RangeReader(za.read("IMG_0001.dng.mraw"))
        rb = container.RangeReader(zb.read("IMG_0001.dng.mraw"))
    assert ra.has_l3 and not rb.has_l3
    assert ra.header["noise"]["step"] == 1.0 and rb.header["noise"]["step"] == 2.0
    with pytest.raises(SystemExit):
        cli.main(["bundle", str(src), str(tmp_path / "c.zip"), "--no-l3", "--cold", "split"])


def test_rejects_path_traversal(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("x", b"hi")
        zf.writestr(bundle.MANIFEST, '{"format": "%s", "files": [{"path": "../evil", "member": "x", '
                    '"kind": "file", "sha256": "", "mtime": 0, "size": 2}]}' % bundle.FORMAT)
    with pytest.raises(ValueError, match="unsafe path"):
        bundle.unbundle(bad, tmp_path / "out", log=lambda *a: None)


@pytest.mark.parametrize("cold", ["keep", "split", "drop"])
def test_raw_that_would_grow_is_stored_as_is(folder, tmp_path, monkeypatch, cold):
    """Raws already compressed lossily in camera can grow as .mraw; the bundle then keeps the original file.

    The padding below lands after the exact layer, so with --cold split the working copy in the main zip stays
    small and the raw is still converted, as it should be.
    """
    src, _ = folder
    real = codec.encode

    def bloated(*a, **k):
        data, info = real(*a, **k)
        return data + bytes(len(data) * 20), info        # trailing bytes: still a readable container, but large
    monkeypatch.setattr(bundle.codec, "encode", bloated)
    out = tmp_path / "g.zip"
    m = bundle.bundle(src, out, effort=3, cold=cold, log=lambda *a: None)
    e = next(e for e in m["files"] if e["path"] == "IMG_0001.dng")
    if cold == "split":
        assert e["kind"] == "raw" and e["stored_size"] < e["size"]
    else:
        assert e["kind"] == "file" and e["stored_size"] == e["size"]
    bundle.unbundle(out, tmp_path / "r", log=lambda *a: None)
    assert _hash(tmp_path / "r" / "IMG_0001.dng") == _hash(src / "IMG_0001.dng")
