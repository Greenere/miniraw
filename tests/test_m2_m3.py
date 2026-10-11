"""M2 (exact round-trip, error bound) and M3 (container, regions, strip/split)."""
import numpy as np
import pytest

from bench import metrics
from miniraw import codec, container, io_raw


@pytest.fixture(scope="module", params=[("mean", "P1"), ("wavelet", "P1"), ("mean", "P2"),
                                        ("mean", "P3"), ("wavelet", "PG")])
def encoded(synth_image, request):
    l1_mode, packing = request.param
    return codec.encode(synth_image, tile=256, effort=3, l1_mode=l1_mode, packing=packing)


def test_exact_roundtrip(synth_image, encoded):
    data, _ = encoded
    mosaic = io_raw.unpack(synth_image.planes, synth_image.offsets)
    assert np.array_equal(codec.decode(data, "exact"), mosaic)


def test_error_bound_every_pixel(synth_image, encoded):
    data, info = encoded
    dec = codec.Decoder(container.RangeReader(data))
    sig = dec.planes("signal")
    for p in range(4):
        pi = info["planes"][p]
        assert metrics.bound_violations(synth_image.planes[p], pi["T"], pi["fwd"], dec.tables[p][2], 1.0) == 0
        rmse, _ = metrics.stabilized_errors(synth_image.planes[p], sig[p], pi["T"])
        assert rmse <= 0.32


def test_checker_rec_stays_in_bin_and_is_unbiased(encoded):
    data, info = encoded
    dec = codec.Decoder(container.RangeReader(data))
    chk, flo = dec.planes("signal", rec="checker"), dec.planes("signal", rec="floor")
    for p in range(4):
        q = info["planes"][p]["q"]
        assert np.array_equal(info["planes"][p]["fwd"][chk[p]], q)
        lo, n, _ = dec.tables[p]
        centre = lo[q] + (n[q] - 1) / 2
        assert abs(np.mean(chk[p] - centre)) < abs(np.mean(flo[p] - centre)) / 4


def test_dither_stays_in_bin(encoded):
    data, info = encoded
    dec = codec.Decoder(container.RangeReader(data))
    d = dec.planes("signal", dither=True)
    for p in range(4):
        assert np.array_equal(info["planes"][p]["fwd"][d[p]], info["planes"][p]["q"])


def test_decode_path_is_integer(encoded):
    """Pitfall 3: tables and intermediate planes on the decode path are integer typed."""
    data, _ = encoded
    dec = codec.Decoder(container.RangeReader(data))
    for lo, n, rec in dec.tables:
        assert lo.dtype.kind == n.dtype.kind == rec.dtype.kind == "i"
    assert dec.l1(0).dtype.kind == "i"
    assert dec.q_tile(0, (0, 0, 0, 0, 256, 256)).dtype.kind == "i"


@pytest.mark.parametrize("region", [(0, 0, 64, 64), (301, 257, 513, 300), (1, 1, 1399, 1199), (700, 900, 3, 5)])
@pytest.mark.parametrize("mode", ["signal", "exact"])
def test_region_matches_full_and_reads_only_touched(encoded, region, mode):
    data, _ = encoded
    full = codec.decode(data, mode)
    reader = container.RangeReader(data)
    dec = codec.Decoder(reader)
    x, y, w, h = region
    assert np.array_equal(dec.mosaic(mode, region), full[y:y + h, x:x + w])
    assert metrics.region_read_check(reader, dec.layout, dec.pshape, region)


def test_strip_and_split(synth_image, encoded):
    data, _ = encoded
    hot, l3 = container.strip(data)
    assert len(hot) + len(l3) == len(data)
    r = container.RangeReader(hot)
    assert not r.flags & container.FLAG_L3
    assert np.array_equal(codec.decode(hot, "signal"), codec.decode(data, "signal"))
    with pytest.raises(ValueError):
        codec.decode(hot, "exact")
    mosaic = io_raw.unpack(synth_image.planes, synth_image.offsets)
    assert np.array_equal(codec.decode(hot, "exact", sidecar=l3), mosaic)


def test_index_reads_one_range(encoded):
    data, _ = encoded
    r = container.RangeReader(data)
    assert r.log == [(0, container.FIRST_READ)]
    assert r.header["source"]["sha256"]
