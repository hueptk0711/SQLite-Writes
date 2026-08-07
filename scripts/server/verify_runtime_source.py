from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_runtime_source(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    expected_package_root = (root / "src" / "nldbwrite_v3").resolve()
    expected_init = expected_package_root / "__init__.py"
    result: dict[str, Any] = {
        "status": "error",
        "project_root": str(root),
        "expected_package_root": str(expected_package_root),
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": sys.version,
        "sys_path": [str(Path(item or ".").resolve()) for item in sys.path],
    }

    try:
        package = importlib.import_module("nldbwrite_v3")
        package_file = Path(str(package.__file__)).resolve()
        package_root = package_file.parent
        source_is_current_bundle = (
            expected_package_root.is_dir()
            and expected_init.is_file()
            and package_root == expected_package_root
            and package_file == expected_init
        )
        result.update(
            {
                "status": "ok" if source_is_current_bundle else "mismatch",
                "package_file": str(package_file),
                "package_root": str(package_root),
                "package_init_sha256": (
                    _sha256(package_file) if package_file.is_file() else None
                ),
                "expected_package_init_sha256": (
                    _sha256(expected_init) if expected_init.is_file() else None
                ),
                "source_is_current_bundle": source_is_current_bundle,
            }
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail unless nldbwrite_v3 is imported from this bundle."
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    result = inspect_runtime_source(args.project_root)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
