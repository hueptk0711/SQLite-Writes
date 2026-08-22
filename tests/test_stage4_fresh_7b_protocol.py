from __future__ import annotations

from pathlib import Path

from scripts.analysis.run_stage3_causal_replay import sha256_file
from scripts.analysis.run_stage4_fresh_7b_protocol import (
    BOOTSTRAP_REPLICATES,
    BOOTSTRAP_SEED,
    CONFIGS,
    EXPECTED_SAMPLE_COUNT,
    GENERATION_ARMS,
    INFERENCE_LOCK,
    MP_FS_PLUS_SHARED_SLUGS,
    PRIMARY_CONFIG_HASH,
    VNEXT_SLUGS,
    operation_label,
    select_fresh_samples,
    source_group_key,
)
from scripts.server.run_stage4_fresh_7b import (
    DETERMINISTIC_REPROCESS_PLAN,
    GENERATION_PLAN,
    build_runner_plan,
)


ROOT = Path(__file__).resolve().parents[1]


def test_stage4_config_contract_is_predeclared() -> None:
    assert EXPECTED_SAMPLE_COUNT == 300
    assert [row[0] for row in CONFIGS] == [
        "direct",
        "j_fs",
        "original_mp_fs_plus",
        "d_g1_primary",
        "d_only_secondary",
        "full_secondary",
        "no_c_secondary",
    ]
    assert GENERATION_ARMS == {
        "direct",
        "j_fs",
        "mp_fs_plus_shared",
    }
    assert MP_FS_PLUS_SHARED_SLUGS == {
        "original_mp_fs_plus",
        "d_g1_primary",
        "d_only_secondary",
        "full_secondary",
        "no_c_secondary",
    }
    assert VNEXT_SLUGS == {
        "d_g1_primary",
        "d_only_secondary",
        "full_secondary",
        "no_c_secondary",
    }
    assert BOOTSTRAP_REPLICATES == 10_000
    assert BOOTSTRAP_SEED == 240822


def test_primary_config_is_stage3b_d_g1_exact_copy() -> None:
    stage3b = ROOT / "configs" / "stage3b" / "d_g1.json"
    stage4 = ROOT / "configs" / "stage4" / "d_g1_primary.json"
    assert sha256_file(stage4) == PRIMARY_CONFIG_HASH
    assert stage4.read_text(encoding="utf-8") == stage3b.read_text(encoding="utf-8")


def test_fresh_selector_excludes_content_overlap() -> None:
    diagnostic = [{
        "id": "diag_1",
        "source_group": "source_a",
        "db_id": "old_db",
        "input_text": "same request",
        "gold_records": [{"x": 1}],
    }]
    candidates = [
        {
            "id": "fresh_bad_text",
            "source_group": "fresh_bad_text",
            "db_id": "new_db",
            "input_text": "same request",
            "gold_records": [{"x": 2}],
        },
        {
            "id": "fresh_good",
            "source_group": "fresh_good",
            "db_id": "new_db",
            "input_text": "new request",
            "gold_records": [{"x": 3}],
        },
    ]
    selected = select_fresh_samples(candidates, diagnostic, sample_count=1)
    assert [row["id"] for row in selected] == ["fresh_good"]


def test_operation_label_normalizes_legacy_upsert_name() -> None:
    assert operation_label(None, {"operation_type": "upsert"}) == "upsert_update"


def test_source_group_prefers_official_metadata() -> None:
    assert source_group_key({"id": "aug_seed_000002_x", "source_group_id": "official_2"}) == (
        "official_2",
        "source_group_id",
    )
    assert source_group_key({"id": "aug_seed_000002_x"}) == (
        "seed_000002",
        "derived_from_sample_id_no_official_source_group",
    )


def test_inference_lock_has_full_4bit_generation_contract() -> None:
    bnb = INFERENCE_LOCK["bitsandbytes_config"]
    assert bnb["load_in_4bit"] is True
    assert bnb["bnb_4bit_quant_type"] == "fp4"
    assert bnb["bnb_4bit_use_double_quant"] is False
    assert bnb["bnb_4bit_compute_dtype"] == "float16"
    assert bnb["bnb_4bit_quant_storage"] == "uint8"
    assert INFERENCE_LOCK["chat_template_usage"]["add_generation_prompt"] is True
    assert INFERENCE_LOCK["padding_side"] == "left"
    assert INFERENCE_LOCK["generation_kwargs"]["temperature"] == "omitted_when_do_sample_false"


def test_stage4_runner_graph_is_three_generation_arms(tmp_path: Path) -> None:
    plan = build_runner_plan(tmp_path)
    assert {row["generation_arm"] for row in GENERATION_PLAN} == GENERATION_ARMS
    assert len(GENERATION_PLAN) == 3
    assert {row["process_slug"] for row in DETERMINISTIC_REPROCESS_PLAN} == {
        "d_g1_primary",
        "d_only_secondary",
        "full_secondary",
        "no_c_secondary",
    }
    assert plan["raw_generation_policy"]["semantic_retry"] is False
