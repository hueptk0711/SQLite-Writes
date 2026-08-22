# Run Stage 4 Fresh 7B After Reviewer Acceptance

Target server path requested by user:

```bash
ssh uet@222.255.250.24
mkdir -p /home/uet/hue_ptk
cd /home/uet/hue_ptk
```

Upload the accepted code/package from the local machine:

```powershell
scp "D:\paper kltn\text to sql\reviewer_packages\Stage4_FRESH_7B_PROTOCOL_PATCH1_FINAL_REVIEWER_PACKAGE_20260822.zip" uet@222.255.250.24:/home/uet/hue_ptk/
```

On the server, unpack only after protocol acceptance and use a clean git
checkout at the accepted Patch-1 commit:

```bash
cd /home/uet/hue_ptk
unzip Stage4_FRESH_7B_PROTOCOL_PATCH1_FINAL_REVIEWER_PACKAGE_20260822.zip -d Stage4_FRESH_7B_PROTOCOL_PATCH1_REVIEW
git clone https://github.com/hueptk0711/SQLite-Writes.git SQLite-Writes-stage4
cd SQLite-Writes-stage4
git checkout <PATCH1_COMMIT_AFTER_REVIEW>
python -m venv .venv-stage4
source .venv-stage4/bin/activate
pip install -r requirements-inference.lock.txt
```

Before any model generation, run the exact-token GPU preflight using the
accepted local Qwen2.5-Coder-7B-Instruct snapshot. Replace the data paths with
the server locations of the archived Stage-4 source files.

```bash
python scripts/server/run_stage4_gpu_preflight.py   --protocol-root stage4_fresh_7b_protocol   --fresh-source-data /home/uet/hue_ptk/data/stage4/dataset_test_v3.json   --fresh-gold-plans /home/uet/hue_ptk/data/stage4/gold_plans.jsonl   --profile-dir /home/uet/hue_ptk/data/stage4/profiles   --model-name-or-path /home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242   --accepted-protocol-commit <PATCH1_COMMIT_AFTER_REVIEW>   --output-dir /home/uet/hue_ptk/stage4_fresh_7b_gpu_preflight
```

If any prompt overflows, or if Original-vs-D_G1 final input equality is not
300/300, stop and send the preflight output for review.

Only after preflight PASS, run the single authoritative Stage-4 runner:

```bash
python scripts/server/run_stage4_fresh_7b.py   --protocol-root stage4_fresh_7b_protocol   --fresh-source-data /home/uet/hue_ptk/data/stage4/dataset_test_v3.json   --fresh-gold-plans /home/uet/hue_ptk/data/stage4/gold_plans.jsonl   --profile-dir /home/uet/hue_ptk/data/stage4/profiles   --db-root /home/uet/hue_ptk/data/stage4/databases   --model-name-or-path /home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242   --accepted-protocol-commit <PATCH1_COMMIT_AFTER_REVIEW>   --result-root /home/uet/hue_ptk/stage4_fresh_7b_results
```
