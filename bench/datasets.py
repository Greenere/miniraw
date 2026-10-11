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


def make_splits(n_dev=20, n_test=60, seed=0):
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
    assert not set(dev) & set(test)
    SPLITS.mkdir(exist_ok=True)
    (SPLITS / "dev.txt").write_text("\n".join(dev) + "\n")
    (SPLITS / "test.txt").write_text("\n".join(test) + "\n")
    return dev, test


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
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--make-splits", action="store_true")
    a = ap.parse_args()
    if a.make_splits or not (SPLITS / "dev.txt").exists():
        make_splits()
    download(read_split(a.split))
