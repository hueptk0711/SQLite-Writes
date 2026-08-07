# Experiment freeze

Freeze date: 2026-08-03

## Primary experiment

- Model: Qwen2.5-Coder-7B-Instruct.
- Sample set: 300 frozen requests trên 5 cơ sở dữ liệu.
- Methods: D-FS-M, J-FS-M, S-FS-v2-M, MP-FS-M, MP-FS+, Gold-MP.
- Primary outcome: target-state correctness.
- Primary paired comparisons: MP-FS+ vs D-FS-M, MP-FS+ vs J-FS-M,
  MP-FS+ vs MP-FS-M, và J-FS-M vs D-FS-M.

## Analysis labels

- Primary: so sánh sáu hệ thống Qwen-7B và bốn paired comparisons đã định trước.
- Post-hoc: backend swap, common transactional preflight, downstream ablation,
  Qwen-14B và Yi-Coder-9B sensitivity.
- Exploratory post-hoc: J-then-D cascade.
- Diagnostic/descriptive: redundancy và efficiency analyses.

Không phát triển thêm method, prompt, builder, verifier, policy, threshold hoặc
routing rule trên tập 300 mẫu này. Mọi ý tưởng mới phải dùng protocol và holdout
mới hoặc được đưa vào Future Work.
