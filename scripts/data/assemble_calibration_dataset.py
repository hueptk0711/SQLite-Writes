from __future__ import annotations

import argparse
import json

from nldbwrite_v3.data.authoring import assemble_calibration_samples


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assemble the 60 independently edited sample files."
    )
    parser.add_argument("--samples-dir", required=True)
    parser.add_argument("--ids", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    samples = assemble_calibration_samples(
        samples_dir=args.samples_dir,
        ids_path=args.ids,
        output_path=args.output,
    )
    print(json.dumps({"assembled_samples": len(samples)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
