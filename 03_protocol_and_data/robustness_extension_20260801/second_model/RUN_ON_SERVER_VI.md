# Lệnh chạy second-model trên server

Phần này **cần GPU**. Không chạy cho đến khi GPU 0 còn đủ VRAM và protocol đã
được freeze. Không chỉnh prompt/config sau khi xem bất kỳ output 14B nào.

## 1. Giải nén gói vào server

Giả sử gói được tải vào `$HOME/hue_ptk`:

```bash
cd "$HOME/hue_ptk"
sha256sum -c mp_fs_plus_robustness_extension_20260801.tar.gz.sha256
tar -xzf mp_fs_plus_robustness_extension_20260801.tar.gz
```

## 2. Khai báo đường dẫn

```bash
export NLDB_FINAL_PROJECT="$HOME/hue_ptk/mp_fs_plus_final_gpu_v2_out8192_20260731"
export NLDB_ROBUSTNESS_BUNDLE="$HOME/hue_ptk/mp_fs_plus_robustness_extension_20260801"
export NLDB_GPU_VENV="$HOME/hue_ptk/mp_fs_plus_final_gpu_20260731/.venv_gpu"
export NLDB_SECOND_MODEL_PATH="$HOME/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-14B-Instruct/snapshots/aedcc2d42b622764e023cf882b6652e646b95671"
export CUDA_VISIBLE_DEVICES=0
export HF_HOME="$HOME/hue_ptk/hf_cache"

test -x "$NLDB_GPU_VENV/bin/python" && echo "GPU VENV: OK"
test -d "$NLDB_SECOND_MODEL_PATH" && echo "MODEL 14B: OK"
test -f "$NLDB_FINAL_PROJECT/configs/experiments/final_protocol.json" && echo "BASE PROJECT: OK"
nvidia-smi -i 0
```

Nếu dòng `GPU VENV: OK` không xuất hiện, tìm đúng môi trường bằng:

```bash
find "$HOME/hue_ptk" -path '*/.venv_gpu/bin/python' -type f -executable -print
```

## 3. Freeze protocol trước inference

```bash
"$NLDB_GPU_VENV/bin/python" \
  "$NLDB_ROBUSTNESS_BUNDLE/second_model/freeze_second_model_protocol.py" \
  --project-root "$NLDB_FINAL_PROJECT" \
  --model-path "$NLDB_SECOND_MODEL_PATH" \
  2>&1 | tee "$NLDB_FINAL_PROJECT/diagnostics/second_model_freeze.log"
```

Lệnh phải in `status: frozen`, model aggregate SHA-256 và protocol SHA-256.
Lưu log này. Nếu có lỗi hash, dừng lại; không bỏ gate.

## 4. Chạy ba phương pháp

```bash
bash "$NLDB_ROBUSTNESS_BUNDLE/second_model/run_second_model_robustness.sh" \
  2>&1 | tee "$NLDB_FINAL_PROJECT/diagnostics/second_model_matrix_console.log"
```

Có thể theo dõi bằng:

```bash
tail -f "$NLDB_FINAL_PROJECT/diagnostics/second_model_matrix_console.log"
```

## 5. Kiểm tra và tải kết quả

```bash
cd "$NLDB_FINAL_PROJECT"
cat artifacts/reports/second_model_qwen25_coder_14b_v1.md
find dist/results -maxdepth 1 \
  -name 'mp_fs_plus_second_model_qwen25_coder_14b_v1_*.tar.gz*' \
  -printf '%f\n' | sort
```

Sau đó dùng `scp` trên Windows, thay `<STAMP>` bằng tên thật:

```powershell
scp `
  "<USER>@<GPU_HOST>:<SERVER_PARENT>/mp_fs_plus_final_gpu_v2_out8192_20260731/dist/results/mp_fs_plus_second_model_qwen25_coder_14b_v1_<STAMP>.tar.gz" `
  "<USER>@<GPU_HOST>:<SERVER_PARENT>/mp_fs_plus_final_gpu_v2_out8192_20260731/dist/results/mp_fs_plus_second_model_qwen25_coder_14b_v1_<STAMP>.tar.gz.sha256" `
  "D:\paper kltn\text to sql\server_results"
```

Gửi lại cả `.tar.gz`, `.sha256`, nội dung log freeze và log matrix để kiểm tra.
