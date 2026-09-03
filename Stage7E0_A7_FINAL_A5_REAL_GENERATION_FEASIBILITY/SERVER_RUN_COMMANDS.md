# Stage7E0-A7 Server Run Commands

Run the shell script, not this Markdown file:

```bash
cd /home/uet/hue_ptk
unzip -q -o Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY_PATCH3_FINAL_REVIEWER_PACKAGE_20260903.zip -d Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY_runner
cd Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY_runner
bash Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY/SERVER_RUN_COMMANDS.sh
```

The run is the single official A7 generation. Do not use `--resume`, do not
change the gate after seeing results, and do not open Gretel unless A7 reaches
12/12 target-state correctness and validation passes.

PATCH2 note: if a previous PATCH1 run already created `stage7e0_a7_final_a5_uet_rtx4090_primary_results_20260903`
and crashed only during summary writing, this script finalizes that existing
result-root without new model calls.

Accepted protocol commit frozen before GPU run: `e31489e02f37f40fc7646fe0d8659557427ebe79`
