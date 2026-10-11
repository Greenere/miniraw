"""Production-path measurements: wall time, peak memory and bytes read from disk.

Each path runs in a fresh Python process (so peak RSS is per path) on one raw file. Bytes read are
counted by wrapping the OS-level file reads (io.FileIO.read / readinto and os.read / os.pread), so a
whole-file read cannot hide behind in-memory slicing.

    python -m bench.experiments.prod_paths data/personal_raws/DSC04565.ARW [--out results/experiments/prod_paths.json]
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PRELUDE = r'''
import builtins, io, os, resource, sys, time, json
sys.path.insert(0, %(src)r)
READ = {"bytes": 0, "calls": 0, "paths": {}}
_watch = %(watch)r

def _count(name, n):
    if name and any(name.endswith(w) for w in _watch):
        READ["bytes"] += n
        READ["calls"] += 1
        READ["paths"][os.path.basename(name)] = READ["paths"].get(os.path.basename(name), 0) + n

class _Counting:
    """File object proxy that counts bytes returned by read-type calls."""
    def __init__(self, f, name):
        self._f, self._name = f, name
    def __getattr__(self, k):
        return getattr(self._f, k)
    def __enter__(self):
        self._f.__enter__(); return self
    def __exit__(self, *a):
        return self._f.__exit__(*a)
    def __iter__(self):
        return iter(self._f)
    def read(self, *a):
        b = self._f.read(*a); _count(self._name, len(b) if b else 0); return b
    def read1(self, *a):
        b = self._f.read1(*a); _count(self._name, len(b) if b else 0); return b
    def readinto(self, buf):
        n = self._f.readinto(buf); _count(self._name, n or 0); return n
    def readinto1(self, buf):
        n = self._f.readinto1(buf); _count(self._name, n or 0); return n

_orig_open = builtins.open
def _open(file, mode="r", *a, **k):
    f = _orig_open(file, mode, *a, **k)
    name = os.fspath(file) if isinstance(file, (str, bytes, os.PathLike)) else ""
    return _Counting(f, name) if "r" in mode and "b" in mode else f
builtins.open = io.open = _open
_fdnames = {}
_orig_osopen = os.open
def _osopen(path, *a, **k):
    fd = _orig_osopen(path, *a, **k); _fdnames[fd] = os.fspath(path); return fd
os.open = _osopen
_orig_pread = os.pread
def _pread(fd, n, off):
    b = _orig_pread(fd, n, off); _count(_fdnames.get(fd, ""), len(b)); return b
os.pread = _pread

import numpy as np
from miniraw import codec, cli, container, io_raw
t0 = time.perf_counter()
'''

EPILOGUE = r'''
dt = time.perf_counter() - t0
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
rss = rss if sys.platform == "darwin" else rss * 1024
print("RESULT " + json.dumps({"wall_s": dt, "peak_rss_mb": rss / 1e6, "read_bytes": READ["bytes"],
                              "read_calls": READ["calls"], "read_by_file": READ["paths"]}))
'''


def run(code, watch, cwd):
    script = PRELUDE % {"src": str(ROOT / "src"), "watch": watch} + code + EPILOGUE
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=cwd)
    for line in out.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    raise RuntimeError(out.stderr[-2000:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw")
    ap.add_argument("--out")
    a = ap.parse_args()
    raw = str(Path(a.raw).resolve())
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        # Prepare the inputs for the decode paths (not measured)
        subprocess.run([sys.executable, "-m", "miniraw.cli", "encode", raw, str(t / "full.mraw")], check=True,
                       capture_output=True, cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src")})
        subprocess.run([sys.executable, "-m", "miniraw.cli", "encode", raw, str(t / "hot.mraw"), "--no-l3"],
                       check=True, capture_output=True, cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src")})
        sizes = {"full.mraw": (t / "full.mraw").stat().st_size, "hot.mraw": (t / "hot.mraw").stat().st_size}
        paths = {
            "encode core (API, L3, no original)":
                (f"raw = io_raw.read({raw!r}); data, _ = codec.encode(raw)", [".ARW", ".arw", ".dng"]),
            "encode core (API, no L3)":
                (f"raw = io_raw.read({raw!r}); data, _ = codec.encode(raw, with_l3=False)", [".ARW", ".arw", ".dng"]),
            "CLI encode (keeps original)":
                (f"cli.main(['encode', {raw!r}, {str(t / 'o1.mraw')!r}])", [".ARW", ".arw", ".dng"]),
            "CLI encode --no-l3 (keeps container)":
                (f"cli.main(['encode', {raw!r}, {str(t / 'o2.mraw')!r}, '--no-l3'])", [".ARW", ".arw", ".dng"]),
            "decode signal (full)":
                (f"m = codec.decode({str(t / 'full.mraw')!r}, 'signal')", [".mraw", ".l3"]),
            "decode exact (full)":
                (f"m = codec.decode({str(t / 'full.mraw')!r}, 'exact')", [".mraw", ".l3"]),
            "restore original ARW":
                (f"cli.main(['decode', {str(t / 'full.mraw')!r}, {str(t / 'r.ARW')!r}])", [".mraw", ".l3"]),
            "decode preview (L0+L1)":
                (f"m = codec.decode({str(t / 'full.mraw')!r}, 'preview')", [".mraw", ".l3"]),
            "decode 1024x1024 region (signal)":
                (f"m = codec.decode({str(t / 'full.mraw')!r}, 'signal', region=(3000, 2000, 1024, 1024))",
                 [".mraw", ".l3"]),
            "header only (index)":
                (f"h = codec.decode({str(t / 'full.mraw')!r}, 'index')", [".mraw", ".l3"]),
        }
        results = {}
        for name, (code, watch) in paths.items():
            r = run(code, watch, ROOT)
            results[name] = r
            print(f"{name:40s} {r['wall_s']:6.2f} s  peak {r['peak_rss_mb']:7.0f} MB  read {r['read_bytes'] / 1e6:7.2f} MB "
                  f"in {r['read_calls']} calls", flush=True)
        print(f"(full.mraw is {sizes['full.mraw'] / 1e6:.1f} MB, hot.mraw {sizes['hot.mraw'] / 1e6:.1f} MB)")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"file": raw, "mraw_sizes": sizes, "paths": results}, indent=1))


if __name__ == "__main__":
    main()
