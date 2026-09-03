# Stage7E0-A7 Server Run Commands

Run the shell script, not this Markdown file:

```bash
cd /home/uet/hue_ptk
unzip -q -o Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY_PATCH1_FINAL_REVIEWER_PACKAGE_20260903.zip -d Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY_runner
cd Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY_runner
bash Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY/SERVER_RUN_COMMANDS.sh
```

The run is the single official A7 generation. Do not use `--resume`, do not
change the gate after seeing results, and do not open Gretel unless A7 reaches
12/12 target-state correctness and validation passes.

Accepted protocol commit frozen before GPU run: `1b73498d64ac2e233ba953e8fdf3cc98992026ea`
