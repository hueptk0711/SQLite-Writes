: <<'STAGE7E0_A5_MARKDOWN_DOC'
# Stage7E0-A5 English Column-Conditioned UET RTX4090 Commands

Run the primary set first. Do not run diagnostics before the primary result is
frozen and reviewed. A completed primary result is preserved whether it is
12/12 PASS or a protocol-compliant scientific FAIL below 12/12.

Copy `Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH4_FINAL_REVIEWER_PACKAGE_20260901.zip` to `/home/uet/hue_ptk`, extract it, then run the shell
script. The `.md` file is bash-compatible and delegates to the `.sh` file, but
running `SERVER_RUN_COMMANDS.sh` directly is preferred.

```bash
cd /home/uet/hue_ptk
rm -rf Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH4_runner
unzip -q -o Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH4_FINAL_REVIEWER_PACKAGE_20260901.zip -d Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH4_runner
bash Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH4_runner/Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT/SERVER_RUN_COMMANDS.sh
```

Do not use `--resume`. If infrastructure interrupts, archive the partial output
as infrastructure-aborted and rerun in a new empty result root. If the primary
run completes with less than 12/12, keep running the validator, archive, and
sha256 commands above; that is a completed scientific result, not an
infrastructure failure. Diagnostics are not part of this primary preflight
command; run them only after the primary result is frozen and reviewed as 12/12
PASS.
STAGE7E0_A5_MARKDOWN_DOC

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$SCRIPT_DIR/SERVER_RUN_COMMANDS.sh"
