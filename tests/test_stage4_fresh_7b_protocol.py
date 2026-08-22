from __future__ import annotations

from pathlib import Path

from scripts.analysis.run_stage3_causal_replay import sha256_file
from scripts.analysis.run_stage4_fresh_7b_protocol import (
    CONFIGS,
    EXPECTED_SAMPLE_COUNT,
    GENERATION_ARMS,
    PRIMARY_CONFIG_HASH,
    VNEXT_SLUGS,
    operation_label,
    select_fresh_samples,
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
        "original_mp_fs_plus",
        "d_g1_primary",
    }
    assert VNEXT_SLUGS == {
        "d_g1_primary",
        "d_only_secondary",
        "full_secondary",
        "no_c_secondary",
    }


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
