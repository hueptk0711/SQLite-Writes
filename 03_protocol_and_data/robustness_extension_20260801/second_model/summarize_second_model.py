from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


METHODS = (
    ("d_fs_m", "D-FS-M"),
    ("j_fs_m", "J-FS-M"),
    ("mp_fs_plus", "MP-FS+"),
)
FIELDS = (
    "samples",
    "target_state_accuracy",
    "execution_success",
    "coverage",
    "accepted_output_accuracy",
    "side_effect_rate",
    "input_truncation_rate",
    "output_limit_hit_rate",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    result_root = Path(args.result_root)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    protocol = json.loads(Path(args.protocol).read_text(encoding="utf-8"))
    rows = []
    for slug, method_id in METHODS:
        metrics = json.loads((result_root / slug / "metrics.json").read_text(encoding="utf-8"))
        rows.append({"method_id": method_id, **{field: metrics.get(field) for field in FIELDS}})
    report = {
        "analysis_id": "post_hoc_second_model_qwen25_coder_14b_v1",
        "status": "pass",
        "paper_primary_result": False,
        "protocol_id": protocol["protocol_id"],
        "methods": {row["method_id"]: {key: value for key, value in row.items() if key != "method_id"} for row in rows},
    }
    stem = output_root / "second_model_qwen25_coder_14b_v1"
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with stem.with_suffix(".csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("method_id", *FIELDS))
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# Post-hoc second-model robustness",
        "",
        "This is not a blind primary result. No prompt or method was tuned from these outputs.",
        "",
        "| Method | Target | Execution | Admission coverage | Admitted accuracy | Side effect |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['method_id']} | {row['target_state_accuracy']:.4f} | "
            f"{row['execution_success']:.4f} | {row['coverage']:.4f} | "
            f"{row['accepted_output_accuracy']:.4f} | {row['side_effect_rate']:.4f} |"
        )
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
