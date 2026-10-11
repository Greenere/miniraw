"""miniraw command line.

    miniraw encode IN.dng OUT.mraw [--step 1.0] [--no-l3]
    miniraw decode IN.mraw OUT.npy --mode index|preview|signal|exact [--region x,y,w,h] [--dither] [--sidecar F]
    miniraw info IN.mraw
    miniraw strip IN.mraw
    miniraw split IN.mraw
"""
import argparse
import json
from pathlib import Path

import numpy as np

from . import codec, container, io_raw


def main(argv=None):
    ap = argparse.ArgumentParser(prog="miniraw")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("encode")
    e.add_argument("src")
    e.add_argument("dst")
    e.add_argument("--step", type=float, default=1.0)
    e.add_argument("--no-l3", action="store_true")
    d = sub.add_parser("decode")
    d.add_argument("src")
    d.add_argument("dst")
    d.add_argument("--mode", default="signal", choices=["index", "preview", "signal", "exact"])
    d.add_argument("--region")
    d.add_argument("--dither", action="store_true")
    d.add_argument("--sidecar")
    for name in ("info", "strip", "split"):
        sub.add_parser(name).add_argument("src")
    a = ap.parse_args(argv)

    if a.cmd == "encode":
        raw = io_raw.read(a.src)
        data, _ = codec.encode(raw, step=a.step, with_l3=not a.no_l3,
                               source={"file": Path(a.src).name})
        Path(a.dst).write_bytes(data)
        _print_info(data)
    elif a.cmd == "decode":
        region = tuple(int(v) for v in a.region.split(",")) if a.region else None
        out = codec.decode(a.src, a.mode, region, a.dither, sidecar=a.sidecar)
        if a.mode == "index":
            Path(a.dst).write_text(json.dumps(_jsonable(out), indent=1))
        else:
            np.save(a.dst, out)
    elif a.cmd == "info":
        _print_info(Path(a.src).read_bytes())
    elif a.cmd in ("strip", "split"):
        data = Path(a.src).read_bytes()
        hot, l3 = container.strip(data)
        if a.cmd == "split":
            Path(a.src + ".l3").write_bytes(l3)
        Path(a.src).write_bytes(hot)
        print(f"hot {len(hot)} B, L3 {len(l3)} B" + (" -> " + a.src + ".l3" if a.cmd == "split" else " dropped"))


def _print_info(data):
    r = container.RangeReader(data)
    hd = r.header
    s = hd["sensor"]
    npx = s["width"] * s["height"]
    sizes = container.layer_sizes(hd)
    present = sum(sizes.values()) - (0 if r.flags & container.FLAG_L3 else sizes["L3"])
    sizes["L0"] = len(data) - present
    print(f"{s['width']}x{s['height']}  bit depth {s['bit_depth']}  step {hd['noise']['step']}  "
          f"k {hd['layout']['k']}  tile {hd['layout']['tile']}  L3 {'present' if r.flags & 1 else 'stripped'}")
    for k in ("L0", "L1", "L2", "L3"):
        print(f"  {k}: {sizes[k]:>10} B  {8 * sizes[k] / npx:6.3f} bpp")
    for i, m in enumerate(hd["noise"]["planes"]):
        print(f"  plane {io_raw.PLANE_NAMES[i]}: {m['type']}" +
              (f" a={m['a']:.3f} c={m['c']:.2f}" if m["type"] == "affine" else ""))


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items() if k != "tables"}
    if isinstance(o, list):
        return [_jsonable(v) for v in o]
    if isinstance(o, bytes):
        return f"<{len(o)} bytes>"
    return o


if __name__ == "__main__":
    main()
