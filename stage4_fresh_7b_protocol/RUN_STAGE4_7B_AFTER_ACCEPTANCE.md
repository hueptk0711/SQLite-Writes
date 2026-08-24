# Run Stage 4 Fresh 7B After Reviewer Acceptance

Target server path requested by user:

```bash
ssh uet@222.255.250.24
mkdir -p /home/uet/hue_ptk
cd /home/uet/hue_ptk
```

Upload the accepted code/package from the local machine:

```powershell
scp "D:\paper kltn\text to sql\reviewer_packages\Stage4_GPU_ENV_COMPATIBILITY_PATCH_FINAL_REVIEWER_PACKAGE_20260822.zip" uet@222.255.250.24:/home/uet/hue_ptk/
```

On the server, unpack only after protocol acceptance and use a clean git
checkout at the accepted environment-final commit:

```bash
cd /home/uet/hue_ptk
git clone https://github.com/hueptk0711/SQLite-Writes.git SQLite-Writes-stage4
cd SQLite-Writes-stage4
git checkout <ENV_FINAL_COMMIT_AFTER_REVIEW>
python3.12 -m venv .venv-stage4
source .venv-stage4/bin/activate
pip install -r requirements-inference.lock.txt
```

If the historical verified GPU environment still exists, prefer reusing it
instead of reinstalling:

```bash
/home/uet/hue_ptk/mp_fs_plus_final_gpu_20260731/.venv_gpu/bin/python --version
```

Expected Python major/minor is `3.12.x`; historical verified exact version was
`3.12.7`. Do not use Python 3.14 for Stage-4 GPU inference.

Before any model generation, run the exact-token GPU preflight using the
accepted local Qwen2.5-Coder-7B-Instruct snapshot. Replace the data paths with
the server locations of the archived Stage-4 source files.

```bash
python scripts/server/run_stage4_gpu_preflight.py   --protocol-root stage4_fresh_7b_protocol   --fresh-source-data /home/uet/hue_ptk/data/stage4/dataset_test_v3.json   --fresh-gold-plans /home/uet/hue_ptk/data/stage4/gold_write_plans_test_v3.jsonl   --profile-dir /home/uet/hue_ptk/data/stage4/profiles_aug900   --model-name-or-path /home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242   --accepted-protocol-commit <ENV_FINAL_COMMIT_AFTER_REVIEW>   --output-dir /home/uet/hue_ptk/stage4_fresh_7b_gpu_preflight
```

If any prompt overflows, or if Original-vs-D_G1 final input equality is not
300/300, stop and send the preflight output for review.

Only after preflight PASS, run the single authoritative Stage-4 runner. For a
brand-new run, omit `--resume`:

```bash
python scripts/server/run_stage4_fresh_7b.py   --protocol-root stage4_fresh_7b_protocol   --fresh-source-data /home/uet/hue_ptk/data/stage4/dataset_test_v3.json   --fresh-gold-plans /home/uet/hue_ptk/data/stage4/gold_write_plans_test_v3.jsonl   --profile-dir /home/uet/hue_ptk/data/stage4/profiles_aug900   --db-root /home/uet/hue_ptk/data/stage4/bird_databases   --model-name-or-path /home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242   --accepted-protocol-commit <ENV_FINAL_COMMIT_AFTER_REVIEW>   --result-root /home/uet/hue_ptk/stage4_fresh_7b_results
```

If the SSH/session crashes before all raw rows are written, resume the exact
same result root explicitly:

```bash
python scripts/server/run_stage4_fresh_7b.py   --resume   --protocol-root stage4_fresh_7b_protocol   --fresh-source-data /home/uet/hue_ptk/data/stage4/dataset_test_v3.json   --fresh-gold-plans /home/uet/hue_ptk/data/stage4/gold_write_plans_test_v3.jsonl   --profile-dir /home/uet/hue_ptk/data/stage4/profiles_aug900   --db-root /home/uet/hue_ptk/data/stage4/bird_databases   --model-name-or-path /home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242   --accepted-protocol-commit <ENV_FINAL_COMMIT_AFTER_REVIEW>   --result-root /home/uet/hue_ptk/stage4_fresh_7b_results
```

After generation completes, run the frozen analysis script:

```bash
python scripts/analysis/analyze_stage4_fresh_7b.py   --protocol-root stage4_fresh_7b_protocol   --result-root /home/uet/hue_ptk/stage4_fresh_7b_results   --output-dir /home/uet/hue_ptk/stage4_fresh_7b_results/analysis
```
