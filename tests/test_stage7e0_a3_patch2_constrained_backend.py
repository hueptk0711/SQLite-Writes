from __future__ import annotations

import json
import sys
import types
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nldbwrite_v3.v2_a1.inventories import build_schema_inventory
from nldbwrite_v3.v2_a1.phase_m_schema import dynamic_schema
from nldbwrite_v3.v2_a1.phase_o_output import phase_o_json_schema
from nldbwrite_v3.v2_a1.slot_inventory import build_slot_bundle
from nldbwrite_v3.v2_a1.span_validation import validate_and_sort_spans
from scripts.data.build_stage7e0_a3_english_preflight import PACKAGE_NAME, STAGE_NAME, build_stage
from scripts.server.run_stage7e0_a3_english import (
    PHASE_M_MAX_NEW_TOKENS,
    PHASE_O_MAX_NEW_TOKENS,
    load_stage7c_a3_rows,
    run_stage7e0,
    validate_generation_config,
)
from scripts.server.run_stage7e0_v2_a1_preflight import (
    build_phase_m_constraint_grammar,
    build_phase_o_constraint_grammar,
    generate_constrained,
)


def _args(**overrides: object) -> object:
    values: dict[str, object] = {
        "accepted_protocol_commit": "TEST",
        "result_root": str(ROOT / ".codex_tmp" / "unused_stage7e0_patch2"),
        "backend": "constrained_hf",
        "model_name_or_path": "unused",
        "quantization": "none",
        "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
        "phase_m_max_new_tokens": PHASE_M_MAX_NEW_TOKENS,
        "max_input_tokens": 28672,
        "seed": 42,
        "trust_remote_code": False,
        "resume": False,
        "skip_git_assertions": True,
        "allow_result_root_inside_git": True,
    }
    values.update(overrides)
    return type("Args", (), values)()


def test_backend_metadata_is_incremental_json_schema_grammar() -> None:
    rows = load_stage7c_a3_rows(ROOT)
    grammar = build_phase_o_constraint_grammar(phase_o_json_schema(ROOT), rows[0]["model_side_input"]["question"])
    metadata = grammar.metadata()
    assert metadata["finite_complete_object_enumeration"] is False
    assert metadata["finite_known_answer_candidates"] is False
    assert metadata["label_side_data_used_for_constraints"] is False
    assert metadata["capacity"]["backend_supports_more_than_two_spans"] is True


def test_prefix_allowed_tokens_fn_is_passed_to_model_generate(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeInferenceMode:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(inference_mode=lambda: FakeInferenceMode()))

    class FakeTensor:
        def __init__(self, ids: list[int]) -> None:
            self.ids = ids
            self.shape = (1, len(ids))

        def to(self, _device: str) -> "FakeTensor":
            return self

    class FakeGenerated:
        def __init__(self, ids: list[int]) -> None:
            self.ids = ids
            self.shape = (len(ids),)

        def __getitem__(self, key: slice) -> "FakeGenerated":
            return FakeGenerated(self.ids[key])

    class FakeOutput:
        def __init__(self, ids: list[int]) -> None:
            self.ids = ids

        def __getitem__(self, index: int) -> FakeGenerated:
            assert index == 0
            return FakeGenerated(self.ids)

    class FakeParam:
        device = "cpu"

    class FakeModel:
        def __init__(self) -> None:
            self.generate_kwargs: dict[str, object] = {}

        def parameters(self) -> object:
            return iter([FakeParam()])

        def generate(self, **kwargs: object) -> FakeOutput:
            self.generate_kwargs = kwargs
            assert callable(kwargs["prefix_allowed_tokens_fn"])
            assert kwargs["do_sample"] is False
            return FakeOutput([99, 1])

    class FakeTokenizer:
        eos_token_id = 0
        all_special_ids = [0]

        def __len__(self) -> int:
            return 3

        def __call__(self, _text: str, return_tensors: str) -> dict[str, FakeTensor]:
            assert return_tensors == "pt"
            return {"input_ids": FakeTensor([99])}

        def decode(self, ids: object, **_kwargs: object) -> str:
            values = ids.ids if isinstance(ids, FakeGenerated) else ids
            return "{}" if list(values) == [1] else "{"

        def apply_chat_template(self, messages: list[dict[str, str]], tokenize: bool, add_generation_prompt: bool) -> str:
            assert tokenize is False
            assert add_generation_prompt is True
            return json.dumps(messages)

    model = FakeModel()
    result = generate_constrained(
        model,
        FakeTokenizer(),
        [{"role": "user", "content": "Add Alice."}],
        max_new_tokens=PHASE_O_MAX_NEW_TOKENS,
        schema=phase_o_json_schema(ROOT),
        phase="phase_o",
        question="Add Alice.",
        root=ROOT,
    )
    assert result["backend"]["backend"] == "incremental_json_schema_grammar"
    assert model.generate_kwargs["prefix_allowed_tokens_fn"].__name__ == "allowed_tokens"


