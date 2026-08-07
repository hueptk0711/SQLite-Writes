# GPU smoke deployment on the UET server

This bundle runs only the 15-sample, non-reportable MP-FS+ technical smoke.
It does not run calibration or the final holdout.

## 1. Upload from Windows

The bundle name and checksum are printed by the local packaging command.

```powershell
scp .\dist\server\mp_fs_plus_gpu_smoke_20260727_v5.tar.gz `
  <USER>@<GPU_HOST>:<SERVER_PARENT>/
scp .\dist\server\mp_fs_plus_gpu_smoke_20260727_v5.tar.gz.sha256 `
  <USER>@<GPU_HOST>:<SERVER_PARENT>/
```

## 2. Create a new server folder

```bash
ssh <USER>@<GPU_HOST>
cd "$HOME/hue_ptk"
sha256sum -c mp_fs_plus_gpu_smoke_20260727_v5.tar.gz.sha256
tar -xzf mp_fs_plus_gpu_smoke_20260727_v5.tar.gz
cd mp_fs_plus_gpu_smoke_20260727_v5
```

Do not extract over `paper_v2_20260714` or an earlier MP-FS+ run.

## 3. Prepare the CUDA environment

The default creates `.venv_gpu` inside the new project folder and installs
the pinned CUDA requirements.

```bash
bash scripts/server/bootstrap_gpu_smoke.sh
```

To reuse an already verified Python environment:

```bash
export NLDB_SKIP_INSTALL=1
export NLDB_PYTHON_BIN=/absolute/path/to/python
bash scripts/server/bootstrap_gpu_smoke.sh
```

The bootstrap prepends this bundle's `src` directory and exits unless the
runtime manifest proves that `nldbwrite_v3` was imported from this exact v5
folder. Reusing an older virtualenv therefore cannot silently run older
package source.

## 4. Point to the pinned model and run

For a local checkpoint:

```bash
export NLDB_PYTHON_BIN="$PWD/.venv_gpu/bin/python"
export NLDB_MODEL_PATH=/absolute/path/to/Qwen-checkpoint
export CUDA_VISIBLE_DEVICES=0
bash scripts/server/run_gpu_smoke15.sh
```

For a remote Hugging Face model, also pin the exact 40-character commit:

```bash
export NLDB_MODEL_PATH=organization/model-name
export NLDB_MODEL_REVISION=0123456789abcdef0123456789abcdef01234567
export HF_TOKEN=REPLACE_IN_YOUR_SHELL_ONLY
bash scripts/server/run_gpu_smoke15.sh
```

Do not save `HF_TOKEN`, passwords, or private keys in this project.

The script defaults to batch size 1, 4-bit loading, 16384 input tokens, and
2048 new tokens. Override only before the run:

```bash
export NLDB_BATCH_SIZE=1
export NLDB_QUANTIZATION=4bit
export NLDB_COMPUTE_DTYPE=float16
```

The existing DEV database/profile assets default to:

```text
$HOME/hue_ptk/paper_v2_20260714/nl_db_write_pipeline/artifacts/profiles_aug900
$HOME/hue_ptk/paper_v2_20260714/nl_db_write_pipeline/data/bird_databases
```

Set `NLDB_PROFILE_DIR` and `NLDB_DATABASE_ROOT` if the server uses different
paths.

## 5. Download the result

The run prints an archive under `dist/results/`. From Windows:

```powershell
scp <USER>@<GPU_HOST>:<SERVER_PARENT>/mp_fs_plus_gpu_smoke_20260727_v5/dist/results/*.tar.gz `
  .\server_results\
scp <USER>@<GPU_HOST>:<SERVER_PARENT>/mp_fs_plus_gpu_smoke_20260727_v5/dist/results/*.sha256 `
  .\server_results\
```

Passing this smoke only authorizes construction of the calibration dataset.
Its metrics are not paper accuracy results.
