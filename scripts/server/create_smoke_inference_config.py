from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from nldbwrite_v3.common import dump_json, load_json


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a pinned Hugging Face config for the GPU smoke."
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-manifest")
    parser.add_argument("--revision")
    parser.add_argument("--output", required=True)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--max-input-tokens", type=int, default=8192)
    parser.add_argument("--max-new-tokens", type=int, default=2048)
    parser.add_argument(
        "--quantization",
        choices=("4bit", "8bit", "none"),
        default="4bit",
    )
    parser.add_argument(
        "--compute-dtype",
        choices=("float16", "bfloat16"),
        default="float16",
    )
    args = parser.parse_args()

    model = Path(args.model)
    config: dict[str, object] = {
        "backend": "hf",
        "model_name_or_path": str(model.resolve()) if model.exists() else args.model,
        "batch_size": args.batch_size,
        "max_input_tokens": args.max_input_tokens,
        "max_new_tokens": args.max_new_tokens,
        "input_truncation_policy": "error",
        "quantization": args.quantization,
        "compute_dtype": args.compute_dtype,
        "device_map": "auto",
        "do_sample": False,
        "seed": 42,
        "trust_remote_code": False,
    }
    if args.quantization == "none":
        config["quantization"] = None
        config["torch_dtype"] = args.compute_dtype

    if model.exists():
        if not model.is_dir():
            raise ValueError(f"Local model must be a directory: {model}")
        if not args.model_manifest:
            raise ValueError("Local model requires --model-manifest")
        manifest = load_json(args.model_manifest)
        model_hash = str(manifest.get("aggregate_sha256") or "")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", model_hash):
            raise ValueError("Model manifest lacks a valid aggregate_sha256")
        config["model_hash"] = model_hash.lower()
    else:
        revision = str(args.revision or "")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
            raise ValueError(
                "Remote model requires --revision with a 40-character commit hash"
            )
        config["revision"] = revision.lower()

    dump_json(config, args.output)
    print(json.dumps(config, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
