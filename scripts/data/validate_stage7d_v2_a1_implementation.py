from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUT_DIR = ROOT / "stage7d_v2_a1_implementation"
LOCK = OUT_DIR / "STAGE7D_IMPLEMENTATION_LOCK.json"
PASS_STATUS = "PASS_STAGE7D_V2_A1_IMPLEMENTATION_LOCKED"
FROZEN_VALIDATED_UTC = "2026-08-27T00:00:00+00:00"

from nldbwrite_v3.v2_a1.diagnostics import primary_pipeline_source_uses_oracle  # noqa: E402


def canonical_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".yaml", ".yml"}:
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        return text.encode("utf-8")
    return data


def sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(canonical_bytes(path)).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def artifact_paths() -> list[Path]:
    return sorted(p for p in OUT_DIR.iterdir() if p.is_file() and p.name != "STAGE7D_IMPLEMENTATION_LOCK.json")


def validate() -> dict[str, Any]:
    violations: list[str] = []
    if not OUT_DIR.exists():
        violations.append("missing_stage7d_artifact_directory")
        return {"status": "FAIL", "violations": violations}
    if not LOCK.exists():
        violations.append("missing_stage7d_lock")
        return {"status": "FAIL", "violations": violations}

    lock = read_json(LOCK)
    input_manifest = read_json(OUT_DIR / "STAGE7D_INPUT_MANIFEST.json")
    component_manifest = read_json(OUT_DIR / "IMPLEMENTATION_COMPONENT_MANIFEST.json")
    no_model = read_json(OUT_DIR / "NO_MODEL_EXECUTION_AUDIT.json")
    tests = read_json(OUT_DIR / "TEST_SUMMARY.json")

    stage7b = read_json(ROOT / "stage7b_v2_method_specification/STAGE7B_V2_SPECIFICATION_LOCK.json")
    stage7b_a1 = read_json(ROOT / "stage7b_a1_free_text_slot_discovery_amendment/STAGE7B_A1_LOCK.json")
    stage7c_a1 = read_json(ROOT / "stage7c_a1_v2_development_protocol/STAGE7C_A1_PROTOCOL_LOCK.json")
    generation = read_json(ROOT / "stage7c_a1_v2_development_protocol/GENERATION_PROTOCOL_A1.json")

    expected_statuses = {
        "Stage7B": (stage7b.get("status"), "PASS_V2_METHOD_SPECIFICATION_LOCKED"),
        "Stage7B-A1": (stage7b_a1.get("status"), "PASS_STAGE7B_A1_FREE_TEXT_SLOT_DISCOVERY_AMENDMENT_LOCKED"),
        "Stage7C-A1": (stage7c_a1.get("status"), "PASS_STAGE7C_A1_V2_DEVELOPMENT_PROTOCOL_LOCKED"),
    }
    for name, (actual, expected) in expected_statuses.items():
        if actual != expected:
            violations.append(f"{name}_status_not_locked:{actual}")

    if stage7c_a1.get("phase_o_model_calls") != 1 or stage7c_a1.get("phase_m_model_calls") != 1:
        violations.append("stage7c_a1_model_call_count_changed")
    if generation.get("model_config", {}).get("model_id") != "Qwen/Qwen2.5-Coder-7B-Instruct":
        violations.append("model_id_not_stage6i_qwen_7b")
    if generation.get("model_config", {}).get("model_revision") != "c03e6d358207e414f1eca0bb1891e29f1db0e242":
        violations.append("model_revision_not_literal_stage6i_revision")

    for rel_path, expected_hash in input_manifest.get("input_hashes", {}).items():
        path = ROOT / rel_path
        if not path.exists():
            violations.append(f"missing_input:{rel_path}")
        elif sha256_file(path) != expected_hash:
            violations.append(f"input_hash_mismatch:{rel_path}")

    for rel_path, expected_hash in component_manifest.get("code_hashes", {}).items():
        path = ROOT / rel_path
        if not path.exists():
            violations.append(f"missing_code:{rel_path}")
        elif sha256_file(path) != expected_hash:
            violations.append(f"code_hash_mismatch:{rel_path}")

    for rel_path, expected_hash in component_manifest.get("test_hashes", {}).items():
        path = ROOT / rel_path
        if not path.exists():
            violations.append(f"missing_test:{rel_path}")
        elif sha256_file(path) != expected_hash:
            violations.append(f"test_hash_mismatch:{rel_path}")

    for rel_path, expected_hash in component_manifest.get("script_hashes", {}).items():
        path = ROOT / rel_path
        if not path.exists():
            violations.append(f"missing_script:{rel_path}")
        elif sha256_file(path) != expected_hash:
            violations.append(f"script_hash_mismatch:{rel_path}")

    if int(tests.get("collected_test_cases", 0)) < 70:
        violations.append("stage7d_test_count_below_gate")
    if "100 passed" not in str(tests.get("last_observed_result", "")):
        violations.append("stage7d_test_summary_not_updated")

    forbidden_true = [
        "model_called",
        "gpu_called",
        "qwen_generation_run",
        "train_dev_generation_run",
        "experiment_run",
        "live_sql_bench_gt_opened",
        "confirmation_481_evaluated",
    ]
    for key in forbidden_true:
        if no_model.get(key) is not False:
            violations.append(f"forbidden_execution_flag_true:{key}")

    if primary_pipeline_source_uses_oracle(ROOT):
        violations.append("primary_pipeline_uses_oracle_source")

    for rel_path in component_manifest.get("code_files", []) + component_manifest.get("test_files", []) + component_manifest.get("script_files", []):
        if not (ROOT / rel_path).exists():
            violations.append(f"missing_manifest_path:{rel_path}")

    report_status = "PASS" if not violations else "FAIL"
    report = (
        "# Stage7D Validation Report\n\n"
        f"Status: {report_status}\n\n"
        f"violations: {json.dumps(violations, ensure_ascii=False)}\n\n"
        "Checks:\n"
        "- upstream Stage7B/Stage7B-A1/Stage7C-A1 locks recomputed\n"
        "- input/code/test hashes recomputed\n"
        "- model/GPU/experiment forbidden flags checked\n"
        "- primary pipeline oracle isolation checked\n"
        "- Stage7D test-count gate checked\n"
    )
    (OUT_DIR / "VALIDATION_REPORT.md").write_text(report, encoding="utf-8")

    artifact_hashes = {p.name: sha256_file(p) for p in artifact_paths()}
    if not violations:
        lock["status"] = PASS_STATUS
    else:
        lock["status"] = "FAIL_STAGE7D_VALIDATION"
    lock["validated_utc"] = FROZEN_VALIDATED_UTC
    lock["violations"] = violations
    lock["artifact_hashes"] = artifact_hashes
    lock["model_called"] = False
    lock["gpu_called"] = False
    lock["experiment_run"] = False
    lock["v2_generation_run"] = False
    write_json(LOCK, lock)

    return {"status": report_status, "violations": violations, "artifact_hashes": artifact_hashes}


def main() -> None:
    result = validate()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
