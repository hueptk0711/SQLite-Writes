from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from nldbwrite_v3.common import sha256_file


_IGNORED_PARTS = {".git", "__pycache__"}
_IGNORED_SUFFIXES = {".lock", ".tmp"}


def build_local_model_manifest(model_path: str | Path) -> dict[str, Any]:
    """Hash every regular checkpoint/tokenizer file in deterministic order."""
    root = Path(model_path).resolve()
    if not root.is_dir():
        raise ValueError(f"Local model path is not a directory: {root}")
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(
        (
            item
            for item in root.rglob("*")
            if item.is_file()
            and not (_IGNORED_PARTS & set(item.relative_to(root).parts))
            and item.suffix.casefold() not in _IGNORED_SUFFIXES
        ),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        files[relative] = {
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    if not files:
        raise ValueError(f"Local model directory contains no hashable files: {root}")
    aggregate = hashlib.sha256()
    aggregate.update(b"nldbwrite-local-model-manifest-v1\n")
    for relative, item in files.items():
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(str(item["size_bytes"]).encode("ascii"))
        aggregate.update(b"\0")
        aggregate.update(str(item["sha256"]).encode("ascii"))
        aggregate.update(b"\n")
    return {
        "manifest_version": 1,
        "model_path": str(root),
        "file_count": len(files),
        "files": files,
        "aggregate_sha256": aggregate.hexdigest(),
    }


def verify_local_model(
    model_path: str | Path,
    expected_aggregate_sha256: str,
) -> dict[str, Any]:
    manifest = build_local_model_manifest(model_path)
    expected = str(expected_aggregate_sha256).casefold()
    actual = str(manifest["aggregate_sha256"]).casefold()
    if actual != expected:
        raise ValueError(
            "Local model aggregate SHA-256 mismatch: "
            f"expected {expected}, computed {actual}"
        )
    manifest["verified"] = True
    return manifest
