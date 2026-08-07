from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path


BUNDLE_ID = "mp_fs_plus_robustness_extension_20260801"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]
    source = workspace / "03_protocol_and_data" / "robustness_extension_20260801"
    output_dir = workspace / "09_release_candidate"
    output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in source.rglob("*") if path.is_file())
    manifest = {
        "bundle_id": BUNDLE_ID,
        "status": "ready_for_server_freeze_then_gpu_run",
        "base_primary_results_modified": False,
        "second_model_requires_gpu": True,
        "ablation_results_present": False,
        "files": {
            path.relative_to(source).as_posix(): {
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in files
        },
    }
    manifest_payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for path in files:
                relative = path.relative_to(source).as_posix()
                info = archive.gettarinfo(str(path), arcname=f"{BUNDLE_ID}/{relative}")
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                if path.suffix == ".sh":
                    info.mode = 0o755
                with path.open("rb") as handle:
                    archive.addfile(info, handle)
            info = tarfile.TarInfo(f"{BUNDLE_ID}/BUNDLE_MANIFEST.json")
            info.size = len(manifest_payload)
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(manifest_payload))
    payload = buffer.getvalue()
    archive_path = output_dir / f"{BUNDLE_ID}.tar.gz"
    archive_path.write_bytes(payload)
    checksum = sha256_bytes(payload)
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    checksum_path.write_text(f"{checksum}  {archive_path.name}\n", encoding="ascii")
    print(
        json.dumps(
            {
                "status": "pass",
                "archive": str(archive_path),
                "sha256": checksum,
                "file_count": len(files),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
