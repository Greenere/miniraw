# D31 verification on a second Mac

> **Done (October 11, 2026).** Run on the Mac that holds the FiveK images; every pass condition held. Results in
> [results/p0_benchmark.md](../results/p0_benchmark.md), section 3, and the D31 entry in `results/DEVIATIONS.md`.

D31 changes how `vst.forward_table` repairs bins that miss the error bound. D27 halved them; D31 trims their top
edge and lets the leftover start the next bin. This runbook checks D31 on the FiveK splits, which are not stored on
the main Mac. It covers 200 images (dev, test, holdout, holdout2), about 2 GB of downloads, and about an hour
of mostly unattended running.

Expected outcome, from the 30 images already checked (dev and the Sony A7 IV set):

- Steps 0.5 and 1.0: every table and every encoded byte unchanged.
- Steps 1.5 and 2.0: a few planes change, all smaller; 0 pixels outside the bound.
- The hot tier no longer grows from step 1.5 to 2.0. In the stored D27 results, that happens on
  `a4293-20090406_at_22h40m24__MG_1277` and `a4695-09-05-17-at-15h52m54s-_MG_9215` (holdout) and
  `a4412-20090417_at_18h17m13__MG_2017` (holdout2).

## 1. Get the code

D31 is on `main`; the main Mac must have pushed it (`git push`). Then:

```sh
git clone https://github.com/Greenere/miniraw.git && cd miniraw     # or, in an existing clone: git pull
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q                                        # expect all passed, 1 skipped
```

The comparison in step 4 is against results the main Mac produced, so the JPEG XL bytes must match. The main
Mac ran Python 3.14.3, libjxl 0.12.0 via imagecodecs 2026.10.10, numpy 2.5.4, scipy 1.18.1, rawpy 0.27.1 and
constriction 0.5.0. Check yours:

```sh
.venv/bin/pip list | grep -iE '^(imagecodecs|numpy|scipy|rawpy|constriction) '
.venv/bin/python -c "from miniraw import entropy; print(entropy.jxl_version())"
```

If they differ, pin them (`.venv/bin/pip install imagecodecs==2026.10.10 ...`). Step 4 also catches a mismatch:
steps 0.5 and 1.0 should show 0 images changed, and if they don't, the libraries differ, not D31.

## 2. Download the images

```sh
for s in dev test holdout holdout2; do .venv/bin/python -m bench.datasets --split $s; done
```

Files go to `data/raw/fivek/` (git-ignored). Re-running skips files already there, so an interrupted download
can simply be restarted.

## 3. Check the tables (about 5 minutes)

```sh
.venv/bin/python -m bench.experiments.d31_check tables dev test holdout holdout2
```

It compares the D27 and D31 tables on every plane at steps 0.5 to 2.0, with no JPEG XL, and writes
`results/experiments/d31_tables.csv`. Pass: no "missing" or "unreadable" lines, 0 planes changed at steps
0.5 and 1.0, and violations 0 at every step.

## 4. Benchmark the two format 2 holdouts (about 10 minutes each)

The stored D27 results are `results/per_image/holdout_v2` and `holdout2_v2`. Run D31 under new names, so
those stay untouched, with `caffeinate` so the Mac can't sleep mid-run:

```sh
caffeinate -i .venv/bin/python -m bench.run --config configs/v2.toml --split holdout  --name holdout_v2_d31  > results/holdout_v2_d31_run.log 2>&1
caffeinate -i .venv/bin/python -m bench.run --config configs/v2.toml --split holdout2 --name holdout2_v2_d31 > results/holdout2_v2_d31_run.log 2>&1
.venv/bin/python -m bench.experiments.d31_check compare holdout_v2  holdout_v2_d31
.venv/bin/python -m bench.experiments.d31_check compare holdout2_v2 holdout2_v2_d31
```

The runner is resumable: it skips images whose CSV already exists, so after an interruption just run the same
command again. Check that each log has 60 lines and no `ERROR`.

Pass:

- **Steps 0.5 and 1.0:** 0 images changed.
- **Steps 1.5 and 2.0:**
  - the new/old ratio is at most 1.0000;
  - violations are 0 after the change.
- **Step 2.0 against 1.5:** the D31 folder has no image whose hot tier is larger at step 2.0.

Timings in these runs are this Mac's and aren't comparable with the main Mac; only sizes and errors are.

## 5. Bring the results back

```sh
git add results/experiments/d31_tables.csv results/per_image/holdout_v2_d31 results/per_image/holdout2_v2_d31 \
        results/holdout_v2_d31_run.log results/holdout2_v2_d31_run.log
git commit -m "D31 verification on FiveK holdouts" && git push
```

Save the printed output of steps 3 and 4 as well; it goes into the D31 entry in `results/DEVIATIONS.md` and
`CHANGELOG.md`.

## If something fails

| Symptom | Likely cause |
| --- | --- |
| `N of 60 files missing` in step 3 | Download incomplete: re-run step 2 |
| Steps 0.5 or 1.0 show changed images in step 4 | Different imagecodecs/libjxl version (step 1) |
| Run times in the logs far above about 30-80 s per image | The Mac slept: keep `caffeinate`, re-run (it resumes) |
| A `.part` file left in `data/raw/fivek/` | Interrupted download: delete it and re-run step 2 |
