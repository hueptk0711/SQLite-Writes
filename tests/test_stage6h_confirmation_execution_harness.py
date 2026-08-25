from __future__ import annotations

import importlib.util
import json
import shutil
import uuid
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_module(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


harness = load_module("scripts/server/run_stage6_confirmation.py", "run_stage6_confirmation")
validator = load_module(
    "scripts/data/validate_stage6h_confirmation_execution_harness.py",
    "validate_stage6h_confirmation_execution_harness",
)


def fresh_dir() -> Path:
    root = PROJECT_ROOT / "stage6h_test_runs"
    root.mkdir(exist_ok=True)
    path = root / f"run_{uuid.uuid4().hex}"
    path.mkdir()
    return path


def audit_rows(stream: str = "direct") -> list[dict]:
    return [
        {
            "stage6_sample_id": f"stage6_crudsql_{index:04d}",
            "arm": stream,
            "prompt_sha256": f"prompt-{index}",
            "chat_prompt_sha256": f"chat-{index}",
            "input_ids_sha256": f"ids-{index}",
            "input_token_count": 100 + index,
        }
        for index in range(harness.FINAL_CONFIRMATION_N)
    ]


def audit_index(stream: str = "direct") -> dict[tuple[str, str], dict]:
    return {
        (stream, row["stage6_sample_id"]): row
        for row in audit_rows(stream)
    }


def shared_audit_index() -> dict[tuple[str, str], dict]:
    rows = audit_rows("d_g1_control") + audit_rows("d_f_g1_vnext")
    return {
        (row["arm"], row["stage6_sample_id"]): row
        for row in rows
    }


def generation_rows(request_rows: list[dict] | None = None) -> list[dict]:
    rows = request_rows if request_rows is not None else audit_rows()
    return [
        {
            "sample_id": row["stage6_sample_id"],
            "stage6_sample_id": row["stage6_sample_id"],
            "prompt_sha256": row["prompt_sha256"],
            "chat_prompt_sha256": row["chat_prompt_sha256"],
            "input_ids_sha256": row["input_ids_sha256"],
            "input_token_count": row["input_token_count"],
            "raw_output": f"output {index}",
            "output_tokens": 12,
            "hit_max_new_tokens": False,
            "generation_status": "success",
            "generation_error": None,
            "latency_sec": 0.01,
        }
        for index, row in enumerate(rows)
    ]


def metadata() -> dict:
    return {
        "model_revision": harness.MODEL_REVISION,
        "model_sha256": harness.MODEL_SHA256,
        "tokenizer_sha256": harness.TOKENIZER_SHA256,
        "generation_lock_sha256": harness.GENERATION_LOCK_SHA256,
    }


def test_stage6h_setup_validates():
    out_dir = fresh_dir() / "stage6_confirmation_execution"
    harness.create_setup(out_dir)
    report = validator.validate(out_dir)
    assert report["status"] == "PASS"
    assert report["confirmation_predictions_created"] is False
    assert report["confirmation_run_started"] is False
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_prompt_sha_mismatch_blocks_generation_callable():
    called = False
    rows = audit_rows()
    rows[0]["prompt_sha256"] = "bad"

    def generate(_requests):
        nonlocal called
        called = True
        return generation_rows(_requests)

    try:
        harness.run_stream_with_guard(
            stream="direct",
            expected_index=audit_index(),
            current_rows=rows,
            generation_callable=generate,
            output_root=fresh_dir(),
            run_id="test",
            metadata=metadata(),
        )
    except harness.HarnessError:
        pass
    assert called is False


def test_chat_prompt_sha_mismatch_blocks_generation_callable():
    called = False
    rows = audit_rows()
    rows[0]["chat_prompt_sha256"] = "bad"

    def generate(_requests):
        nonlocal called
        called = True
        return generation_rows(_requests)

    try:
        harness.run_stream_with_guard(
            stream="direct",
            expected_index=audit_index(),
            current_rows=rows,
            generation_callable=generate,
            output_root=fresh_dir(),
            run_id="test",
            metadata=metadata(),
        )
    except harness.HarnessError:
        pass
    assert called is False


def test_input_ids_sha_mismatch_blocks_generation_callable():
    called = False
    rows = audit_rows()
    rows[0]["input_ids_sha256"] = "bad"

    def generate(_requests):
        nonlocal called
        called = True
        return generation_rows(_requests)

    try:
        harness.run_stream_with_guard(
            stream="direct",
            expected_index=audit_index(),
            current_rows=rows,
            generation_callable=generate,
            output_root=fresh_dir(),
            run_id="test",
            metadata=metadata(),
        )
    except harness.HarnessError:
        pass
    assert called is False


def test_input_token_count_mismatch_blocks_generation_callable():
    called = False
    rows = audit_rows()
    rows[0]["input_token_count"] = 999999

    def generate(_requests):
        nonlocal called
        called = True
        return generation_rows(_requests)

    try:
        harness.run_stream_with_guard(
            stream="direct",
            expected_index=audit_index(),
            current_rows=rows,
            generation_callable=generate,
            output_root=fresh_dir(),
            run_id="test",
            metadata=metadata(),
        )
    except harness.HarnessError:
        pass
    assert called is False


def test_wrong_model_hash_stops_normalization():
    bad = metadata()
    bad["model_sha256"] = "0" * 64
    try:
        harness.normalize_raw_generation_row(
            stream="direct",
            audit_row=audit_rows()[0],
            generation_row=generation_rows()[0],
            run_id="test",
            metadata=bad,
        )
    except harness.HarnessError as exc:
        assert "Model/generation identity mismatch" in str(exc)
    else:
        raise AssertionError("expected HarnessError")


def test_wrong_git_head_stops_authorization_boundary():
    try:
        harness.verify_authorization_boundary(
            PROJECT_ROOT,
            expected_git_head="0" * 40,
            require_git_clean=False,
        )
    except harness.HarnessError as exc:
        assert "git_head_mismatch" in str(exc)
    else:
        raise AssertionError("expected HarnessError")


def test_dirty_worktree_stops_authorization_boundary():
    class FakeValidator:
        @staticmethod
        def validate(*_args, **_kwargs):
            return {"status": "FAIL", "violations": [{"code": "git_status_not_clean"}]}

    with mock.patch.object(harness, "load_stage6g_validator", return_value=FakeValidator):
        try:
            harness.verify_authorization_boundary(
                PROJECT_ROOT,
                expected_git_head=harness.STAGE6G_AUTHORIZATION_COMMIT,
                require_git_clean=True,
            )
        except harness.HarnessError as exc:
            assert "git_status_not_clean" in str(exc)
        else:
            raise AssertionError("expected HarnessError")


def test_stale_raw_output_blocks_clean_initial_run():
    root = fresh_dir()
    raw = root / "raw_generations" / "direct.jsonl"
    raw.parent.mkdir()
    raw.write_text('{"stale": true}\n', encoding="utf-8")
    try:
        harness.check_clean_initial_outputs(root)
    except harness.HarnessError as exc:
        assert "Pre-existing raw generation files" in str(exc)
    else:
        raise AssertionError("expected HarnessError")
    shutil.rmtree(root, ignore_errors=True)


def test_modified_completed_row_blocks_resume():
    root = fresh_dir()
    rows = harness.run_stream_with_guard(
        stream="direct",
        expected_index=audit_index(),
        current_rows=audit_rows(),
        generation_callable=generation_rows,
        output_root=root,
        run_id="test",
        metadata=metadata(),
    )
    raw_path = root / harness.STREAMS["direct"]["raw_generation_path"]
    checkpoint = raw_path.with_suffix(raw_path.suffix + ".checkpoint.json")
    rows[0]["raw_output"] = "tampered"
    harness.write_jsonl(raw_path, rows)
    try:
        harness.verify_resume_checkpoint(raw_path, checkpoint)
    except harness.HarnessError as exc:
        assert "Raw generation row hash mismatch" in str(exc)
    else:
        raise AssertionError("expected HarnessError")
    shutil.rmtree(root, ignore_errors=True)


def test_required_raw_row_fields_missing_fails():
    row = harness.normalize_raw_generation_row(
        stream="direct",
        audit_row=audit_rows()[0],
        generation_row=generation_rows()[0],
        run_id="test",
        metadata=metadata(),
    )
    row.pop("input_ids_sha256")
    try:
        harness.validate_raw_generation_rows([row])
    except harness.HarnessError as exc:
        assert "missing required fields" in str(exc)
    else:
        raise AssertionError("expected HarnessError")


def test_raw_rows_include_reuse_runner_sample_id():
    row = harness.normalize_raw_generation_row(
        stream="direct",
        audit_row=audit_rows()[0],
        generation_row=generation_rows()[0],
        run_id="test",
        metadata=metadata(),
    )
    assert row["sample_id"] == row["stage6_sample_id"]
    assert row["generation_status"] == "success"
    assert "generation_error" in row


def test_independent_dfg1_generation_attempt_fails():
    try:
        harness.run_stream_with_guard(
            stream="d_f_g1_vnext",
            expected_index=audit_index("d_f_g1_vnext"),
            current_rows=audit_rows("d_f_g1_vnext"),
            generation_callable=generation_rows,
            output_root=fresh_dir(),
            run_id="test",
            metadata=metadata(),
        )
    except harness.HarnessError as exc:
        assert "Unknown generation stream" in str(exc)
    else:
        raise AssertionError("expected HarnessError")


def test_shared_replay_row_sha_differs_fails():
    shared_rows = [
        harness.normalize_raw_generation_row(
            stream="shared_mp_fs_plus_generation",
            audit_row=audit_rows("shared_mp_fs_plus_generation")[0],
            generation_row=generation_rows()[0],
            run_id="test",
            metadata=metadata(),
        )
    ]
    d_g1 = harness.make_replay_provenance_rows(shared_rows, replay_arm="d_g1_control")
    d_f_g1 = harness.make_replay_provenance_rows(shared_rows, replay_arm="d_f_g1_vnext")
    d_f_g1[0]["shared_raw_generation_row_sha256"] = "bad"
    try:
        harness.validate_shared_replay_provenance(d_g1, d_f_g1)
    except harness.HarnessError as exc:
        assert "Shared replay row SHA differs" in str(exc)
    else:
        raise AssertionError("expected HarnessError")


def test_shared_generation_stream_uses_d_g1_audit_arm_and_checks_dfg1_identity():
    index = shared_audit_index()
    rows = harness.rows_for_stream(index, "shared_mp_fs_plus_generation")
    assert len(rows) == harness.FINAL_CONFIRMATION_N
    assert {row["arm"] for row in rows} == {"d_g1_control"}
    report = harness.verify_shared_stream_audit_identity(index)
    assert report["checked_pairs"] == harness.FINAL_CONFIRMATION_N


def test_shared_generation_stream_fails_when_dfg1_identity_differs():
    index = shared_audit_index()
    first_id = "stage6_crudsql_0000"
    index[("d_f_g1_vnext", first_id)]["input_ids_sha256"] = "different"
    try:
        harness.verify_shared_stream_audit_identity(index)
    except harness.HarnessError as exc:
        assert "H2 shared audit identity mismatch" in str(exc)
    else:
        raise AssertionError("expected HarnessError")


def test_duplicate_current_sample_ids_block_before_generation_callable():
    called = False
    rows = audit_rows()
    rows = [rows[0].copy() for _ in range(harness.FINAL_CONFIRMATION_N)]

    def generate(_requests):
        nonlocal called
        called = True
        return generation_rows(_requests)

    try:
        harness.run_stream_with_guard(
            stream="direct",
            expected_index=audit_index(),
            current_rows=rows,
            generation_callable=generate,
            output_root=fresh_dir(),
            run_id="test",
            metadata=metadata(),
        )
    except harness.HarnessError as exc:
        assert "duplicate sample IDs" in str(exc)
    else:
        raise AssertionError("expected HarnessError")
    assert called is False


def test_duplicate_generated_sample_ids_fail_after_generation():
    def generate(requests):
        rows = generation_rows(requests)
        return [rows[0].copy() for _ in range(harness.FINAL_CONFIRMATION_N)]

    try:
        harness.run_stream_with_guard(
            stream="direct",
            expected_index=audit_index(),
            current_rows=audit_rows(),
            generation_callable=generate,
            output_root=fresh_dir(),
            run_id="test",
            metadata=metadata(),
        )
    except harness.HarnessError as exc:
        assert "duplicate sample IDs" in str(exc)
    else:
        raise AssertionError("expected HarnessError")


def test_actual_generation_input_mismatch_fails_at_boundary():
    def generate(requests):
        rows = generation_rows(requests)
        rows[0]["input_ids_sha256"] = "actual-input-b"
        return rows

    try:
        harness.run_stream_with_guard(
            stream="direct",
            expected_index=audit_index(),
            current_rows=audit_rows(),
            generation_callable=generate,
            output_root=fresh_dir(),
            run_id="test",
            metadata=metadata(),
        )
    except harness.HarnessError as exc:
        assert "actual generation input mismatch" in str(exc)
    else:
        raise AssertionError("expected HarnessError")


def test_resume_generates_only_unfinished_ids():
    root = fresh_dir()
    raw_path = root / harness.STREAMS["direct"]["raw_generation_path"]
    expected_ids = {row["stage6_sample_id"] for row in audit_rows()}
    partial = [
        harness.normalize_raw_generation_row(
            stream="direct",
            audit_row=row,
            generation_row=generation_rows([row])[0],
            run_id="partial",
            metadata=metadata(),
        )
        for row in audit_rows()[:2]
    ]
    harness.write_rows_incrementally(raw_path, partial, expected_sample_ids=expected_ids)
    generated_ids: list[str] = []

    def generate_remaining(requests):
        generated_ids.extend(row["stage6_sample_id"] for row in requests)
        return generation_rows(requests)

    rows = harness.run_stream_with_guard(
        stream="direct",
        expected_index=audit_index(),
        current_rows=audit_rows(),
        generation_callable=generate_remaining,
        output_root=root,
        run_id="resume",
        metadata=metadata(),
        mode="resume",
    )
    assert len(rows) == harness.FINAL_CONFIRMATION_N
    assert "stage6_crudsql_0000" not in generated_ids
    assert "stage6_crudsql_0001" not in generated_ids
    shutil.rmtree(root, ignore_errors=True)
