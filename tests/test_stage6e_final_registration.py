from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from scripts.data.audit_crudsql_stage6a import table_fingerprint
from scripts.data.create_stage6e_final_registration import create_stage6e_final_registration
from scripts.data.validate_stage6e_final_registration import validate_stage6e_final_registration


ROOT = Path(__file__).resolve().parents[1]
STAGE6E_DIR = ROOT / "stage6_final_registration_revision"
STAGE6B_DIR = ROOT / "stage6_crudsql_registration"
STAGE6C_SETUP_DIR = ROOT / "stage6_gold_review_setup"
STAGE6C_EXEC_DIR = ROOT / "stage6_gold_review_execution"
STAGE6C_R03_DIR = ROOT / "stage6_gold_review_r03_adjudication"
STAGE6C_R04_DIR = ROOT / "stage6_gold_review_r04_resolution"
STAGE6D_SETUP_DIR = ROOT / "stage6_corrected_gold_review_setup"
STAGE6D_EXEC_DIR = ROOT / "stage6_corrected_gold_review_execution"


def copy_stage6e(tmp_path: Path) -> Path:
    target = tmp_path / "stage6_final_registration_revision"
    shutil.copytree(STAGE6E_DIR, target)
    return target


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def canonical_sha256(value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def copy_stage6e_inputs(tmp_path: Path) -> dict[str, Path]:
    roots = {
        "stage6b": STAGE6B_DIR,
        "stage6c_setup": STAGE6C_SETUP_DIR,
        "stage6c_exec": STAGE6C_EXEC_DIR,
        "stage6c_r03": STAGE6C_R03_DIR,
        "stage6c_r04": STAGE6C_R04_DIR,
        "stage6d_setup": STAGE6D_SETUP_DIR,
        "stage6d_exec": STAGE6D_EXEC_DIR,
    }
    copied = {}
    for name, source in roots.items():
        target = tmp_path / source.name
        shutil.copytree(source, target)
        copied[name] = target
    copied["stage6e"] = tmp_path / "stage6e_out"
    return copied


def mutate_first_value(rows: list[dict], sample_id: str, suffix: str) -> int:
    for row in rows:
        if row["stage6_sample_id"] == sample_id:
            row["values"][0] = f"{row['values'][0]}{suffix}"
            column_index = row["column_indexes"][0]
            row["expected_inserted_row"][column_index] = row["values"][0]
            return column_index
    raise AssertionError(f"missing sample {sample_id}")


def mutate_first_parameter(rows: list[dict], sample_id: str, suffix: str, column_index: int) -> dict:
    for row in rows:
        if row["stage6_sample_id"] == sample_id:
            row["parameters"][0] = f"{row['parameters'][0]}{suffix}"
            row["expected_inserted_row"][column_index] = row["parameters"][0]
            return row
    raise AssertionError(f"missing sample {sample_id}")


def recompute_post_state(stage6b_dir: Path, program: dict) -> str:
    table_name = f"Table_{program['table_id']}"
    db_rel = program.get("isolated_db") or f"isolated_table_dbs/crudsql_db_{program['table_id']}.sqlite"
    source = sqlite3.connect(stage6b_dir / db_rel)
    con = sqlite3.connect(":memory:")
    try:
        source.backup(con)
    finally:
        source.close()
    try:
        con.execute(program["sql_template"], program["parameters"])
        return table_fingerprint(con, table_name)["initial_state_sha256"]
    finally:
        con.close()


def update_original_post_hash(rows: list[dict], sample_id: str, post_state_sha256: str) -> None:
    for row in rows:
        if row["stage6_sample_id"] == sample_id:
            row["post_state_sha256"] = post_state_sha256
            return
    raise AssertionError(f"missing sample {sample_id}")


def update_corrected_post_hash(rows: list[dict], sample_id: str, post_state_sha256: str) -> None:
    for row in rows:
        if row["stage6_sample_id"] == sample_id:
            row["corrected_post_state_sha256"] = post_state_sha256
            return
    raise AssertionError(f"missing sample {sample_id}")


def original_review_plan_subset(plan: dict) -> dict:
    return {
        "column_indexes": plan["column_indexes"],
        "columns": plan["columns"],
        "expected_inserted_row": plan["expected_inserted_row"],
        "fresh_db_per_sample": True,
        "operation": "INSERT",
        "values": plan["values"],
    }


def review_program_subset(program: dict) -> dict:
    return {
        "parameters": program["parameters"],
        "sql_template": program["sql_template"],
        "sqlite_parameter_style": program["sqlite_parameter_style"],
    }


def rewrite_original_review_item(
    stage6c_setup_dir: Path,
    sample_id: str,
    plan: dict,
    program: dict,
    post_state_sha256: str,
) -> None:
    path = stage6c_setup_dir / "artifacts" / "gold_review_items.jsonl"
    rows = read_jsonl(path)
    for row in rows:
        if row["stage6_sample_id"] == sample_id:
            row["gold_write_plan"] = original_review_plan_subset(plan)
            row["gold_program"] = review_program_subset(program)
            row["hashes"]["post_state_sha256"] = post_state_sha256
            row["hashes"]["gold_write_plan_sha256"] = canonical_sha256(row["gold_write_plan"])
            row["hashes"]["gold_program_sha256"] = canonical_sha256(row["gold_program"])
            content = {key: value for key, value in row.items() if key != "authored_content_sha256"}
            row["authored_content_sha256"] = canonical_sha256(content)
            write_jsonl(path, rows)
            return
    raise AssertionError(f"missing sample {sample_id}")


def rewrite_corrected_review_item(
    stage6d_setup_dir: Path,
    sample_id: str,
    plan: dict,
    program: dict,
    post_state_sha256: str,
) -> None:
    path = stage6d_setup_dir / "artifacts" / "corrected_gold_review_items.jsonl"
    rows = read_jsonl(path)
    for row in rows:
        if row["stage6_sample_id"] == sample_id:
            row["corrected_gold_write_plan"] = original_review_plan_subset(plan)
            row["corrected_gold_program"] = review_program_subset(program)
            row["corrected_hashes"]["corrected_post_state_sha256"] = post_state_sha256
            row["corrected_hashes"]["corrected_gold_write_plan_sha256"] = canonical_sha256(
                row["corrected_gold_write_plan"]
            )
            row["corrected_hashes"]["corrected_gold_program_sha256"] = canonical_sha256(
                row["corrected_gold_program"]
            )
            content = {key: value for key, value in row.items() if key != "corrected_authored_content_sha256"}
            row["corrected_authored_content_sha256"] = canonical_sha256(content)
            write_jsonl(path, rows)
            return
    raise AssertionError(f"missing sample {sample_id}")


def test_stage6e_final_registration_validator_passes_repo_artifact() -> None:
    report = validate_stage6e_final_registration(STAGE6E_DIR)

    assert report["status"] == "PASS"
    assert report["original_registered_n"] == 500
    assert report["source_task_invalid_n"] == 19
    assert report["replacement_samples"] == 0
    assert report["final_confirmation_n"] == 481
    assert report["original_review_accepted_count"] == 460
    assert report["corrected_review_accepted_count"] == 21
    assert report["final_gold_replay_pass_count"] == 481
    assert report["final_gold_freeze_created"] is True
    assert report["confirmation_run_allowed_now"] is False
    assert report["model_called"] is False
    assert report["gpu_called"] is False

    provenance = read_jsonl(STAGE6E_DIR / "artifacts" / "FINAL_REVIEWED_GOLD_PROVENANCE.jsonl")
    approval_counts = {}
    for row in provenance:
        approval_counts[row["approval_path"]] = approval_counts.get(row["approval_path"], 0) + 1
    assert len(provenance) == 481
    assert approval_counts == {
        "C01_C02_CORRECTED_APPROVED": 21,
        "R01_R02_AGREED_APPROVED": 431,
        "R03_ADJUDICATED_APPROVED": 29,
    }


def test_stage6e_rejects_replacement_policy_mutation(tmp_path: Path) -> None:
    stage6e = copy_stage6e(tmp_path)
    path = stage6e / "STAGE6E_FINAL_REGISTRATION_LOCK.json"
    lock = json.loads(path.read_text(encoding="utf-8"))
    lock["replacement_samples"] = 1
    write_json(path, lock)

    report = validate_stage6e_final_registration(stage6e)

    assert report["status"] == "FAIL"
    assert "lock_replacement_samples_mismatch" in report["violations"]


def test_stage6e_rejects_final_manifest_row_removal(tmp_path: Path) -> None:
    stage6e = copy_stage6e(tmp_path)
    path = stage6e / "artifacts" / "FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl"
    rows = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")

    report = validate_stage6e_final_registration(stage6e)

    assert report["status"] == "FAIL"
    assert "final_confirmation_sample_manifest_mismatch" in report["violations"]


def test_stage6e_rejects_invalid_exclusion_mutation(tmp_path: Path) -> None:
    stage6e = copy_stage6e(tmp_path)
    path = stage6e / "artifacts" / "SOURCE_TASK_INVALID_EXCLUSIONS.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["replacement_sample"] = "stage6_crudsql_0499"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )

    report = validate_stage6e_final_registration(stage6e)

    assert report["status"] == "FAIL"
    assert "source_task_invalid_exclusions_mismatch" in report["violations"]


def test_stage6e_rejects_final_gold_program_mutation(tmp_path: Path) -> None:
    stage6e = copy_stage6e(tmp_path)
    path = stage6e / "artifacts" / "FINAL_GOLD_PROGRAMS.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["parameters"][0] = "MUTATED"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )

    report = validate_stage6e_final_registration(stage6e)

    assert report["status"] == "FAIL"
    assert "final_gold_programs_mismatch" in report["violations"]


def test_stage6e_rejects_overlap_audit_mutation(tmp_path: Path) -> None:
    stage6e = copy_stage6e(tmp_path)
    path = stage6e / "artifacts" / "FINAL_OVERLAP_AUDIT.json"
    audit = json.loads(path.read_text(encoding="utf-8"))
    audit["sample_id_overlap_count"] = 1
    write_json(path, audit)

    report = validate_stage6e_final_registration(stage6e)

    assert report["status"] == "FAIL"
    assert "final_overlap_audit_mismatch" in report["violations"]


def test_stage6e_rejects_original_gold_mutated_after_review(tmp_path: Path) -> None:
    dirs = copy_stage6e_inputs(tmp_path)
    sample_id = "stage6_crudsql_0001"
    suffix = "_MUTATED_AFTER_REVIEW"

    plan_path = dirs["stage6b"] / "artifacts" / "gold_write_plans.jsonl"
    plans = read_jsonl(plan_path)
    column_index = mutate_first_value(plans, sample_id, suffix)
    write_jsonl(plan_path, plans)

    program_path = dirs["stage6b"] / "artifacts" / "gold_programs.jsonl"
    programs = read_jsonl(program_path)
    mutated_program = mutate_first_parameter(programs, sample_id, suffix, column_index)
    post_state_sha256 = recompute_post_state(dirs["stage6b"], mutated_program)
    mutated_program["post_state_sha256"] = post_state_sha256
    write_jsonl(program_path, programs)

    post_path = dirs["stage6b"] / "artifacts" / "gold_post_state_hashes.jsonl"
    posts = read_jsonl(post_path)
    update_original_post_hash(posts, sample_id, post_state_sha256)
    write_jsonl(post_path, posts)

    lock = create_stage6e_final_registration(
        stage6b_dir=dirs["stage6b"],
        stage6c_setup_dir=dirs["stage6c_setup"],
        stage6c_exec_dir=dirs["stage6c_exec"],
        stage6c_r03_dir=dirs["stage6c_r03"],
        stage6c_r04_dir=dirs["stage6c_r04"],
        stage6d_setup_dir=dirs["stage6d_setup"],
        stage6d_exec_dir=dirs["stage6d_exec"],
        out_dir=dirs["stage6e"],
    )

    assert lock["status"] == "FAIL"
    assert f"original_reviewed_gold_plan_mismatch:{sample_id}" in lock["validation_violations"]
    assert f"original_reviewed_gold_program_mismatch:{sample_id}" in lock["validation_violations"]
    assert f"original_reviewed_post_state_mismatch:{sample_id}" in lock["validation_violations"]
    assert "final_gold_replay_failed" not in lock["validation_violations"]


def test_stage6e_rejects_corrected_gold_mutated_after_review(tmp_path: Path) -> None:
    dirs = copy_stage6e_inputs(tmp_path)
    sample_id = "stage6_crudsql_0000"
    suffix = "_MUTATED_AFTER_REVIEW"

    plan_path = dirs["stage6d_setup"] / "artifacts" / "corrected_gold_write_plans.jsonl"
    plans = read_jsonl(plan_path)
    column_index = mutate_first_value(plans, sample_id, suffix)
    write_jsonl(plan_path, plans)

    program_path = dirs["stage6d_setup"] / "artifacts" / "corrected_gold_programs.jsonl"
    programs = read_jsonl(program_path)
    mutated_program = mutate_first_parameter(programs, sample_id, suffix, column_index)
    post_state_sha256 = recompute_post_state(dirs["stage6b"], mutated_program)
    mutated_program["post_state_sha256"] = post_state_sha256
    write_jsonl(program_path, programs)

    post_path = dirs["stage6d_setup"] / "artifacts" / "corrected_gold_post_state_hashes.jsonl"
    posts = read_jsonl(post_path)
    update_corrected_post_hash(posts, sample_id, post_state_sha256)
    write_jsonl(post_path, posts)

    lock = create_stage6e_final_registration(
        stage6b_dir=dirs["stage6b"],
        stage6c_setup_dir=dirs["stage6c_setup"],
        stage6c_exec_dir=dirs["stage6c_exec"],
        stage6c_r03_dir=dirs["stage6c_r03"],
        stage6c_r04_dir=dirs["stage6c_r04"],
        stage6d_setup_dir=dirs["stage6d_setup"],
        stage6d_exec_dir=dirs["stage6d_exec"],
        out_dir=dirs["stage6e"],
    )

    assert lock["status"] == "FAIL"
    assert f"corrected_reviewed_gold_plan_mismatch:{sample_id}" in lock["validation_violations"]
    assert f"corrected_reviewed_gold_program_mismatch:{sample_id}" in lock["validation_violations"]
    assert f"corrected_reviewed_post_state_mismatch:{sample_id}" in lock["validation_violations"]
    assert "final_gold_replay_failed" not in lock["validation_violations"]


def test_stage6e_rejects_coordinated_original_gold_and_review_item_mutation(tmp_path: Path) -> None:
    dirs = copy_stage6e_inputs(tmp_path)
    sample_id = "stage6_crudsql_0001"
    suffix = "_COORDINATED_MUTATION"

    plan_path = dirs["stage6b"] / "artifacts" / "gold_write_plans.jsonl"
    plans = read_jsonl(plan_path)
    column_index = mutate_first_value(plans, sample_id, suffix)
    mutated_plan = next(row for row in plans if row["stage6_sample_id"] == sample_id)
    write_jsonl(plan_path, plans)

    program_path = dirs["stage6b"] / "artifacts" / "gold_programs.jsonl"
    programs = read_jsonl(program_path)
    mutated_program = mutate_first_parameter(programs, sample_id, suffix, column_index)
    post_state_sha256 = recompute_post_state(dirs["stage6b"], mutated_program)
    mutated_program["post_state_sha256"] = post_state_sha256
    write_jsonl(program_path, programs)

    post_path = dirs["stage6b"] / "artifacts" / "gold_post_state_hashes.jsonl"
    posts = read_jsonl(post_path)
    update_original_post_hash(posts, sample_id, post_state_sha256)
    write_jsonl(post_path, posts)

    rewrite_original_review_item(dirs["stage6c_setup"], sample_id, mutated_plan, mutated_program, post_state_sha256)

    lock = create_stage6e_final_registration(
        stage6b_dir=dirs["stage6b"],
        stage6c_setup_dir=dirs["stage6c_setup"],
        stage6c_exec_dir=dirs["stage6c_exec"],
        stage6c_r03_dir=dirs["stage6c_r03"],
        stage6c_r04_dir=dirs["stage6c_r04"],
        stage6d_setup_dir=dirs["stage6d_setup"],
        stage6d_exec_dir=dirs["stage6d_exec"],
        out_dir=dirs["stage6e"],
    )

    assert lock["status"] == "FAIL"
    assert "accepted_upstream_root_mismatch:stage6c_gold_review_items_sha256" in lock["validation_violations"]
    assert f"original_reviewed_gold_plan_mismatch:{sample_id}" not in lock["validation_violations"]
    assert "final_gold_replay_failed" not in lock["validation_violations"]


def test_stage6e_rejects_coordinated_corrected_gold_and_review_item_mutation(tmp_path: Path) -> None:
    dirs = copy_stage6e_inputs(tmp_path)
    sample_id = "stage6_crudsql_0000"
    suffix = "_COORDINATED_MUTATION"

    plan_path = dirs["stage6d_setup"] / "artifacts" / "corrected_gold_write_plans.jsonl"
    plans = read_jsonl(plan_path)
    column_index = mutate_first_value(plans, sample_id, suffix)
    mutated_plan = next(row for row in plans if row["stage6_sample_id"] == sample_id)
    write_jsonl(plan_path, plans)

    program_path = dirs["stage6d_setup"] / "artifacts" / "corrected_gold_programs.jsonl"
    programs = read_jsonl(program_path)
    mutated_program = mutate_first_parameter(programs, sample_id, suffix, column_index)
    post_state_sha256 = recompute_post_state(dirs["stage6b"], mutated_program)
    mutated_program["post_state_sha256"] = post_state_sha256
    write_jsonl(program_path, programs)

    post_path = dirs["stage6d_setup"] / "artifacts" / "corrected_gold_post_state_hashes.jsonl"
    posts = read_jsonl(post_path)
    update_corrected_post_hash(posts, sample_id, post_state_sha256)
    write_jsonl(post_path, posts)

    rewrite_corrected_review_item(dirs["stage6d_setup"], sample_id, mutated_plan, mutated_program, post_state_sha256)

    lock = create_stage6e_final_registration(
        stage6b_dir=dirs["stage6b"],
        stage6c_setup_dir=dirs["stage6c_setup"],
        stage6c_exec_dir=dirs["stage6c_exec"],
        stage6c_r03_dir=dirs["stage6c_r03"],
        stage6c_r04_dir=dirs["stage6c_r04"],
        stage6d_setup_dir=dirs["stage6d_setup"],
        stage6d_exec_dir=dirs["stage6d_exec"],
        out_dir=dirs["stage6e"],
    )

    assert lock["status"] == "FAIL"
    assert "accepted_upstream_root_mismatch:stage6d_corrected_review_items_sha256" in lock["validation_violations"]
    assert f"corrected_reviewed_gold_plan_mismatch:{sample_id}" not in lock["validation_violations"]
    assert "final_gold_replay_failed" not in lock["validation_violations"]
