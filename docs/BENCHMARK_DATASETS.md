# Benchmark datasets for MiniRaw

Oct 10, 2026. This is a plan for testing MiniRaw beyond the current two sets: 10 Sony A7 IV files and 20 MIT-Adobe
FiveK files. **Nothing listed here has been downloaded or run yet.** The development machine has about 16 GB of
free disk space, which limits what can be fetched at once.

## Why

- **Generalization.** All current results come from one modern camera plus older (2003-2009) FiveK cameras.
- **Comparability.** Measuring on the same data as published work lets MiniRaw sit next to its numbers. In
  particular, RAWIC, a learned lossless raw codec, reports an average 7.7% bitrate reduction over JPEG XL.

## Options

| Dataset | Contents | Use for MiniRaw | Access and size |
| --- | --- | --- | --- |
| **NUS-8** (Cheng et al., colour constancy) | About 1,736 raw photos from 8 cameras, plus a ninth (Nikon D40) | **Head-to-head with RAWIC**, which evaluates on 5 of these cameras | See "NUS-8 details". Roughly 4 GB or more per camera. |
| **RAISE** | 8,156 Nikon NEFs (D7000, D90, D40) | RAWIC's other evaluation set, and the original spec's main benchmark | Registration form (name, affiliation, email); non-commercial research only. Being requested by the owner. |
| **MIT-Adobe FiveK** | 5,000 DNGs from many cameras, mostly from around 2011 | Larger cross-camera runs. A 20-image dev subset is already in use. | Direct HTTPS downloads, about 5-25 MB per file. Some files were already lossy-compressed in camera. |
| **RawNIND** | The same scenes shot at several ISOs on several cameras | How the savings scale with ISO and noise | Hosting and terms to be confirmed. Some cameras use X-Trans, which MiniRaw skips. |
| **SID** (See-in-the-Dark) | Sony A7S II ARW (Bayer) and Fuji X-T2 (X-Trans), extreme low light | High-ISO stress test, and a second Sony model for byte-exact ARW restore | The Sony part is a single archive of about 25 GB. Too large for the current free space. |
| **SIDD** | Smartphone raws from 5 phones | Small, noisy phone sensors | Phone raws may be pre-processed. |
| **Google HDR+ burst** | DNG bursts from Pixel and Nexus phones (CC-BY-SA) | Modern phone sensors | Very large; use a subset. |
| **raw.pixls.us** | Community archive of 1-2 sample raws for almost every camera model (CC0) | **Compatibility**: which formats restore byte for byte, and which fall back to DNG | Small per file; not a statistical benchmark. |

## NUS-8 details

Official page: https://yorkucvil.github.io/projects/public_html/illuminant/illuminant.html

**Cameras.**
- All 8: Canon EOS-1Ds Mark III, Canon EOS 600D, Fujifilm X-M1, Nikon D5200, Olympus E-PL6, Panasonic Lumix
  DMC-GX1, Samsung NX2000, Sony SLT-A57. The page also lists a Nikon D40 set.
- RAWIC uses five: Canon 1Ds Mark III, Canon 600D, Olympus E-PL6, Panasonic GX1, Samsung NX2000.
- The Fujifilm X-M1 is X-Trans, which MiniRaw doesn't support.

**Formats offered:** camera RAW, DCRAW-converted linear PNG (`dcraw -D -4 -T`, no demosaicing), camera JPEG, masks,
and ground truth (.mat with dark levels).

**Official download: can't be scripted.**
- Each camera's RAW set is a split zip in five parts (e.g. `Canon1DsMkIII_RAW.zip.001` to `.005`) on sync.com.
- Sync share links are end-to-end encrypted and decrypted by JavaScript in the browser, so a script can't fetch
  them. They have to be downloaded in a browser.
- A split zip keeps its file index in the last part, so all five parts of a camera are needed before anything can
  be extracted.

**Hugging Face mirror: scriptable, but not original raws.**
- Two mirrors: `StevenChangWei/nus8-dataset` and `CreanAlence/nus8-dataset`, which look identical. They are
  labelled Apache-2.0; whether the mirror is entitled to relicense the data is unverified.
- They contain PNG files (e.g. `images/Canon1DsMkIII_0001.PNG`, about 19-24 MB each) plus masks and
  `all_cameras_meta.json`.
- The first listing page alone shows 1,000 PNGs totalling about 18.6 GB, so the full mirror is larger than the
  free disk.
- These are almost certainly the DCRAW-converted PNGs, not the camera's raw files. Before using them, check that
  each PNG is the full-resolution Bayer mosaic (not downsampled), and get each camera's CFA pattern, black level
  and white level from the metadata.
- With PNG input, MiniRaw's sensor-exact compression can be measured. Restoring the original camera file can't,
  because the original container isn't there.

**Disk plan.** Process one camera at a time: download, run `bench.run --dir`, keep the CSVs, delete the images.
The runner is resumable and keeps per-image CSVs, so this works within about 5 GB of space per camera.

## RAWIC

- Code: https://github.com/chunbaobao/RAWIC. Paper: https://arxiv.org/abs/2603.28105.
- It trains and evaluates on NUS-8 (the five cameras above) and RAISE. Its preprocessing scripts are
  `cal_dataset_pure.py` and `cal_dataset_raise.py`.
- Pretrained weights are published (PKU Disk, Baidu NetDisk, Google Drive), with MD5 checksums.
- Its arithmetic coder (`arithmetic_v2`) **needs CUDA**, so RAWIC can't run on the Apple M1 without porting.
- **Plan:**
  - Compare against RAWIC's *published* numbers.
  - Measure JPEG XL on the same sensor data here, so all three are related through a shared baseline.
  - Running RAWIC itself would need an NVIDIA GPU machine.
- For a valid comparison, match RAWIC's preprocessing (crop, bit depth, black-level handling) and its test split.
  Read its scripts before measuring.

## Recommended order

1. **NUS-8, RAWIC's five cameras**, one camera at a time. Either:
   - download the official RAW parts in a browser (preferred, since it keeps the original raw files), or
   - use the Hugging Face PNGs after checking they are full mosaics.
2. **RAISE**, once registration is approved. Also one batch at a time; NEFs are about 10-20 MB each.
3. **A RawNIND subset**, for ISO scaling.
4. **raw.pixls.us**, one file per camera, as a compatibility check.

To report for each dataset:
- Per-camera median sizes for every layer, sensor-exact and file-exact (where originals exist).
- JPEG XL lossless and JPEG-LS on the same data.
- Encode and decode time.
- Gate verdicts.
- Hash verification of every exact decode.

## Running a dataset

```sh
python -m bench.run --dir PATH/TO/CAMERA_FOLDER --quick --workers 3   # per-image CSVs, resumable
python -m bench.report --split CAMERA_FOLDER_NAME --out results/report_CAMERA.md
```

PNG mosaics (the Hugging Face mirror) would need a small loader that supplies the CFA pattern and black and white
levels, because `io_raw.read` expects a camera raw file.
