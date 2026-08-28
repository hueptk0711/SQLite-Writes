from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import V2A1Error


PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".yaml", ".yml"}
STAGE7D_PASS_STATUS = "PASS_STAGE7D_V2_A1_IMPLEMENTATION_LOCKED"


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES:
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        data = text.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class V2A1Protocol:
    root: Path
    stage7b_lock: dict[str, Any]
    stage7b_a1_lock: dict[str, Any]
    stage7c_a1_lock: dict[str, Any]

    @property
    def model_revision(self) -> str:
        return self.stage7c_a1_lock["input_hashes"] and read_json(self.root / "stage7c_a1_v2_development_protocol/GENERATION_PROTOCOL_A1.json")["model_config"]["model_revision"]

    @property
    def phase_o_model_calls(self) -> int:
        return int(self.stage7c_a1_lock["phase_o_model_calls"])

    @property
    def phase_m_model_calls(self) -> int:
        return int(self.stage7c_a1_lock["phase_m_model_calls"])


def load_v2_a1_protocol(root: Path = PROJECT_ROOT) -> V2A1Protocol:
    _verify_stage7d_runtime_integrity(root)
    stage7b = read_json(root / "stage7b_v2_method_specification/STAGE7B_V2_SPECIFICATION_LOCK.json")
    stage7b_a1 = read_json(root / "stage7b_a1_free_text_slot_discovery_amendment/STAGE7B_A1_LOCK.json")
    stage7c_a1 = read_json(root / "stage7c_a1_v2_development_protocol/STAGE7C_A1_PROTOCOL_LOCK.json")
    if stage7b.get("status") != "PASS_V2_METHOD_SPECIFICATION_LOCKED":
        raise V2A1Error("protocol_stage7b_not_locked", "Stage7B lock is not accepted")
    if stage7b_a1.get("status") != "PASS_STAGE7B_A1_FREE_TEXT_SLOT_DISCOVERY_AMENDMENT_LOCKED":
        raise V2A1Error("protocol_stage7b_a1_not_locked", "Stage7B-A1 lock is not accepted")
    if stage7c_a1.get("status") != "PASS_STAGE7C_A1_V2_DEVELOPMENT_PROTOCOL_LOCKED":
        raise V2A1Error("protocol_stage7c_a1_not_locked", "Stage7C-A1 lock is not accepted")
    if stage7c_a1.get("phase_o_model_calls") != 1 or stage7c_a1.get("phase_m_model_calls") != 1:
        raise V2A1Error("protocol_model_call_count_changed", "V2-A1 must use one Phase O call and one Phase M call")
    for key in ("model_called", "gpu_called", "v2_implemented", "experiment_run", "live_sql_bench_gt_opened"):
        if stage7c_a1.get(key) is not False:
            raise V2A1Error("protocol_forbidden_execution_flag", f"{key} must be false in Stage7C-A1")
    return V2A1Protocol(root=root, stage7b_lock=stage7b, stage7b_a1_lock=stage7b_a1, stage7c_a1_lock=stage7c_a1)


def _verify_stage7d_runtime_integrity(root: Path) -> None:
    lock_path = root / "stage7d_v2_a1_implementation/STAGE7D_IMPLEMENTATION_LOCK.json"
    manifest_path = root / "stage7d_v2_a1_implementation/STAGE7D_INPUT_MANIFEST.json"
    if not lock_path.exists() or not manifest_path.exists():
        raise V2A1Error("protocol_stage7d_lock_missing", "Stage7D runtime lock and input manifest are required")
    lock = read_json(lock_path)
    if lock.get("status") != STAGE7D_PASS_STATUS:
        raise V2A1Error("protocol_stage7d_not_locked", "Stage7D lock is not accepted")
    manifest = read_json(manifest_path)
    for rel_path, expected_hash in manifest.get("input_hashes", {}).items():
        path = root / rel_path
        if not path.exists():
            raise V2A1Error("protocol_hash_mismatch", "Locked upstream input is missing", details={"path": rel_path})
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise V2A1Error("protocol_hash_mismatch", "Locked upstream input hash mismatch", details={"path": rel_path, "expected": expected_hash, "actual": actual_hash})
