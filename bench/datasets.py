"""FiveK download and dev/test split lists (seed 0).

The dev split is camera-stratified: one image per camera model for the most
common models. Lists are committed under bench/splits/.
"""
import argparse
import json
import random
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
META = ROOT / "data" / "meta"
RAW = ROOT / "data" / "raw" / "fivek"
SPLITS = Path(__file__).resolve().parent / "splits"
HF = "https://huggingface.co/datasets/yangkun2197/MIT-Adobe-FiveK/resolve/main/"


def load_meta():
    META.mkdir(parents=True, exist_ok=True)
    d = {}
    for name in ("training", "validation", "testing"):
        p = META / f"{name}.json"
        if not p.exists():
            urllib.request.urlretrieve(HF + p.name, p)
        d.update(json.loads(p.read_text()))
    return d


def make_splits(n_dev=20, n_test=60, n_holdout=60, seed=0):
    meta = load_meta()
    by_cam = defaultdict(list)
    for k in sorted(meta):
        cam = meta[k]["camera"]
        by_cam[f"{cam['make']} {cam['model']}"].append(k)
    rng = random.Random(seed)
    cams = sorted(by_cam, key=lambda c: (-len(by_cam[c]), c))
    for c in cams:
        rng.shuffle(by_cam[c])
    dev = [by_cam[c][0] for c in cams[:n_dev]]
    # Test: round-robin over cameras, skipping anything used in dev.
    test, i = [], 1
    while len(test) < n_test:
        for c in cams[:n_dev]:
            if i < len(by_cam[c]) and len(test) < n_test:
                test.append(by_cam[c][i])
        i += 1
    # Holdout: the same round-robin continued, added after the test split had been looked at (D26).
    holdout, j = [], i
    while len(holdout) < n_holdout:
        for c in cams[:n_dev]:
            k = by_cam[c][j] if j < len(by_cam[c]) else None
            if k and k not in test and len(holdout) < n_holdout:
                holdout.append(k)
        j += 1
    assert not set(dev) & set(test) and not (set(dev) | set(test)) & set(holdout)
    SPLITS.mkdir(exist_ok=True)
    (SPLITS / "dev.txt").write_text("\n".join(dev) + "\n")
    (SPLITS / "test.txt").write_text("\n".join(test) + "\n")
    (SPLITS / "holdout.txt").write_text("\n".join(holdout) + "\n")
    return dev, test, holdout


def _used(exclude=None):
    names = exclude or [f.stem for f in SPLITS.glob("*.txt")]
    return set().union(*(set(read_split(n)) for n in names))


def make_holdout_split(name, n=60, seed=0):
    """A fresh held-out split: the dev cameras' round-robin, skipping every image in an existing split."""
    meta = load_meta()
    used = _used()
    by_cam = defaultdict(list)
    for k in sorted(meta):
        cam = meta[k]["camera"]
        by_cam[f"{cam['make']} {cam['model']}"].append(k)
    rng = random.Random(seed)
    cams = sorted(by_cam, key=lambda c: (-len(by_cam[c]), c))
    for c in cams:
        rng.shuffle(by_cam[c])            # same order as make_splits
    pools = {c: [k for k in by_cam[c] if k not in used] for c in cams[:20]}
    out, i = [], 0
    while len(out) < n and any(i < len(v) for v in pools.values()):
        for c in cams[:20]:
            if i < len(pools[c]) and len(out) < n:
                out.append(pools[c][i])
        i += 1
    (SPLITS / f"{name}.txt").write_text("\n".join(out) + "\n")
    return out


def make_index_split(n=500, seed=1, name="index500"):
    """index500: images in no other split, stratified by FiveK subject in proportion (index benchmark)."""
    meta = load_meta()
    used = _used(["dev", "test", "holdout"]) if name == "index500" else _used()
    by_subj = defaultdict(list)
    for k in sorted(meta):
        if k not in used:
            by_subj[meta[k]["categories"].get("subject", "unknown")].append(k)
    rng = random.Random(seed)
    total = sum(len(v) for v in by_subj.values())
    picked, rest = [], []
    for subj in sorted(by_subj):
        keys = by_subj[subj]
        rng.shuffle(keys)
        m = round(n * len(keys) / total)
        picked += keys[:m]
        rest += [(-len(keys), i, k) for i, k in enumerate(keys[m:])]
    picked += [k for _, _, k in sorted(rest)[: max(n - len(picked), 0)]]   # rounding: top up from the largest
    picked = sorted(picked)[:n]
    (SPLITS / f"{name}.txt").write_text("\n".join(picked) + "\n")
    return picked


def read_split(name):
    return [l.strip() for l in (SPLITS / f"{name}.txt").read_text().splitlines() if l.strip()]


def path_for(key):
    return RAW / f"{key}.dng"


def download(keys, workers=4):
    meta = load_meta()
    RAW.mkdir(parents=True, exist_ok=True)

    def get(k):
        p = path_for(k)
        if p.exists():
            return k, p.stat().st_size
        url = urllib.parse.quote(meta[k]["urls"]["dng"].replace("http://", "https://"), safe=":/")
        tmp = p.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.rename(p)
        return k, p.stat().st_size

    with ThreadPoolExecutor(workers) as ex:
        for k, size in ex.map(get, keys):
            print(f"{size / 1e6:7.1f} MB  {k}")


def camera_of(key):
    cam = load_meta()[key]["camera"]
    return f"{cam['make']} {cam['model']}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--make-splits", action="store_true")
    a = ap.parse_args()
    if a.make_splits or not (SPLITS / "dev.txt").exists():
        make_splits()
    if a.split == "index500" and not (SPLITS / "index500.txt").exists():
        make_index_split()
    if a.split == "index500b" and not (SPLITS / "index500b.txt").exists():
        make_index_split(seed=2, name="index500b")
    if a.split == "holdout2" and not (SPLITS / "holdout2.txt").exists():
        make_holdout_split("holdout2")
    download(read_split(a.split))
