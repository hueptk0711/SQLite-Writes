from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_BUNDLE_NAME = "mp_fs_plus_calibration_gpu_20260729"
MANIFEST_NAME = "BUNDLE_MANIFEST.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def excluded(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return True
    if any(
        part in {
            ".git",
            ".pytest_cache",
            "__pycache__",
            ".venv",
            ".venv_cpu",
            ".venv_gpu",
            "_local_validation",
            "test_tmp",
        }
        for part in parts
    ):
        return True
    if parts[0] in {"dist", "experiments"}:
        return True
    if relative.suffix in {".pyc", ".pyo"}:
        return True
    return False


def source_files(root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.name != MANIFEST_NAME
            and not excluded(path.relative_to(root))
        ),
        key=lambda item: item.relative_to(root).as_posix(),
    )


def write_manifest(
    root: Path,
    files: list[Path],
    *,
    bundle_name: str,
    purpose: str,
) -> Path:
    entries = {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        entries[relative] = {
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    manifest = {
        "manifest_version": 1,
        "bundle_name": bundle_name,
        "created_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "purpose": purpose,
        "paper_result_eligible": False,
        "file_count_excluding_manifest": len(entries),
        "files": entries,
    }
    target = root / MANIFEST_NAME
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def add_file(
    archive: tarfile.TarFile,
    root: Path,
    path: Path,
    *,
    bundle_name: str,
) -> None:
    relative = path.relative_to(root).as_posix()
    arcname = f"{bundle_name}/{relative}"
    data = path.read_bytes()
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o755 if path.suffix == ".sh" else 0o644
    from io import BytesIO

    archive.addfile(info, BytesIO(data))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output-dir", default="..")
    parser.add_argument("--bundle-name")
    parser.add_argument(
        "--purpose",
        default="locked_gpu_calibration_source_bundle",
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    bundle_name = str(args.bundle_name or root.name or DEFAULT_BUNDLE_NAME)
    if not bundle_name or "/" in bundle_name or "\\" in bundle_name:
        raise SystemExit(f"Invalid bundle name: {bundle_name!r}")
    output_dir.mkdir(parents=True, exist_ok=True)

    files = source_files(root)
    manifest_path = write_manifest(
        root,
        files,
        bundle_name=bundle_name,
        purpose=str(args.purpose),
    )
    archive_files = sorted(
        [*files, manifest_path],
        key=lambda item: item.relative_to(root).as_posix(),
    )

    archive_path = output_dir / f"{bundle_name}.tar.gz"
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in archive_files:
                    add_file(
                        archive,
                        root,
                        path,
                        bundle_name=bundle_name,
                    )

    archive_sha256 = sha256_file(archive_path)
    checksum_path = Path(f"{archive_path}.sha256")
    checksum_path.write_text(
        f"{archive_sha256}  {archive_path.name}\n",
        encoding="utf-8",
        newline="\n",
    )
    result = {
        "bundle": str(archive_path),
        "bundle_sha256": archive_sha256,
        "checksum": str(checksum_path),
        "archive_root": bundle_name,
        "file_count": len(archive_files),
        "size_bytes": archive_path.stat().st_size,
        "manifest": str(manifest_path),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
