from __future__ import annotations

from pathlib import Path

import pytest

from scripts.analysis.run_stage3_causal_replay import load_variant_config
from scripts.analysis.run_stage3b_component_selection import CANDIDATES, validate_stage3b


ROOT = Path(__file__).resolve().parents[1]


def test_candidate_set_is_small_and_pre_specified() -> None:
    assert CANDIDATES == [
        ("FULL", "configs/stage3b/full.json"),
        ("NO_C", "configs/stage3b/no_c.json"),
        ("D_ONLY", "configs/stage3b/d_only.json"),
        ("D_G1", "configs/stage3b/d_g1.json"),
    ]


def test_candidate_component_contracts() -> None:
    configs = {
        name: load_variant_config(ROOT, relative) for name, relative in CANDIDATES
    }
    assert configs["FULL"]["stage2_interventions"] == {
        "control_field_roles": True,
        "explicit_conflict_preservation": True,
        "update_column_consistency": True,
    }
    assert configs["NO_C"]["stage2_interventions"]["update_column_consistency"] is False
    for name in ("D_ONLY", "D_G1"):
        assert not any(configs[name]["stage2_interventions"].values())
        assert configs[name]["structured_source_parser"]["enabled"] is True
        assert configs[name]["free_text_typed_normalization"]["enabled"] is False
        assert configs[name]["constrained_reference_repair"]["enabled"] is False
    assert configs["D_ONLY"]["diagnostic_targeted_repair"]["enabled"] is False
    assert configs["D_G1"]["diagnostic_targeted_repair"]["evidence_span_boundary"] is True
    assert configs["D_G1"]["diagnostic_targeted_repair"]["evidence_span_selection"] is False


def test_validate_stage3b_rejects_prompt_surface_drift() -> None:
    prompt_rows = [{
        "V0_to_V3_all_equal": 0,
        "V4_to_V8_all_equal": 1,
        "all_candidates_equal_V4": 1,
    }] * 300
    sample_rows = [{
        "sample_id": f"s{index}",
        "FULL_correct": 1,
        "FULL_strict_correct": 1,
        "FULL_first_failure": "none",
    } for index in range(300)]
    frozen = {
        f"s{index}": {
            "V8_correct": "1",
            "V8_strict_correct": "1",
            "V8_first_failure": "none",
        }
        for index in range(300)
    }
    with pytest.raises(ValueError, match="V0_V3_prompt_equivalence"):
        validate_stage3b(prompt_rows, {"sample": sample_rows}, frozen)


def test_candidate_configs_do_not_modify_frozen_implementation() -> None:
    allowed = {ROOT / relative for _, relative in CANDIDATES}
    assert all(path.is_file() for path in allowed)
    assert not any((ROOT / "src" / "nldbwrite_v3").rglob("*stage3b*"))
