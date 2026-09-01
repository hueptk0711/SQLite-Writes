# Stage7C-A5 Gold Provenance Erratum PATCH0

Status: REAL_CONSTRAINED_PRIMARY_FAIL_DO_NOT_OPEN_GRETEL

The Stage7E0-A5 UET raw generations are reused byte-for-byte. No model, GPU,
Gretel pilot, development-dev, or official test rows are opened in this
erratum. The previous old-gold `1/12` classification is superseded by corrected
offline replay.

```text
source_tar=stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901.tar.gz
source_tar_sha256=f275b7990c06f6d2130b6ebf0cdb33d29d8a51b83281fdc0beb83cdc2f34c035
old_gold_primary_pass_count=1/12
corrected_primary_pass_count=2/12
required_pass_count=12/12
duplicate_literal_count=3
primary_duplicate_literal_count=2
diagnostic_duplicate_literal_count=1
implicit_first_occurrence_forbidden_count=0
corrected_pass_case_ids=['stage7c_a5_primary_english_003', 'stage7c_a5_primary_english_012']
```
