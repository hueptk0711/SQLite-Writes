from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from nldbwrite_v3.common import dump_json, sha256_file


PACKAGES = (
    "torch",
    "transformers",
    "accelerate",
    "bitsandbytes",
    "sentencepiece",
    "protobuf",
    "safetensors",
    "tokenizers",
)


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _nvidia_smi() -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ]
    device_id = str(os.environ.get("NLDB_NVIDIA_SMI_ID") or "").strip()
    if device_id:
        command[1:1] = ["-i", device_id]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": str(exc), "gpus": []}
    rows = []
    for line in result.stdout.splitlines():
        values = [item.strip() for item in line.split(",")]
        if len(values) == 3:
            rows.append(
                {
                    "name": values[0],
                    "memory_mib": int(values[1]),
                    "driver_version": values[2],
                }
            )
    return {
        "available": result.returncode == 0 and bool(rows),
        "returncode": result.returncode,
        "stderr": result.stderr.strip() or None,
        "queried_device_id": device_id or None,
        "gpus": rows,
    }


def capture(lock_path: Path) -> dict[str, Any]:
    packages = _package_versions()
    nvidia = _nvidia_smi()
    torch_info: dict[str, Any] = {
        "installed": packages["torch"] is not None,
        "cuda_available": False,
    }
    if torch_info["installed"]:
        import torch

        torch_info.update(
            {
                "cuda_available": bool(torch.cuda.is_available()),
                "cuda_runtime": torch.version.cuda,
                "cudnn_version": (
                    torch.backends.cudnn.version()
                    if torch.backends.cudnn.is_available()
                    else None
                ),
                "gpu_count": (
                    torch.cuda.device_count()
                    if torch.cuda.is_available()
                    else 0
                ),
                "gpus": (
                    [
                        {
                            "index": index,
                            "name": torch.cuda.get_device_name(index),
                            "memory_bytes": torch.cuda.get_device_properties(
                                index
                            ).total_memory,
                        }
                        for index in range(torch.cuda.device_count())
                    ]
                    if torch.cuda.is_available()
                    else []
                ),
            }
        )
    missing = [name for name, version in packages.items() if version is None]
    ready = (
        not missing
        and bool(nvidia["available"])
        and bool(torch_info["cuda_available"])
    )
    return {
        "manifest_version": 1,
        "status": "gpu_ready" if ready else "not_gpu_ready",
        "os": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "python": {
            "version": sys.version,
            "executable": sys.executable,
        },
        "packages": packages,
        "missing_packages": missing,
        "nvidia_smi": nvidia,
        "torch": torch_info,
        "dependency_lock": {
            "path": str(lock_path.resolve()),
            "sha256": sha256_file(lock_path),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lock",
        default="requirements-inference.lock.txt",
    )
    parser.add_argument(
        "--output",
        default="environment_manifest.json",
    )
    parser.add_argument("--require-gpu", action="store_true")
    args = parser.parse_args()
    manifest = capture(Path(args.lock))
    dump_json(manifest, args.output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 2 if args.require_gpu and manifest["status"] != "gpu_ready" else 0


if __name__ == "__main__":
    raise SystemExit(main())
