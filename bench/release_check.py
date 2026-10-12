"""Pre-release check: drive an installed `miniraw` command over real raw files and verify every output.

Run it with the Python of the environment under test (for example one that installed the release candidate
from TestPyPI); it uses only that environment's miniraw, rawpy and numpy, not this repository's code.

    /path/to/venv/bin/python bench/release_check.py data/personal_raws data/raw/fivek ... [--work DIR] [--jobs 3]

Per file:
  original    encode, decode to the source extension, compare with cmp (where restorable)
  exact       decode .npy (exact) and compare with what LibRaw reads from the original file
  signal      --no-l3: every pixel within step/2 + one DN of the original in the noise-stabilized domain;
              the near-lossless file in the original format reads back in LibRaw with the same size
  split       split, then decode with --sidecar: exact again
  step 2      smaller working copy than step 1, bound holds
  images      .jpg, preview and a 512 px region decode; info runs
Per folder: bundle with --cold keep / split / drop and unbundle; every file restored as expected.
Prints one line per file and a summary; exits non-zero on any failure.
"""
import argparse
import filecmp
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rawpy

from miniraw import container, vst

RAW_EXT = {".arw", ".dng", ".nef", ".cr2", ".cr3", ".raf", ".orf", ".rw2", ".pef", ".srw"}
BIN = Path(sys.executable).with_name("miniraw")


