"""Selective I/O and tile selection (efficiency changes must not change decoded values)."""
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from miniraw import codec, container, io_raw, layers


@pytest.mark.parametrize("shape,tile", [((600, 700), 256), ((512, 512), 512), ((37, 1000), 128), ((100, 90), None)])
def test_tiles_touching_matches_filtered_grid(shape, tile):
    rng = np.random.default_rng(0)
    rects = [(0, 0, shape[0], shape[1]), (0, 0, 0, 5), (5, 5, 5, 9), (-10, -3, 7, 9), (shape[0] - 1, 0, shape[0] + 50, 3)]
    rects += [tuple(sorted(rng.integers(-5, shape[0] + 5, 2))) + tuple(sorted(rng.integers(-5, shape[1] + 5, 2)))
              for _ in range(50)]
    for y0, x0, y1, x1 in [(r[0], r[2], r[1], r[3]) if len(r) == 4 and i >= 5 else r for i, r in enumerate(rects)]:
        cy0, cx0, cy1, cx1 = max(y0, 0), max(x0, 0), min(y1, shape[0]), min(x1, shape[1])
        expected = [t for t in layers.tile_grid(shape, tile)
                    if cy1 > cy0 and cx1 > cx0 and t[2] < cy1 and t[2] + t[4] > cy0 and t[3] < cx1 and t[3] + t[5] > cx0]
        assert layers.tiles_touching(shape, tile, y0, x0, y1, x1) == expected


@pytest.fixture(scope="module")
def mraw_file(synth_image, tmp_path_factory):
    data, _ = codec.encode(synth_image, tile=256, effort=3)
    p = tmp_path_factory.mktemp("io") / "x.mraw"
    p.write_bytes(data)
    return p, data


def test_file_reader_matches_bytes_reader(mraw_file):
    p, data = mraw_file
    for mode in ("signal", "exact"):
        assert np.array_equal(codec.decode(p, mode), codec.decode(data, mode))


def test_header_and_preview_reads_are_small(mraw_file):
    p, data = mraw_file
    r = container.RangeReader(p)
    assert r.bytes_read() == container.FIRST_READ            # header only
    codec.Decoder(r).preview()
    source = "IDX" if ("IDX", 0, 0, 0) in r.sections else "L1"      # format 2: IDX; format 1: L1
    extra = sum(v[1] for k, v in r.sections.items() if k[0] == source)
    assert r.bytes_read() == container.FIRST_READ + extra     # + the preview source, nothing else
    assert r.bytes_read() < len(data) / 3


def test_region_reads_never_touch_cold_layers(mraw_file):
    p, _ = mraw_file
    r = container.RangeReader(p)
    codec.Decoder(r).mosaic("signal", (10, 10, 40, 40))
    cold = {(v[0], v[1]) for k, v in r.sections.items() if k[0] in container.COLD}
    assert not any((off, n) in cold for off, n in r.log)


def test_concurrent_region_decodes_share_one_reader(mraw_file):
    p, data = mraw_file
    full = codec.decode(data, "exact")
    dec = codec.Decoder(container.RangeReader(p))
    regions = [(x, y, 97, 61) for x in range(0, 1300, 211) for y in range(0, 1100, 173)]
    with ThreadPoolExecutor(8) as ex:
        crops = list(ex.map(lambda rg: dec.mosaic("exact", rg), regions))
    for (x, y, w, h), c in zip(regions, crops):
        assert np.array_equal(c, full[y:y + h, x:x + w])


def test_sidecar_opened_lazily_and_read_selectively(mraw_file, tmp_path):
    p, data = mraw_file
    hot, l3 = container.strip(data)
    (tmp_path / "h.mraw").write_bytes(hot)
    (tmp_path / "h.mraw.l3").write_bytes(l3)
    r = container.RangeReader(tmp_path / "h.mraw", tmp_path / "h.mraw.l3")
    assert not isinstance(r._sidecar, (container._File, container._Bytes))   # not opened yet
    dec = codec.Decoder(r)
    crop = dec.mosaic("exact", (0, 0, 64, 64))
    assert np.array_equal(crop, codec.decode(data, "exact")[:64, :64])
    side = [e for e in r.log if e[0] == "sidecar"]
    assert side and sum(e[2] for e in side) < len(l3)                        # only the touched L3 tiles
