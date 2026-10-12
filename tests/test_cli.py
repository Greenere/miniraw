"""Command-line safety and error messages: no silent overwrites, one-line errors, relative paths."""
import hashlib

import pytest

from miniraw import cli, export, io_raw

pytest.importorskip("rawpy")


@pytest.fixture()
def shoot(synth_image, tmp_path):
    """A folder with one raw (DNG) and a text file."""
    src = tmp_path / "shoot"
    src.mkdir()
    hd = {"sensor": {"cfa": [list(o) for o in synth_image.offsets], "black": synth_image.black,
                     "white": synth_image.white, "wb": [2.0, 1.0, 1.0, 1.5], "bit_depth": 14,
                     "cam_xyz": [[0.75, -0.24, -0.06], [-0.57, 1.34, 0.25], [-0.06, 0.12, 0.66]]},
          "source": {"exif": {"Make": "TEST", "Model": "CAM-1"}}}
    mosaic = io_raw.unpack(synth_image.planes, synth_image.offsets)
    (src / "IMG_0001.dng").write_bytes(export.dng_bytes(mosaic, hd, compress=True))
    (src / "notes.txt").write_text("hello")
    return src


def _hash(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _fails(argv):
    """Run the CLI, expect a clean exit with a message; return the message."""
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert isinstance(e.value.code, str), e.value.code
    return e.value.code


def test_unbundle_to_a_relative_folder(shoot, tmp_path, monkeypatch):
    """The README's `unbundle trip.zip restored/` crashed after the first file with a relative path."""
    monkeypatch.chdir(tmp_path)
    cli.main(["bundle", "shoot", "trip.zip", "--effort", "3"])
    cli.main(["unbundle", "trip.zip", "restored/"])
    assert _hash(tmp_path / "restored" / "IMG_0001.dng") == _hash(shoot / "IMG_0001.dng")


def test_unbundle_refuses_to_replace_files(shoot, tmp_path):
    cli.main(["bundle", str(shoot), str(tmp_path / "b.zip"), "--effort", "3", "--cold", "drop"])
    # unbundling a near-lossless bundle over the source folder must not replace the originals
    before = _hash(shoot / "IMG_0001.dng")
    msg = _fails(["unbundle", str(tmp_path / "b.zip"), str(shoot)])
    assert "--force" in msg and "IMG_0001.dng" in msg
    assert _hash(shoot / "IMG_0001.dng") == before
    out = tmp_path / "r"
    out.mkdir()
    (out / "notes.txt").write_text("mine")
    _fails(["unbundle", str(tmp_path / "b.zip"), str(out)])
    assert sorted(p.name for p in out.iterdir()) == ["notes.txt"]          # nothing written
    cli.main(["unbundle", str(tmp_path / "b.zip"), str(out), "--force"])
    assert (out / "notes.txt").read_text() == "hello" and (out / "IMG_0001.dng").exists()


def test_bundle_refuses_to_replace_a_zip(shoot, tmp_path):
    cli.main(["bundle", str(shoot), str(tmp_path / "b.zip"), "--effort", "3"])
    assert "--force" in _fails(["bundle", str(shoot), str(tmp_path / "b.zip"), "--effort", "3"])
    cli.main(["bundle", str(shoot), str(tmp_path / "b.zip"), "--effort", "3", "--force"])


def test_encode_and_decode_refuse_to_replace_files(shoot, tmp_path):
    raw, mr = shoot / "IMG_0001.dng", tmp_path / "a.mraw"
    cli.main(["encode", str(raw), str(mr), "--effort", "3"])
    assert "--force" in _fails(["encode", str(raw), str(mr), "--effort", "3"])
    cli.main(["encode", str(raw), str(mr), "--effort", "3", "--no-l3", "--force"])
    # decoding a stripped file onto the original raw would replace it with the near-lossless version
    before = _hash(raw)
    assert "--force" in _fails(["decode", str(mr), str(raw)])
    assert _hash(raw) == before
    cli.main(["decode", str(mr), str(tmp_path / "out.dng")])
    assert "--force" in _fails(["decode", str(mr), str(tmp_path / "out.dng")])


def test_split_refuses_to_replace_a_sidecar(shoot, tmp_path):
    mr = tmp_path / "a.mraw"
    cli.main(["encode", str(shoot / "IMG_0001.dng"), str(mr), "--effort", "3"])
    (tmp_path / "a.mraw.l3").write_bytes(b"someone else's sidecar")
    assert "--force" in _fails(["split", str(mr)])
    assert (tmp_path / "a.mraw.l3").read_bytes() == b"someone else's sidecar"


def test_one_line_errors(shoot, tmp_path):
    mr = tmp_path / "a.mraw"
    cli.main(["encode", str(shoot / "IMG_0001.dng"), str(mr), "--effort", "3"])
    (tmp_path / "trunc.mraw").write_bytes(mr.read_bytes()[: len(mr.read_bytes()) // 2])
    (tmp_path / "empty.mraw").write_bytes(b"")
    cases = [
        (["encode", str(tmp_path / "nothere.ARW"), str(tmp_path / "x.mraw")], "no such file"),
        (["encode", str(shoot / "notes.txt"), str(tmp_path / "x.mraw")], "not a raw file LibRaw can read"),
        (["decode", str(shoot / "IMG_0001.dng"), str(tmp_path / "x.jpg")], "not a .mraw file"),
        (["decode", str(tmp_path / "trunc.mraw"), str(tmp_path / "x.dng")], "damaged or truncated"),
        (["info", str(tmp_path / "empty.mraw")], "not a .mraw file"),
        (["info", str(tmp_path / "nothere.mraw")], "no such file"),
        (["bundle", str(tmp_path / "nothere"), str(tmp_path / "x.zip")], "not a folder"),
        (["unbundle", str(tmp_path / "nothere.zip"), str(tmp_path / "r")], "no such file"),
    ]
    for argv, expected in cases:
        msg = _fails(argv)
        assert expected in msg and "Traceback" not in msg, (argv, msg)
        assert msg.startswith(f"miniraw {argv[0]}: "), msg


@pytest.mark.parametrize("step", ["0", "-1", "nan"])
def test_step_must_be_positive(shoot, tmp_path, capsys, step):
    with pytest.raises(SystemExit) as e:
        cli.main(["encode", str(shoot / "IMG_0001.dng"), str(tmp_path / "x.mraw"), "--step", step])
    assert e.value.code == 2 and "greater than 0" in capsys.readouterr().err


def test_split_refuses_a_file_without_an_exact_layer(shoot, tmp_path):
    mr = tmp_path / "a.mraw"
    cli.main(["encode", str(shoot / "IMG_0001.dng"), str(mr), "--effort", "3", "--no-l3"])
    assert "no exact layer" in _fails(["split", str(mr)])
    assert not (tmp_path / "a.mraw.l3").exists()


def test_unbundle_summary_says_how_each_file_came_back(shoot, tmp_path, capsys):
    cli.main(["bundle", str(shoot), str(tmp_path / "d.zip"), "--effort", "3", "--cold", "drop"])
    capsys.readouterr()
    cli.main(["unbundle", str(tmp_path / "d.zip"), str(tmp_path / "r")])
    out = capsys.readouterr().out
    assert "1 identical to the original" in out                     # notes.txt
    assert "near-lossless pixels (the bundle was made with --cold drop)" in out
    assert "verified against the original hashes" not in out
