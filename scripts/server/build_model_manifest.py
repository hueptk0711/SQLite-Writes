from __future__ import annotations

import argparse
import json

from nldbwrite_v3.common import dump_json
from nldbwrite_v3.inference import build_local_model_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--output", default="model_manifest.json")
    args = parser.parse_args()
    manifest = build_local_model_manifest(args.model_path)
    dump_json(manifest, args.output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