def test_plain_hf_backend_is_refused() -> None:
    with pytest.raises(SystemExit, match="backend=hf"):
        validate_generation_config(_args(backend="hf"))


def test_four_bit_quantization_is_refused_before_model_load() -> None:
    with pytest.raises(SystemExit, match="forbids 4-bit"):
        run_stage7e0(_args(quantization="4bit"))


def test_phase_m_max_new_tokens_must_be_8192() -> None:
    with pytest.raises(SystemExit, match="Phase M max_new_tokens"):
        validate_generation_config(_args(phase_m_max_new_tokens=1024))


def test_label_side_mutation_does_not_change_phase_o_constraints() -> None:
    rows = load_stage7c_a3_rows(ROOT)
    row = rows[0]
    schema = phase_o_json_schema(ROOT)
    baseline = build_phase_o_constraint_grammar(schema, row["model_side_input"]["question"]).fingerprint
    mutated = json.loads(json.dumps(row))
    mutated["label_side_expected"]["phase_o"]["operation"] = "UPDATE"
    assert build_phase_o_constraint_grammar(schema, mutated["model_side_input"]["question"]).fingerprint == baseline


def test_five_span_phase_o_grammar_is_structurally_valid() -> None:
    rows = load_stage7c_a3_rows(ROOT)
    row = next(item for item in rows if len(item["label_side_expected"]["phase_o"]["value_spans"]) >= 5)
    grammar = build_phase_o_constraint_grammar(phase_o_json_schema(ROOT), row["model_side_input"]["question"])
    text = json.dumps(row["label_side_expected"]["phase_o"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert grammar.status(text) == "complete"


def test_wrong_phase_o_operation_string_is_impossible_under_grammar() -> None:
    rows = load_stage7c_a3_rows(ROOT)
    grammar = build_phase_o_constraint_grammar(phase_o_json_schema(ROOT), rows[0]["model_side_input"]["question"])
    assert grammar.status('{"operation":"INSERT INTO","value_spans":[{"end_char":9,"start_char":4}]}') == "invalid"


def test_phase_m_legacy_fields_are_impossible_under_v2_schema() -> None:
    rows = load_stage7c_a3_rows(ROOT)
    row = rows[0]
    inventory = build_schema_inventory(row["model_side_input"])
    spans = validate_and_sort_spans(row["model_side_input"]["question"], row["label_side_expected"]["phase_o"]["value_spans"])
    slots = build_slot_bundle(spans)
    schema = dynamic_schema("INSERT", inventory, slots, root=ROOT)
    grammar = build_phase_m_constraint_grammar(schema, "INSERT", inventory, slots, root=ROOT)
    assert grammar.status('{"table":"users","columns":[],"values":[]}') == "invalid"


def test_clean_zip_contains_patch2_backend_and_invalid_run_classification(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    package_path = tmp_path / PACKAGE_NAME
    build_stage(stage_dir, package_path)
    with zipfile.ZipFile(package_path) as archive:
        names = set(archive.namelist())
        assert "scripts/server/run_stage7e0_v2_a1_preflight.py" in names
        assert "scripts/server/run_stage7e0_a3_english.py" in names
        assert "tests/test_stage7e0_a3_patch2_constrained_backend.py" in names
        assert f"{STAGE_NAME}/INVALID_RUN_001_CLASSIFICATION.json" in names
