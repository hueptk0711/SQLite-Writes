# Statistical Analysis Plan

Primary paired comparison: Original MP-FS+ vs D_G1.

Report paired counts:

- both correct
- original only correct
- D_G1 only correct
- both wrong

Use McNemar exact test for the paired correctness table. Report a 95% paired
accuracy-difference confidence interval by bootstrap over sample IDs.

Predeclared subgroups:

- free_text
- semi_structured
- plain_insert
- insert_ignore
- upsert_update
- per database
- dependency-sensitive
- non-dependency-sensitive
