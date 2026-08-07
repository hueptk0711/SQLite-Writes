from __future__ import annotations

import argparse
import json
from pathlib import Path

from nldbwrite_v3.common import dump_json, read_ids, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ids",
        default="data/frozen/dev/dev_ids_v3.txt",
    )
    parser.add_argument(
        "--output-dir",
        default="data/splits/preflight",
    )
    args = parser.parse_args()
    source = Path(args.ids)
    ids = read_ids(source)
    if len(ids) < 20:
        raise ValueError("At least 20 development IDs are required")
    target = Path(args.output_dir)
    target.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for size in (5, 20):
        path = target / f"dev_preflight_{size}_ids.txt"
        path.write_text("\n".join(ids[:size]) + "\n", encoding="utf-8")
        outputs[str(size)] = {
            "path": str(path),
            "sample_count": size,
            "sha256": sha256_file(path),
        }
    manifest = {
        "source_ids": str(source),
        "source_ids_sha256": sha256_file(source),
        "selection": "first_n_from_fixed_stratified_dev_order",
        "outputs": outputs,
    }
    dump_json(manifest, target / "preflight_manifest.json")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
