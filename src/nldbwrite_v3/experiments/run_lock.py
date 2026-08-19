from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from nldbwrite_v3.common import load_json, sha256_file
from nldbwrite_v3.evaluator import find_database


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _code_file_hashes(project_root: Path) -> dict[str, str]:
    files: list[Path] = []
    for relative_root in ("src", "scripts", "schemas"):
        root = project_root / relative_root
        if root.exists():
            files.extend(
                path
                for path in root.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix.casefold() in {".py", ".json"}
            )
    for filename in ("pyproject.toml",):
        path = project_root / filename
        if path.exists():
            files.append(path)
    return {
        path.relative_to(project_root).as_posix(): sha256_file(path)
        for path in sorted(
            set(files),
            key=lambda item: item.relative_to(project_root).as_posix(),
        )
    }


def build_run_lock(
    *,
    project_root: str | Path,
    stage: str,
    method_id: str,
    method_config_path: str | Path,
    inference_config_path: str | Path | None,
    base_config_path: str | Path | None,
    resolved_config_sha256: str,
    dataset_path: str | Path,
    split_path: str | Path,
    gold_plans_path: str | Path | None,
    profile_dir: str | Path,
    db_root: str | Path,
    selected_db_ids: Iterable[str],
    prompt_set_sha256: str,
    model_metadata: dict[str, Any],
    dependency_lock_path: str | Path | None,
    environment_manifest_path: str | Path | None,
    v2_source_path: str | Path | None = None,
    final_protocol_path: str | Path | None = None,
    method_variant: str | None = None,
    method_version: str | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    db_ids = sorted(set(str(item) for item in selected_db_ids))
    profiles = Path(profile_dir)
    profile_hashes: dict[str, str] = {}
    database_hashes: dict[str, str] = {}
    for db_id in db_ids:
        profile_path = profiles / f"{db_id}.json"
        if not profile_path.exists():
            raise ValueError(f"Profile not found for run lock: {profile_path}")
        profile_hashes[db_id] = sha256_file(profile_path)
        database_hashes[db_id] = sha256_file(find_database(db_root, db_id))

    code_files = _code_file_hashes(root)
    v2_code_files: dict[str, str] = {}
    if v2_source_path is not None:
        v2_root = Path(v2_source_path).resolve()
        if not (v2_root / "nldbwrite").is_dir():
            raise ValueError(
                "v2 source path must be the directory containing nldbwrite/"
            )
        v2_code_files = {
            path.relative_to(v2_root).as_posix(): sha256_file(path)
            for path in sorted(
                (v2_root / "nldbwrite").rglob("*.py"),
                key=lambda item: item.relative_to(v2_root).as_posix(),
            )
            if "__pycache__" not in path.parts
        }
    prompt_templates = {
        relative: digest
        for relative, digest in code_files.items()
        if relative
        in {
            "src/nldbwrite_v3/planner/prompt.py",
            "src/nldbwrite_v3/experiments/prompts.py",
        }
    }
    dependency_hash = (
        sha256_file(dependency_lock_path)
        if dependency_lock_path is not None
        else None
    )
    environment_hash = (
        sha256_file(environment_manifest_path)
        if environment_manifest_path is not None
        else None
    )
    environment_status = None
    if environment_manifest_path is not None:
        environment_status = load_json(environment_manifest_path).get("status")

    model_manifest = model_metadata.get("model_manifest") or {}
    identity = {
        "backend": model_metadata.get("backend"),
        "model_name_or_path": model_metadata.get("model_name_or_path"),
        "revision": model_metadata.get("revision"),
        "model_hash": model_metadata.get("model_hash"),
        "model_aggregate_sha256": model_manifest.get("aggregate_sha256"),
        "tokenizer_sha256": model_manifest.get("tokenizer_sha256"),
        "chat_template_sha256": model_manifest.get("chat_template_sha256"),
        "model_config_sha256": model_manifest.get("model_config_sha256"),
        "generation_config_sha256": model_manifest.get(
            "generation_config_sha256"
        ),
    }
    lock = {
        "lock_version": 1,
        "stage": stage,
        "method_id": method_id,
        **({"method_variant": method_variant} if method_variant is not None else {}),
        **({"method_version": method_version} if method_version is not None else {}),
        "hashes": {
            "method_config_sha256": sha256_file(method_config_path),
            "inference_config_sha256": (
                sha256_file(inference_config_path)
                if inference_config_path is not None
                else None
            ),
            "base_config_sha256": (
                sha256_file(base_config_path)
                if base_config_path is not None
                else None
            ),
            "resolved_config_sha256": resolved_config_sha256,
            "dataset_sha256": sha256_file(dataset_path),
            "split_sha256": sha256_file(split_path),
            "gold_plans_sha256": (
                sha256_file(gold_plans_path)
                if gold_plans_path is not None
                else None
            ),
            "profile_aggregate_sha256": _canonical_sha256(profile_hashes),
            "database_aggregate_sha256": _canonical_sha256(database_hashes),
            "source_code_tree_sha256": _canonical_sha256(code_files),
            "v2_source_tree_sha256": (
                _canonical_sha256(v2_code_files)
                if v2_code_files
                else None
            ),
            "prompt_template_sha256": _canonical_sha256(prompt_templates),
            "prompt_set_sha256": prompt_set_sha256,
            "dependency_lock_sha256": dependency_hash,
            "environment_manifest_sha256": environment_hash,
            "final_protocol_sha256": (
                sha256_file(final_protocol_path)
                if final_protocol_path is not None
                else None
            ),
        },
        "profiles": profile_hashes,
        "databases": database_hashes,
        "model": identity,
        "environment_status": environment_status,
    }
    lock["run_lock_sha256"] = _canonical_sha256(lock)
    return lock


def verify_or_create_run_lock(
    lock: dict[str, Any],
    path: str | Path,
    *,
    resume: bool,
) -> dict[str, Any]:
    target = Path(path)
    if target.exists() and resume:
        prior = load_json(target)
        if prior.get("run_lock_sha256") != lock.get("run_lock_sha256"):
            changed = [
                key
                for key in sorted(
                    set((prior.get("hashes") or {}))
                    | set((lock.get("hashes") or {}))
                )
                if (prior.get("hashes") or {}).get(key)
                != (lock.get("hashes") or {}).get(key)
            ]
            raise ValueError(
                "Resume rejected because run_lock changed"
                + (f": {', '.join(changed)}" if changed else "")
            )
        return prior
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return lock
