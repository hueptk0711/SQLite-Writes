# Migration from paper_v2_current

The v2 tree remains a provenance archive. Do not edit its source, datasets, or
completed results during the v3 campaign.

| v2 responsibility | v3 location | Change |
|---|---|---|
| `sql/build_sql.py` | `compiler/` | Explicit conflict, placeholders, atomic program |
| `sql/normalize_values.py` | `compiler.normalize_value` | Semantic preservation; off by default |
| `eval/evaluate.py` | `evaluator/state.py` | Gold-plan oracle and strict state comparison |
| `data/derive_gold_records.py` | `data/gold_sql.py` | Gold Write Plan with conflict semantics |
| prompt JSON records | `planner/` | Mapping-only plan for structured payloads |
| repair full JSON | `repair/patch.py` | Restricted plan delta |
| mixed experiment configs | `configs/{baselines,proposed,ablations}` | Compact main matrix |
| original record-JSON builder | `baselines/v2_builder.py` | Frozen adapter used only by `S-FS-v2` |

The v3 code reads profiles and databases through `NLDB_PROFILE_DIR` and
`NLDB_DATABASE_ROOT`; no relative v2 layout is required. `S-FS-v2` additionally
uses `NLDB_V2_SOURCE` to load the frozen original builder. Development and test
datasets, IDs, and Gold Write Plans are independently frozen under
`data/frozen/dev/` and `data/frozen/test/`, with zero ID/source-group overlap.
