from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path
from typing import Iterable


BUNDLE_NAME = "mp_fs_plus_gpu_smoke_20260727_v5"
ROOT_FILES = (
    ".gitignore",
    "pyproject.toml",
    "requirements.txt",
    "requirements-inference.txt",
    "requirements-inference.lock.txt",
    "uv.lock",
)
ROOT_DIRECTORIES = (
    "src",
    "configs",
    "schemas",
    "scripts",
    "tests",
    "docs",
)
DATA_FILES = (
    "data/frozen/dev/dataset_dev_v3.json",
    "data/frozen/dev/dev_ids_v3.txt",
    "data/frozen/dev/gold_write_plans_dev_v3.jsonl",
    "data/smoke/real_model_smoke15/dataset.json",
    "data/smoke/real_model_smoke15/ids.txt",
    "data/smoke/real_model_smoke15/gold_write_plans.jsonl",
    "data/smoke/real_model_smoke15/selection_manifest.json",
    "data/smoke/real_model_smoke15/server_external_assets_manifest.json",
)
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    ".venv_gpu",
    ".pytest_cache",
    "__pycache__",
    "nldbwrite_v3.egg-info",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
TEXT_SUFFIXES = {
    ".cfg",
    ".csv",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_LOCAL_WINDOWS_PREFIXES = (
    "C:" + "\\Users\\",
    "D:" + "\\paper kltn\\",
    "D:" + "/paper kltn/",
)
LOCAL_WINDOWS_PATH = re.compile(
    "|".join(re.escape(value) for value in _LOCAL_WINDOWS_PREFIXES),
    re.IGNORECASE,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _eligible(path: Path) -> bool:
    if any(part in EXCLUDED_PARTS for part in path.parts):
        return False
    return path.suffix.casefold() not in EXCLUDED_SUFFIXES


def _iter_directory(root: Path, relative: str) -> Iterable[Path]:
    base = root / relative
    if not base.is_dir():
        raise FileNotFoundError(base)
    for path in sorted(base.rglob("*")):
        if path.is_file() and _eligible(path.relative_to(root)):
            yield path


def _source_files(root: Path) -> list[tuple[Path, str]]:
    output: list[tuple[Path, str]] = []
    for relative in ROOT_FILES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        output.append((path, relative))
    for relative in ROOT_DIRECTORIES:
        output.extend(
            (path, path.relative_to(root).as_posix())
            for path in _iter_directory(root, relative)
        )
    for relative in DATA_FILES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(
                f"{path} is missing; run build_real_model_smoke15.py first"
            )
        output.append((path, relative))
    sanitized_asset_manifest = (
        root
        / "data"
        / "smoke"
        / "real_model_smoke15"
        / "server_external_assets_manifest.json"
    )
    output.append(
        (
            sanitized_asset_manifest,
            "data/frozen/dev/frozen_manifest_dev.json",
        )
    )
    deployment_readme = root / "docs" / "GPU_SERVER_DEPLOYMENT.md"
    output.append((deployment_readme, "README.md"))
    return sorted(output, key=lambda item: item[1])


def _tar_info(name: str, size: int, *, executable: bool = False) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mode = 0o755 if executable else 0o644
    return info


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a clean Linux GPU-smoke deployment tarball."
    )
    parser.add_argument("--output-dir", default="dist/server")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    sources = _source_files(project_root)
    payloads: dict[str, bytes] = {}
    for source, relative in sources:
        data = source.read_bytes()
        if source.suffix.casefold() in TEXT_SUFFIXES:
            text = data.decode("utf-8")
            match = LOCAL_WINDOWS_PATH.search(text)
            if match:
                raise ValueError(
                    f"Local Windows path found in packaged file {relative}: "
                    f"{match.group(0)!r}"
                )
        if relative in payloads:
            raise ValueError(f"Duplicate bundle path: {relative}")
        payloads[relative] = data

    file_hashes = {
        relative: _sha256(data)
        for relative, data in sorted(payloads.items())
    }
    bundle_manifest = {
        "bundle_id": BUNDLE_NAME,
        "purpose": "mp_fs_plus_real_model_technical_smoke_only",
        "paper_result_eligible": False,
        "server_parent_required": True,
        "project_directory_template": f"<SERVER_PARENT>/{BUNDLE_NAME}",
        "excluded": [
            ".venv",
            ".venv_gpu",
            ".pytest_cache",
            "__pycache__",
            "*.pyc",
            "*.egg-info",
            "experiments",
            "local artifacts",
            "database files",
            "model weights",
            "credentials",
        ],
        "external_assets_required": [
            "profiles_aug900",
            "bird_databases",
            "pinned Hugging Face model",
            "NVIDIA CUDA GPU",
        ],
        "file_count": len(payloads),
        "files": file_hashes,
    }
    payloads["SERVER_BUNDLE_MANIFEST.json"] = (
        json.dumps(bundle_manifest, ensure_ascii=False, indent=2).encode("utf-8")
        + b"\n"
    )
    payloads["SHA256SUMS.txt"] = "".join(
        f"{_sha256(data)}  {relative}\n"
        for relative, data in sorted(payloads.items())
        if relative != "SHA256SUMS.txt"
    ).encode("utf-8")

    output_dir = (project_root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"{BUNDLE_NAME}.tar.gz"
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw,
            mtime=0,
        ) as compressed:
            with tarfile.open(
                fileobj=compressed,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as archive:
                for relative, data in sorted(payloads.items()):
                    name = f"{BUNDLE_NAME}/{relative}"
                    executable = relative.endswith(".sh")
                    archive.addfile(
                        _tar_info(name, len(data), executable=executable),
                        io.BytesIO(data),
                    )

    archive_sha256 = _sha256(archive_path.read_bytes())
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    checksum_path.write_text(
        f"{archive_sha256}  {archive_path.name}\n",
        encoding="utf-8",
    )
    result = {
        "status": "built",
        "bundle": str(archive_path),
        "bundle_sha256": archive_sha256,
        "checksum_file": str(checksum_path),
        "archive_root": BUNDLE_NAME,
        "file_count": len(payloads),
        "size_bytes": archive_path.stat().st_size,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