def run(*args):
    r = subprocess.run([str(BIN), *map(str, args)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"miniraw {' '.join(map(str, args))}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def libraw_mosaic(path):
    with rawpy.imread(str(path)) as r:
        m = np.array(r.raw_image_visible, dtype=np.uint16)
    return m[: m.shape[0] // 2 * 2, : m.shape[1] // 2 * 2]


def bound_excess(orig, dec, mraw):
    """Largest |T(orig) - T(dec)| - (step/2 + one DN) over all pixels, from the file's own noise model."""
    hd = container.RangeReader(mraw).header
    step, worst = hd["noise"]["step"], -np.inf
    for p, (dy, dx) in enumerate(hd["sensor"]["cfa"]):
        T = vst.T_table(hd["noise"]["planes"][p], hd["tables"][p]["xmax"])
        slope = np.abs(np.diff(T))
        one_dn = np.maximum(np.concatenate([slope, [slope[-1]]]), np.concatenate([[slope[0]], slope]))
        o, d = orig[dy::2, dx::2].astype(np.int64), dec[dy::2, dx::2].astype(np.int64)
        worst = max(worst, float(np.max(np.abs(T[o] - T[d]) - (step / 2 + one_dn[o]))))
    return worst


def check_file(src, work):
    w = Path(tempfile.mkdtemp(dir=work))
    res, t0 = {"file": src.name}, time.perf_counter()
    try:
        truth = libraw_mosaic(src)
        mr = w / "a.mraw"
        run("encode", src, mr)
        info = run("info", mr)
        restorable = "byte for byte" in info.split("Details")[0]
        if restorable:
            run("decode", mr, w / f"back{src.suffix}")
            assert filecmp.cmp(src, w / f"back{src.suffix}", shallow=False), "restored original differs"
        res["original"] = "identical" if restorable else "n/a"
        run("decode", mr, w / "exact.npy")
        assert np.array_equal(np.load(w / "exact.npy"), truth), "exact decode differs from LibRaw's data"
        res["exact"] = "ok"
        run("decode", mr, w / "a.jpg")
        run("decode", mr, w / "p.jpg", "--mode", "preview")
        run("decode", mr, w / "r.jpg", "--region", "100,100,512,512")
        run("decode", mr, w / "a.dng")
        assert np.array_equal(libraw_mosaic(w / "a.dng"), truth), "exact DNG differs from LibRaw's data"
        nl = w / "n.mraw"
        run("encode", src, nl, "--no-l3")
        run("decode", nl, w / "sig.npy")
        sig = np.load(w / "sig.npy")
        ex = bound_excess(truth, sig, nl)
        assert ex <= 1e-9, f"bound exceeded by {ex:.4f} sigma"
        res["bound"] = f"{ex:+.3f}"
        if restorable:
            run("decode", nl, w / f"near{src.suffix}")
            assert libraw_mosaic(w / f"near{src.suffix}").shape == truth.shape, "near-lossless raw unreadable"
        sp = w / "s.mraw"
        shutil.copy(mr, sp)
        run("split", sp)
        run("decode", sp, w / "s.npy", "--sidecar", f"{sp}.l3")
        assert np.array_equal(np.load(w / "s.npy"), truth), "split + sidecar decode differs"
        res["split"] = "ok"
        s2 = w / "s2.mraw"
        run("encode", src, s2, "--no-l3", "--step", "2")
        run("decode", s2, w / "s2.npy")
        ex2 = bound_excess(truth, np.load(w / "s2.npy"), s2)
        assert ex2 <= 1e-9 and s2.stat().st_size < nl.stat().st_size, "step 2 bound or size"
        res["sizes MB"] = f"{src.stat().st_size / 1e6:.1f}>{mr.stat().st_size / 1e6:.1f}>{nl.stat().st_size / 1e6:.1f}>{s2.stat().st_size / 1e6:.1f}"
        res["ok"] = True
    except Exception as e:  # noqa: BLE001 - report and continue
        res["ok"], res["error"] = False, str(e)[:300]
    finally:
        shutil.rmtree(w, ignore_errors=True)
    res["s"] = round(time.perf_counter() - t0)
    return res


def check_bundles(folder, work):
    out = []
    raws = {p.name for p in folder.iterdir() if p.suffix.lower() in RAW_EXT}
    for cold in ("keep", "split", "drop"):
        w = Path(tempfile.mkdtemp(dir=work))
        try:
            z, r = w / "b.zip", w / "restored"
            run("bundle", folder, z, "--cold", cold)
            msg = run("unbundle", z, r)
            names = {p.name for p in folder.iterdir() if p.is_file() and p.name != ".DS_Store"}
            got = {p.name for p in r.iterdir()}
            assert names <= got, f"missing after unbundle: {sorted(names - got)[:3]}"
            for n in names:
                same = filecmp.cmp(folder / n, r / n, shallow=False)
                if n not in raws:
                    assert same, f"{n} differs"
                elif cold == "drop":         # near-lossless raw in its original format, or a DNG
                    assert libraw_mosaic(r / n).shape == libraw_mosaic(folder / n).shape, f"{n} unreadable"
                elif not same:               # not restorable byte for byte: must be a DNG with identical sensor data
                    assert "recreated as DNG" in msg, f"{n} differs and was not reported as recreated"
                    assert np.array_equal(libraw_mosaic(r / n), libraw_mosaic(folder / n)), f"{n}: sensor data differs"
            out.append((folder.name, cold, True, f"{z.stat().st_size / 1e6:.0f} MB", ""))
        except Exception as e:  # noqa: BLE001
            out.append((folder.name, cold, False, "", str(e)[:300]))
        finally:
            shutil.rmtree(w, ignore_errors=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--work", default=None, help="scratch folder (default: system temp)")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--no-bundles", action="store_true")
    a = ap.parse_args()
    print(run("--version").strip(), "from", BIN)
    files = sorted(p for f in a.folders for p in Path(f).iterdir() if p.suffix.lower() in RAW_EXT)
    work = Path(a.work or tempfile.gettempdir())
    work.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(a.jobs) as ex:
        results = []
        for r in ex.map(lambda p: check_file(p, work), files):
            results.append(r)
            line = "  ".join(f"{k} {v}" for k, v in r.items() if k not in ("file", "ok", "error"))
            print(f"{'PASS' if r['ok'] else 'FAIL'} {r['file']:40s} {line}" + ("" if r["ok"] else f"\n     {r['error']}"),
                  flush=True)
    bundles = [] if a.no_bundles else [b for f in a.folders for b in check_bundles(Path(f), work)]
    for name, cold, ok, size, err in bundles:
        print(f"{'PASS' if ok else 'FAIL'} bundle {name} --cold {cold} {size} {err}", flush=True)
    nf = sum(not r["ok"] for r in results) + sum(not b[2] for b in bundles)
    print(f"\n{len(results)} files, {len(bundles)} bundle round trips: {nf} failed")
    sys.exit(1 if nf else 0)


if __name__ == "__main__":
    main()
