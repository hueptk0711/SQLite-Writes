# Statistical Analysis Plan

Primary paired comparison: Original MP-FS+ vs D_G1.

Report paired counts:

- both correct
- original only correct
- D_G1 only correct
- both wrong

Accuracy remains sample-weighted. The primary 95% confidence interval for the
paired accuracy difference uses a cluster bootstrap over source_group:

- cluster key: official source_group if present, else official source_group_id;
  sample-ID derivation only if no official metadata exists
- bootstrap replicates: 10000
- bootstrap RNG seed: 240822
- interval: percentile 95% CI

Report McNemar exact test as a secondary conventional paired test with a note
that the dataset contains clustered variants.

The paired comparison is computed independently for:

- `target_state_correct`
- `strict_full_state_correct`

No paired analysis may use sample-ID intersections. The analysis stops unless
Original MP-FS+ and D_G1 both contain exactly the frozen 300 sample IDs.

Predeclared subgroups:

- free_text
- semi_structured
- plain_insert
- insert_ignore
- upsert_update
- per database
- dependency-sensitive
- non-dependency-sensitive

Safety/selective summaries are reported for every predeclared method before
subgrouping: coverage, accepted-output accuracy, false-accept count/rate,
execution-success rate, constraint-failure count/rate, and off-target
state-change count/rate.
