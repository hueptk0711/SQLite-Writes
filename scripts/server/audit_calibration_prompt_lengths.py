from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from nldbwrite_v3.experiments.run_method import (
    _load_method_config,
    _load_profiles,
    _prompt_for_sample,
)


METHODS = (
    ("d_fs_m", "configs/final/d_fs_m.json"),
    ("j_fs_m", "configs/final/j_fs_m.json"),
    ("mp_fs_m", "configs/final/mp_fs_m.json"),
    ("mp_fs_plus", "configs/final/mp_fs_plus.json"),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument(
        "--data",
        default="data/calibration/dataset.json",
    )
    parser.add_argument(
        "--ids",
        default="data/calibration/calibration_ids.txt",
    )
    parser.add_argument(
        "--profile-dir",
        default="data/calibration/authoring_kit/profiles",
    )
    parser.add_argument("--current-limit", type=int, default=28672)
    parser.add_argument("--max-new-tokens", type=int, default=4096)
    parser.add_argument(
        "--output",
        default="diagnostics/calibration_prompt_length_audit.json",
    )
    args = parser.parse_args()

    from transformers import AutoConfig, AutoTokenizer

    model_path = Path(args.model_path).resolve()
    tokenizer = AutoTokenizer.from_pretrained(
        model_path,
        trust_remote_code=False,
        local_files_only=True,
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"
    model_config = AutoConfig.from_pretrained(
        model_path,
        trust_remote_code=False,
        local_files_only=True,
    )
    model_context_limit = int(
        getattr(model_config, "max_position_embeddings", 0) or 0
    )

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    samples = {str(row["id"]): row for row in data}
    ids = [
        line.strip()
        for line in Path(args.ids).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    profiles = _load_profiles(args.profile_dir)
    rows: list[dict[str, Any]] = []
    method_summaries: dict[str, dict[str, Any]] = {}

    for slug, config_path in METHODS:
        config, _base = _load_method_config(config_path)
        method_id = str(config["method_id"])
        method_rows = []
        for sample_id in ids:
            sample = samples[sample_id]
            profile = profiles[str(sample["db_id"])]
            prompt, _payload = _prompt_for_sample(
                method_id,
                sample,
                profile,
                config,
            )
            if hasattr(tokenizer, "apply_chat_template"):
                prompt = tokenizer.apply_chat_template(
                    [{"role": "user", "content": prompt}],
                    tokenize=False,
                    add_generation_prompt=True,
                )
            length = len(
                tokenizer(
                    prompt,
                    add_special_tokens=True,
                    truncation=False,
                )["input_ids"]
            )
            row = {
                "method_slug": slug,
                "method_id": method_id,
                "sample_id": sample_id,
                "db_id": sample["db_id"],
                "operation_semantics": sample["operation_semantics"],
                "input_format": sample["input_format"],
                "complexity": sample["complexity"],
                "multi_table": bool(sample["multi_table"]),
                "input_tokens": length,
                "above_current_limit": length > args.current_limit,
            }
            rows.append(row)
            method_rows.append(row)
        lengths = [row["input_tokens"] for row in method_rows]
        method_summaries[slug] = {
            "method_id": method_id,
            "samples": len(method_rows),
            "minimum_input_tokens": min(lengths),
            "maximum_input_tokens": max(lengths),
            "mean_input_tokens": sum(lengths) / len(lengths),
            "above_current_limit_count": sum(
                row["above_current_limit"] for row in method_rows
            ),
        }

    maximum = max(row["input_tokens"] for row in rows)
    recommended = int(math.ceil(maximum / 1024) * 1024)
    usable_context = (
        model_context_limit - args.max_new_tokens
        if model_context_limit
        else None
    )
    feasible = usable_context is None or recommended <= usable_context
    top_rows = sorted(
        rows,
        key=lambda row: (-row["input_tokens"], row["method_slug"], row["sample_id"]),
    )[:20]
    oversized = [
        row
        for row in rows
        if row["above_current_limit"]
    ]
    report = {
        "audit_version": 1,
        "status": (
            "current_limit_sufficient"
            if not oversized
            else (
                "increase_limit_feasible"
                if feasible
                else "not_feasible_with_model_context"
            )
        ),
        "model_path": str(model_path),
        "tokenizer_class": type(tokenizer).__name__,
        "model_context_limit": model_context_limit or None,
        "max_new_tokens": args.max_new_tokens,
        "usable_input_context": usable_context,
        "current_input_limit": args.current_limit,
        "prompt_count": len(rows),
        "maximum_observed_input_tokens": maximum,
        "recommended_input_limit_next_1024": recommended,
        "recommendation_feasible": feasible,
        "oversized_prompt_count": len(oversized),
        "oversized_by_method": dict(
            sorted(Counter(row["method_slug"] for row in oversized).items())
        ),
        "methods": method_summaries,
        "top_20_longest_prompts": top_rows,
        "all_prompts_above_current_limit": oversized,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
