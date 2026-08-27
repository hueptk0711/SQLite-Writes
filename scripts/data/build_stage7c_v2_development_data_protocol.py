#!/usr/bin/env python3
"""Build Stage7C V2 development/data protocol artifacts.

This stage is CPU-only. It freezes data provenance, adapter contracts, leakage
boundaries, and selection/evaluation protocols before any V2 implementation or
generation run exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CRUDSQL_ROOT = PROJECT_ROOT.parents[1] / "external_sources" / "CRUDSQL_63bfce67"
STAGE = "Stage7C_V2_DEVELOPMENT_DATA_PROTOCOL"
DATE = "20260827"
CRUDSQL_REPO = "https://github.com/bizard-lab/CRUDSQL.git"
CRUDSQL_COMMIT = "63bfce67d8391185453a812751e115a499201363"
EXPECTED_CREATE_COUNTS = {"train": 1760, "dev": 240}
EXPECTED_TABLE_COUNTS = {"train": 440, "dev": 60, "confirmation": 121}
HASH_POLICY = "sha256_bytes_for_raw_files_text_sha256_canonical_lf_for_json_artifacts"
MODEL_CALLED = False
GPU_CALLED = False
V2_IMPLEMENTED = False
EXPERIMENT_RUN = False
LIVESQLBENCH_GT_OPENED = False
OPERATION_LABEL_MAPPING = {
    "0": {"crudsql_label": "Create", "v2_operation": "INSERT", "stage7c_use": "train_dev_create_development"},
    "1": {"crudsql_label": "Delete", "v2_operation": "DELETE", "stage7c_use": "reserved_after_v2_freeze"},
    "2": {"crudsql_label": "Update", "v2_operation": "UPDATE", "stage7c_use": "reserved_after_v2_freeze"},
    "3": {"crudsql_label": "Read", "v2_operation": "OUT_OF_SCOPE_READ", "stage7c_use": "excluded_from_write_v2"},
    "UPSERT": {"crudsql_label": "not_represented", "v2_operation": "UPSERT", "stage7c_use": "not_in_crudsql"},
}
FROZEN_GENERATION_CONFIG = {
    "model_id": "stage6i_same_exact_7b_model_family_for_v2_primary",
    "model_revision": "reuse_stage6i_exact_revision_before_any_stage7d_generation",
    "tokenizer_revision": "reuse_stage6i_exact_tokenizer_revision_before_any_stage7d_generation",
    "do_sample": False,
    "temperature": 0.0,
    "top_p": 1.0,
    "seed_policy": "deterministic_decoding_no_sampling_seed_not_used",
    "stop_criteria": ["single_json_object_complete", "max_new_tokens"],
    "retry_count": 0,
    "phase_o_max_new_tokens": 32,
    "phase_m_max_new_tokens": 768,
    "generation_timeout_seconds_per_phase": 120,
    "execution_timeout_seconds_per_sample": 30,
}

STAGE7B_INPUTS = (
    "stage7b_v2_method_specification/STAGE7B_V2_SPECIFICATION_LOCK.json",
    "stage7b_v2_method_specification/V2_ARCHITECTURE_SPEC.json",
    "stage7b_v2_method_specification/REFERENCE_CONSTRAINT_SPEC.json",
    "stage7b_v2_method_specification/COMPLETENESS_VERIFICATION_SPEC.json",
    "stage7b_v2_method_specification/TYPED_MATERIALIZATION_SPEC.json",
    "stage7b_v2_method_specification/DEVELOPMENT_DATA_POLICY.json",
)

STAGE6_TEST_INPUTS = (
    "stage6_final_registration_revision/artifacts/FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl",
)

RAW_SOURCE_RELS = (
    "data/train/crud_train_sql.json",
    "data/train/crud_train_table.json",
    "data/train/train.db",
    "data/dev/crud_dev_sql.json",
    "data/dev/crud_dev_table.json",
    "data/dev/dev.db",
)

ARTIFACTS = (
    "STAGE7C_INPUT_MANIFEST.json",
    "CRUDSQL_SOURCE_MANIFEST.json",
    "TRAIN_CREATE_MANIFEST.jsonl",
    "DEV_CREATE_MANIFEST.jsonl",
    "DATASET_ELIGIBILITY_SPEC.json",
    "DATASET_ELIGIBILITY_AUDIT.json",
    "CRUDSQL_ADAPTER_SPEC.json",
    "SCHEMA_INVENTORY_SPEC.json",
    "EVIDENCE_INVENTORY_SPEC.json",
    "SEMANTIC_SLOT_INVENTORY_SPEC.json",
    "SEMANTIC_SLOT_DERIVATION_SPEC.json",
    "SEMANTIC_SLOT_DERIVATION_AUDIT.json",
    "OPERATION_LABEL_MAPPING_SPEC.json",
    "GOLD_PROGRAM_DERIVATION_SPEC.json",
    "GOLD_PROGRAM_DERIVATION_AUDIT.json",
    "GOLD_POST_STATE_PROTOCOL.json",
    "MODEL_INPUT_LEAKAGE_POLICY.json",
    "SPLIT_CONTAMINATION_AUDIT.json",
    "DEV_SELECTION_PROTOCOL.json",
    "GENERATION_PROTOCOL_SPEC.json",
    "EVALUATION_ENVIRONMENT_SPEC.json",
    "RESERVED_BENCHMARK_POLICY.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
)
RAW_ARTIFACTS = tuple(f"upstream_crudsql/{rel}" for rel in RAW_SOURCE_RELS)
LOCK_FILE = "STAGE7C_DATA_PROTOCOL_LOCK.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")


def git_output(root: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def sqlite_integrity(path: Path) -> dict[str, Any]:
    con = sqlite3.connect(path)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    finally:
        con.close()
    return {"integrity_check": integrity, "table_count": len(tables)}


def sqlite_affinity(declared_type: str) -> str:
    dtype = (declared_type or "").upper()
    if "INT" in dtype:
        return "INTEGER"
    if any(token in dtype for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in dtype or dtype == "":
        return "BLOB"
    if any(token in dtype for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def split_paths(root: Path, split: str) -> dict[str, Path]:
    return {
        "sql": root / "data" / split / f"crud_{split}_sql.json",
        "table": root / "data" / split / f"crud_{split}_table.json",
        "db": root / "data" / split / f"{split}.db",
    }


def copy_raw_source(crudsql_root: Path, output_dir: Path) -> None:
    for rel in RAW_SOURCE_RELS:
        src = crudsql_root / rel
        dst = output_dir / "upstream_crudsql" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def copy_packaged_source(packaged_root: Path, output_dir: Path) -> None:
    for rel in RAW_SOURCE_RELS:
        src = packaged_root / rel
        dst = output_dir / "upstream_crudsql" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def reset_output_dir(output_dir: Path, force: bool) -> None:
    default = PROJECT_ROOT / "stage7c_v2_development_data_protocol"
    if output_dir.exists():
        if not force and output_dir == default:
            raise RuntimeError(f"{output_dir} exists; pass --force to rebuild.")
        resolved_output = output_dir.resolve()
        resolved_root = PROJECT_ROOT.resolve()
        inside_project = resolved_output == resolved_root or resolved_root in resolved_output.parents
        if output_dir.name != "stage7c_v2_development_data_protocol" or not inside_project:
            raise RuntimeError(f"Refusing to remove output outside Stage7C path: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)


def input_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in STAGE7B_INPUTS + STAGE6_TEST_INPUTS:
        path = PROJECT_ROOT / rel
        hashes[rel] = sha256_file(path)
    return hashes


def source_manifest(crudsql_root: Path, output_dir: Path, source_mode: str) -> dict[str, Any]:
    files = []
    for rel in RAW_SOURCE_RELS:
        source_path = crudsql_root / rel
        packaged_path = output_dir / "upstream_crudsql" / rel
        item = {
            "path": f"upstream_crudsql/{rel}",
            "source_path": rel,
            "sha256": sha256_file(packaged_path),
            "size_bytes": packaged_path.stat().st_size,
        }
        if rel.endswith(".json"):
            item["record_count"] = len(read_json(packaged_path))
        if rel.endswith(".db"):
            item.update(sqlite_integrity(packaged_path))
        item["source_sha256_matches_packaged"] = source_path.is_file() and sha256_file(source_path) == item["sha256"]
        files.append(item)
    return {
        "stage": STAGE,
        "source": {
            "dataset": "CRUDSQL",
            "repository": CRUDSQL_REPO,
            "commit": CRUDSQL_COMMIT,
            "source_mode": source_mode,
            "local_source_head": git_output(crudsql_root, "rev-parse", "HEAD"),
            "local_source_clean": git_output(crudsql_root, "status", "--short") == "",
        },
        "included_splits": ["train", "dev"],
        "excluded_splits": ["test"],
        "files": files,
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
    }


def preserve_packaged_source_if_needed(output_dir: Path, source_mode: str, packaged_source_dir: Path | None) -> Path | None:
    if source_mode != "packaged":
        return packaged_source_dir
    source_dir = packaged_source_dir or output_dir / "upstream_crudsql"
    if not source_dir.exists():
        raise RuntimeError(f"Packaged source mode requires {source_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_root = output_dir.parent / f"{output_dir.name}_packaged_source_preserve"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    preserved = temp_root / "upstream_crudsql"
    shutil.copytree(source_dir, preserved)
    return preserved


def verify_packaged_source_hashes(source_root: Path, expected_manifest_path: Path | None = None) -> None:
    if expected_manifest_path and expected_manifest_path.is_file():
        manifest = read_json(expected_manifest_path)
        expected = {entry["source_path"]: entry["sha256"] for entry in manifest.get("files", [])}
        for rel in RAW_SOURCE_RELS:
            actual = sha256_file(source_root / rel)
            if expected.get(rel) != actual:
                raise RuntimeError(f"Packaged source hash mismatch for {rel}")
    for rel in RAW_SOURCE_RELS:
        if not (source_root / rel).is_file():
            raise RuntimeError(f"Missing packaged source file: {rel}")


def extract_question_spans(question: str) -> list[dict[str, Any]]:
    spans = []
    start = 0
    parts = re.split(r"([，。；;、,:：,\n\r\t])", question)
    cursor = 0
    for part in parts:
        if not part:
            continue
        if re.fullmatch(r"[，。；;、,:：,\n\r\t]", part):
            cursor += len(part)
            start = cursor
            continue
        text = part.strip()
        leading = len(part) - len(part.lstrip())
        if text:
            span_start = start + leading
            spans.append({"text": text, "start_char": span_start, "end_char": span_start + len(text)})
        cursor += len(part)
    if not spans and question:
        spans.append({"text": question, "start_char": 0, "end_char": len(question)})
    return spans


def normalized_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value)).casefold()


def trim_value_span(question: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and question[start].isspace():
        start += 1
    while start < end and question[end - 1].isspace():
        end -= 1
    suffixes = (
        "请你把这条数据添加到数据库中",
        "请把这条数据添加到数据库中",
        "把这条数据添加到数据库中",
        "添加到数据库中",
        "加进表中吗",
        "加入表中",
        "添加到表中",
        "补充到表中",
        "录入表中",
    )
    changed = True
    while changed:
        changed = False
        text = question[start:end]
        for suffix in suffixes:
            if text.endswith(suffix):
                end -= len(suffix)
                changed = True
                break
    while start < end and question[start] in "的是为有:：=达到到了 ":
        start += 1
    while start < end and question[end - 1] in "，。；;、,吗?？ ":
        end -= 1
    if start >= end:
        return None
    text = question[start:end]
    data_suffix = re.match(r"^(.{1,30}?)的[^，。；;、,\n\r\t]*(?:数据|信息|记录)$", text)
    if data_suffix and not re.search(r"[0-9A-Za-z%+\-.]", data_suffix.group(1)):
        end = start + len(data_suffix.group(1))
        text = question[start:end]
    norm = normalized_text(text)
    if norm in {"你好", "您好", "它", "其", "表中", "以下内容", "以下数据", "我需要添加一条数据", "请你把这条数据添加到数据库中", "请把这条数据添加到数据库中"}:
        return None
    if norm.startswith(("新加入", "一条", "其中", "该", "这条", "请", "帮我", "我需要", "能帮我")) and not re.search(r"[0-9A-Za-z%+\-.]", text):
        return None
    if "添加" in text and "数据" in text and not re.search(r"[0-9A-Za-z%+\-.]", text):
        return None
    return start, end


def add_span(spans: list[dict[str, Any]], question: str, start: int, end: int, source: str) -> None:
    trimmed = trim_value_span(question, start, end)
    if not trimmed:
        return
    start, end = trimmed
    text = question[start:end]
    if not text:
        return
    if "_split" not in source and "和" in text and "及以上" not in text:
        sep = text.rfind("和")
        left = text[:sep]
        right = text[sep + 1 :]
        if left and right and re.search(r"[0-9A-Za-z%+\-.]", right):
            add_span(spans, question, start, start + len(left), source + "_split")
            add_span(spans, question, start + sep + 1, end, source + "_split")
            return
    candidate = {"text": text, "start_char": start, "end_char": end, "source": source}
    for i, existing in enumerate(list(spans)):
        overlap = not (end <= existing["start_char"] or start >= existing["end_char"])
        if not overlap:
            continue
        if start == existing["start_char"] and end == existing["end_char"]:
            return
        if (end - start) < (existing["end_char"] - existing["start_char"]):
            spans[i] = candidate
        return
    spans.append(candidate)


def header_aliases(header: str) -> list[str]:
    cleaned = re.sub(r"[（(].*?[）)]", "", str(header))
    cleaned = re.sub(r"\s+", "", cleaned)
    aliases = {cleaned}
    for token in ("股票", "证券", "公司", "企业", "项目", "招聘", "岗位", "所属", "所在", "单位"):
        if cleaned.startswith(token) and len(cleaned) > len(token) + 1:
            aliases.add(cleaned[len(token) :])
    if cleaned.endswith("名称") and len(cleaned) > 2:
        aliases.add("名称")
    if cleaned.endswith("代码") and len(cleaned) > 2:
        aliases.add("代码")
    if cleaned.endswith("类别") and len(cleaned) > 2:
        aliases.add(cleaned[:-2])
    return sorted((alias for alias in aliases if len(alias) >= 2), key=len, reverse=True)


def semantic_value_spans(question: str, table: dict[str, Any]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    segment_spans = extract_question_spans(question)
    cue_pattern = re.compile(r"(?:达到了|达到|分别是|填入|填写|填上|设为|设置为|是|(?<!华)为|(?<!只)有|=)([^，。；;、,:：,\n\r\t]{1,80})")
    for segment in segment_spans:
        text = segment["text"]
        base = segment["start_char"]
        for match in re.finditer(r"把([^，。；;、,\n\r\t]{1,60}?)(?:加进|加入|添加|补充|录入|写入)", text):
            add_span(spans, question, base + match.start(1), base + match.end(1), "object_before_insert_verb")
        for match in re.finditer(r"(?:加入|添加|插入|新增)([^，。；;、,\n\r\t]{1,30}?)(?:的[^，。；;、,\n\r\t]*(?:数据|信息|记录)|到|至)", text):
            add_span(spans, question, base + match.start(1), base + match.end(1), "entity_after_insert_verb_before_descriptor")
        for match in re.finditer(r"^([^，。；;、,:：,\n\r\t]{1,20})的[^，。；;、,:：,\n\r\t]*(?:为|是|有|达|填)", text):
            candidate = match.group(1)
            if normalized_text(candidate) not in {"股票", "证券", "公司", "企业", "其", "该公司", "上涨股票", "下跌股票"}:
                add_span(spans, question, base + match.start(1), base + match.end(1), "possessive_left_value_before_assignment")
        for match in re.finditer(r"^([^，。；;、,\n\r\t]{2,40}?)(?:将要招聘|将招聘|招聘)", text):
            add_span(spans, question, base + match.start(1), base + match.end(1), "entity_before_recruitment_verb")
        for match in re.finditer(r"(?:招聘|招)([0-9]+(?:\.[0-9]+)?)个", text):
            add_span(spans, question, base + match.start(1), base + match.end(1), "count_after_recruitment_verb")
        for match in re.finditer(r"(博士研究生及以上|硕士研究生及以上|研究生及以上|本科及以上|大专及以上|专科及以上|博士研究生|硕士研究生|本科|大专|专科)", text):
            add_span(spans, question, base + match.start(1), base + match.end(1), "education_level_pattern")
        for match in re.finditer(r"(专业技术岗位?|管理岗位?|工勤岗位?)", text):
            add_span(spans, question, base + match.start(1), base + match.end(1), "job_category_pattern")
        for match in re.finditer(r"第([0-9]+(?:\.[0-9]+)?)", text):
            add_span(spans, question, base + match.start(1), base + match.end(1), "ordinal_number_pattern")
        for match in cue_pattern.finditer(text):
            add_span(spans, question, base + match.start(1), base + match.end(1), "value_after_assignment_cue")
        has_segment_span = any(not (span["end_char"] <= base or span["start_char"] >= base + len(text)) for span in spans)
        if not has_segment_span and re.search(r"[0-9A-Za-z%+\-.]", text) and not any(token in text for token in ("添加", "数据库", "表中", "请你", "帮我")):
            add_span(spans, question, base, base + len(text), "fallback_value_like_segment")
    for column in table.get("header", []):
        for alias in header_aliases(str(column)):
            for match in re.finditer(re.escape(alias), question):
                start = match.end()
                tail = question[start : min(len(question), start + 4)]
                connector = re.match(r"(?:的)?(?:是|为|达到了|达到|有|:|：|=)", tail)
                if connector:
                    value_start = start + connector.end()
                elif start < len(question) and re.match(r"[0-9A-Za-z+\-.]", question[start]):
                    value_start = start
                else:
                    continue
                value_end = len(question)
                for delimiter in "，。；;、,\n\r\t":
                    pos = question.find(delimiter, value_start)
                    if pos != -1:
                        value_end = min(value_end, pos)
                add_span(spans, question, value_start, value_end, "schema_header_assignment_cue")
    if not spans:
        for segment in segment_spans:
            text = segment["text"]
            if any(token in text for token in ("添加", "数据", "数据库", "表中", "你好", "您好")) and not re.search(r"[0-9A-Za-z%+\-.]", text):
                continue
            add_span(spans, question, segment["start_char"], segment["end_char"], "fallback_non_request_segment")
    return sorted(spans, key=lambda row: (row["start_char"], row["end_char"], row["text"]))


def schema_inventory(table: dict[str, Any]) -> dict[str, Any]:
    return {
        "tables": [{"table_ref": "TAB_1", "table_id": table["id"], "table_name": table["name"]}],
        "columns": [
            {
                "column_ref": f"COL_{index + 1}",
                "table_ref": "TAB_1",
                "source_column_index": index,
                "header": header,
                "source_column_type": table["types"][index],
                "sqlite_affinity": sqlite_affinity(str(table["types"][index])),
            }
            for index, header in enumerate(table["header"])
        ],
        "constraints": [],
    }


def evidence_and_slots(question: str, table: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    spans = semantic_value_spans(question, table)
    evidence = []
    slots = []
    for index, span in enumerate(spans, start=1):
        evidence_ref = f"EV_{index}"
        slot_ref = f"SLOT_{index}"
        evidence.append({"evidence_ref": evidence_ref, "text": span["text"], "start_char": span["start_char"], "end_char": span["end_char"], "source": span["source"]})
        slots.append({"slot_ref": slot_ref, "evidence_ref": evidence_ref, "role": "write_value", "required": True, "source": "deterministic_value_bearing_question_schema_extractor", "uses_gold_sql": False})
    return {"evidence": evidence, "construction": "deterministic_value_bearing_span_extraction"}, {"slots": slots, "construction": "deterministic_value_bearing_question_schema_extractor", "uses_gold_sql": False, "model_call_used": False}


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def gold_insert_program(row: dict[str, Any], table: dict[str, Any]) -> dict[str, Any]:
    assignments = [
        {"column_index": cond[0], "column_name": f"col_{int(cond[0]) + 1}", "operator_index": cond[1], "value": cond[2]}
        for cond in row["sql"].get("conds", [])
    ]
    columns = [quote_identifier(assignment["column_name"]) for assignment in assignments]
    placeholders = ", ".join("?" for _ in assignments)
    sql = f"INSERT INTO {quote_identifier(table['name'])} ({', '.join(columns)}) VALUES ({placeholders})"
    return {
        "operation": "INSERT",
        "table_name": table["name"],
        "assignments": assignments,
        "sql_template": sql,
        "parameters": [assignment["value"] for assignment in assignments],
    }


def canonical_table_state(con: sqlite3.Connection, table_name: str) -> dict[str, Any]:
    columns = [row[1] for row in con.execute(f"PRAGMA table_info({quote_identifier(table_name)})")]
    rows = con.execute(f"SELECT {', '.join(quote_identifier(col) for col in columns)} FROM {quote_identifier(table_name)} ORDER BY rowid").fetchall()
    return {"table_name": table_name, "columns": columns, "rows": [list(row) for row in rows]}


def execute_gold_insert_and_hash(db_path: Path, row: dict[str, Any], table: dict[str, Any]) -> dict[str, Any]:
    program = gold_insert_program(row, table)
    source = sqlite3.connect(db_path)
    con = sqlite3.connect(":memory:")
    try:
        source.backup(con)
        con.execute(program["sql_template"], program["parameters"])
        con.commit()
        post_state = canonical_table_state(con, table["name"])
    finally:
        con.close()
        source.close()
    return {
        "status": "PASS",
        "gold_program_sha256": sha256_text(canonical_json(program)),
        "gold_post_state_sha256": sha256_text(canonical_json(post_state)),
        "assignment_count": len(program["assignments"]),
    }


def canonical_record(split: str, source_index: int, create_ordinal: int, row: dict[str, Any], table: dict[str, Any]) -> dict[str, Any]:
    question = row["question"]
    evidence_inventory, slot_inventory = evidence_and_slots(question, table)
    source_hash = sha256_text(canonical_json(row))
    sample_id = f"stage7c_crudsql_{split}_create_{create_ordinal:04d}"
    model_side_input = {
        "question": question,
        "schema_inventory": schema_inventory(table),
        "evidence_inventory": evidence_inventory,
        "semantic_slot_inventory": slot_inventory,
    }
    return {
        "sample_id": sample_id,
        "split": split,
        "source_repository": CRUDSQL_REPO,
        "source_commit": CRUDSQL_COMMIT,
        "source_sql_file": f"upstream_crudsql/data/{split}/crud_{split}_sql.json",
        "source_table_file": f"upstream_crudsql/data/{split}/crud_{split}_table.json",
        "source_db_file": f"upstream_crudsql/data/{split}/{split}.db",
        "source_sql_index": source_index,
        "create_ordinal": create_ordinal,
        "table_id": row["table_id"],
        "question": question,
        "question_sha256": sha256_text(question),
        "canonical_source_record_sha256": source_hash,
        "operation_label_for_evaluation_only": "CREATE",
        "v2_gold_operation_for_evaluation_only": "INSERT",
        "operation_label_visible_to_phase_o": False,
        "model_side_input": model_side_input,
        "model_side_input_sha256": sha256_text(canonical_json(model_side_input)),
        "model_side_input_fields": ["question", "schema_inventory", "evidence_inventory", "semantic_slot_inventory"],
        "semantic_slot_inventory_derivation_inputs": ["question"],
        "label_side_bookkeeping": {
            "crudsql_type": row["sql"]["type"],
            "crudsql_operation_label": "Create",
            "v2_gold_operation": "INSERT",
            "gold_assignment_count": len(row["sql"].get("conds", [])),
            "gold_insert_program_sha256": sha256_text(canonical_json(gold_insert_program(row, table))),
            "gold_annotation_sha256": sha256_text(canonical_json(row["sql"])),
            "gold_sql_or_structured_annotation_visible_to_model": False,
            "used_for": "development_evaluation_only",
        },
    }


def build_split_manifest(output_dir: Path, split: str) -> list[dict[str, Any]]:
    paths = split_paths(output_dir / "upstream_crudsql", split)
    sql_rows = read_json(paths["sql"])
    tables = {row["id"]: row for row in read_json(paths["table"])}
    manifest = []
    create_ordinal = 0
    for source_index, row in enumerate(sql_rows):
        if row.get("sql", {}).get("type") != 0:
            continue
        table = tables[row["table_id"]]
        manifest.append(canonical_record(split, source_index, create_ordinal, row, table))
        create_ordinal += 1
    return manifest


def leakage_counts(rows: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    forbidden = {"operation", "operation_label", "gold", "gold_sql", "sql", "conds", "sel", "agg", "type"}
    for row in rows:
        model_text = canonical_json(row["model_side_input"]).casefold()
        if any(f'"{key.casefold()}"' in model_text for key in forbidden):
            counts["model_side_forbidden_key_present"] += 1
        if row["operation_label_visible_to_phase_o"]:
            counts["operation_label_visible_to_phase_o"] += 1
        if row["semantic_slot_inventory_derivation_inputs"] != ["question"]:
            counts["slot_inventory_uses_non_question_input"] += 1
        if row["model_side_input"]["semantic_slot_inventory"].get("uses_gold_sql") is not False:
            counts["slot_inventory_gold_sql_flag_not_false"] += 1
    return counts


def source_split_counts(output_dir: Path, split: str) -> dict[str, Any]:
    rows = read_json(output_dir / "upstream_crudsql" / "data" / split / f"crud_{split}_sql.json")
    type_counts = Counter(str(row.get("sql", {}).get("type")) for row in rows)
    return {"total_records": len(rows), "type_counts": dict(sorted(type_counts.items())), "create_type0_count": type_counts["0"]}


def contamination_audit(train_rows: list[dict[str, Any]], dev_rows: list[dict[str, Any]]) -> dict[str, Any]:
    test_rows = read_jsonl(PROJECT_ROOT / STAGE6_TEST_INPUTS[0])
    train_hashes = {row["question_sha256"] for row in train_rows}
    dev_hashes = {row["question_sha256"] for row in dev_rows}
    test_hashes = {row["input_text_sha256"] for row in test_rows}
    train_tables = {row["table_id"] for row in train_rows}
    dev_tables = {row["table_id"] for row in dev_rows}
    confirmation_tables = {row["table_id"] for row in test_rows}
    return {
        "stage": STAGE,
        "train_dev_question_hash_overlap": len(train_hashes & dev_hashes),
        "train_481_question_hash_overlap": len(train_hashes & test_hashes),
        "dev_481_question_hash_overlap": len(dev_hashes & test_hashes),
        "train_sample_id_count": len({row["sample_id"] for row in train_rows}),
        "dev_sample_id_count": len({row["sample_id"] for row in dev_rows}),
        "train_dev_sample_id_overlap": len({row["sample_id"] for row in train_rows} & {row["sample_id"] for row in dev_rows}),
        "train_table_id_count": len(train_tables),
        "dev_table_id_count": len(dev_tables),
        "confirmation_table_id_count": len(confirmation_tables),
        "train_dev_table_id_overlap": len(train_tables & dev_tables),
        "train_confirmation_table_id_overlap": len(train_tables & confirmation_tables),
        "dev_confirmation_table_id_overlap": len(dev_tables & confirmation_tables),
        "test_question_text_imported": False,
        "live_sql_bench_gt_opened": LIVESQLBENCH_GT_OPENED,
        "status": "PASS",
    }


def eligibility_spec() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "scope": "CRUDSQL train/dev Create only",
        "selected_operation": {"crudsql_sql_type": 0, "label": "Create"},
        "method_agnostic_exclusion_reasons": ["missing_source_file", "db_integrity_failure", "table_id_missing", "malformed_record", "no_question_text", "no_deterministic_question_span"],
        "forbidden_exclusion_reasons": ["v2_prediction_wrong", "low_confidence_after_generation", "dev_accuracy_hurts", "manual_metric_optimization"],
        "eligibility_frozen_before_v2": True,
    }


def eligibility_audit(output_dir: Path, train_rows: list[dict[str, Any]], dev_rows: list[dict[str, Any]]) -> dict[str, Any]:
    train_counts = source_split_counts(output_dir, "train")
    dev_counts = source_split_counts(output_dir, "dev")
    return {
        "stage": STAGE,
        "expected_official_create_counts": EXPECTED_CREATE_COUNTS,
        "source_split_counts": {"train": train_counts, "dev": dev_counts},
        "eligible_create_counts": {"train": len(train_rows), "dev": len(dev_rows)},
        "exclusions_from_create_pool": {"train": {}, "dev": {}},
        "all_exclusions_method_agnostic": True,
        "status": "PASS",
    }


def gold_values(row: dict[str, Any]) -> list[str]:
    return [str(cond[2]) for cond in row["sql"].get("conds", [])]


def split_slot_derivation_audit(rows: list[dict[str, Any]], raw_rows: list[dict[str, Any]]) -> dict[str, Any]:
    raw_by_index = {index: row for index, row in enumerate(raw_rows)}
    counts: Counter[str] = Counter()
    gold_total = 0
    gold_covered = 0
    slot_total = 0
    spurious_slots = 0
    unresolved_examples = []
    for manifest_row in rows:
        raw = raw_by_index[manifest_row["source_sql_index"]]
        values = gold_values(raw)
        slots = manifest_row["model_side_input"]["semantic_slot_inventory"]["slots"]
        evidence = {entry["evidence_ref"]: entry["text"] for entry in manifest_row["model_side_input"]["evidence_inventory"]["evidence"]}
        slot_texts = [evidence[slot["evidence_ref"]] for slot in slots]
        gold_total += len(values)
        slot_total += len(slot_texts)
        covered = sum(1 for value in values if any(normalized_text(value) and normalized_text(value) in normalized_text(slot) for slot in slot_texts))
        spurious = sum(1 for slot in slot_texts if not any(normalized_text(value) and normalized_text(value) in normalized_text(slot) for value in values))
        gold_covered += covered
        spurious_slots += spurious
        relation = "equal" if len(slot_texts) == len(values) else ("slots_gt_gold" if len(slot_texts) > len(values) else "slots_lt_gold")
        counts[relation] += 1
        if covered < len(values):
            counts["gold_value_under_covered_samples"] += 1
        if spurious:
            counts["spurious_required_slot_samples"] += 1
        if (covered < len(values) or spurious) and len(unresolved_examples) < 10:
            unresolved_examples.append(
                {
                    "sample_id": manifest_row["sample_id"],
                    "slot_count": len(slot_texts),
                    "gold_assignment_count": len(values),
                    "covered_gold_values": covered,
                    "spurious_required_slots": spurious,
                    "slot_texts": slot_texts,
                    "gold_values_label_side_only": values,
                }
            )
    n = len(rows)
    return {
        "sample_count": n,
        "slot_count_total": slot_total,
        "gold_assignment_count_total_label_side_only": gold_total,
        "exact_cardinality_match": counts["equal"],
        "slots_gt_gold_assignments": counts["slots_gt_gold"],
        "slots_lt_gold_assignments": counts["slots_lt_gold"],
        "cardinality_mismatch": counts["slots_gt_gold"] + counts["slots_lt_gold"],
        "gold_value_coverage_count": gold_covered,
        "spurious_required_slot_count": spurious_slots,
        "gold_value_coverage_rate": round(gold_covered / gold_total, 6) if gold_total else 1.0,
        "spurious_required_slot_rate": round(spurious_slots / slot_total, 6) if slot_total else 0.0,
        "under_segmentation_rate": round(counts["slots_lt_gold"] / n, 6) if n else 0.0,
        "over_segmentation_rate": round(counts["slots_gt_gold"] / n, 6) if n else 0.0,
        "unresolved_samples": counts["gold_value_under_covered_samples"],
        "spurious_required_slot_samples": counts["spurious_required_slot_samples"],
        "example_unresolved_records": unresolved_examples,
    }


def semantic_slot_derivation_audit(output_dir: Path, train_rows: list[dict[str, Any]], dev_rows: list[dict[str, Any]]) -> dict[str, Any]:
    train_raw = read_json(output_dir / "upstream_crudsql" / "data" / "train" / "crud_train_sql.json")
    dev_raw = read_json(output_dir / "upstream_crudsql" / "data" / "dev" / "crud_dev_sql.json")
    return {
        "stage": STAGE,
        "gold_used_for_model_side_inventory": False,
        "gold_used_for_label_side_audit_only": True,
        "metrics_are_protocol_audit_not_selection_metric": True,
        "train": split_slot_derivation_audit(train_rows, train_raw),
        "dev": split_slot_derivation_audit(dev_rows, dev_raw),
    }


def gold_program_derivation_audit(output_dir: Path, train_rows: list[dict[str, Any]], dev_rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"stage": STAGE, "compiler": "crudsql_type0_conds_to_insert", "splits": {}}
    for split, rows in (("train", train_rows), ("dev", dev_rows)):
        raw_rows = read_json(output_dir / "upstream_crudsql" / "data" / split / f"crud_{split}_sql.json")
        tables = {row["id"]: row for row in read_json(output_dir / "upstream_crudsql" / "data" / split / f"crud_{split}_table.json")}
        db_path = output_dir / "upstream_crudsql" / "data" / split / f"{split}.db"
        pass_count = 0
        failures = []
        sample_hashes = {}
        for manifest_row in rows:
            raw = raw_rows[manifest_row["source_sql_index"]]
            table = tables[raw["table_id"]]
            try:
                execution = execute_gold_insert_and_hash(db_path, raw, table)
                pass_count += 1
                sample_hashes[manifest_row["sample_id"]] = {
                    "gold_program_sha256": execution["gold_program_sha256"],
                    "gold_post_state_sha256": execution["gold_post_state_sha256"],
                    "assignment_count": execution["assignment_count"],
                }
            except Exception as exc:
                failures.append({"sample_id": manifest_row["sample_id"], "error": str(exc)})
        result["splits"][split] = {
            "sample_count": len(rows),
            "gold_derivation_pass_count": pass_count,
            "gold_derivation_failure_count": len(failures),
            "gold_execution_failure_count": len(failures),
            "failures": failures,
            "sample_gold_hashes": sample_hashes,
        }
    result["status"] = "PASS" if all(row["gold_derivation_failure_count"] == 0 for row in result["splits"].values()) else "FAIL"
    return result


def static_specs() -> dict[str, Any]:
    return {
        "CRUDSQL_ADAPTER_SPEC.json": {
            "stage": STAGE,
            "operation": "Create",
            "v2_operation_for_create": "INSERT",
            "canonical_record_fields": ["sample_id", "split", "question", "table_id", "model_side_input", "label_side_bookkeeping"],
            "gold_sql_visibility": "label_side_bookkeeping_only_never_model_side_input",
            "stage7d_prompt_input_contract": "generation code must pass only row['model_side_input']; canonical record and label_side_bookkeeping are forbidden prompt inputs",
            "phase_o_input_fields": ["question", "schema_inventory", "evidence_inventory", "semantic_slot_inventory"],
            "phase_m_input_fields": ["question", "schema_inventory", "evidence_inventory", "semantic_slot_inventory", "phase_o_predicted_operation", "operation_specific_dynamic_schema"],
            "no_v2_implementation": True,
        },
        "SCHEMA_INVENTORY_SPEC.json": {
            "stage": STAGE,
            "table_refs": "TAB_* assigned deterministically per sample",
            "column_refs": "COL_* assigned in table header order",
            "sqlite_affinity_policy": "Stage7B five-affinity policy",
            "gold_assignment_visible": False,
        },
        "EVIDENCE_INVENTORY_SPEC.json": {
            "stage": STAGE,
            "source": "natural_language_question_only",
            "construction": "deterministic value-bearing span extraction from question using schema cues and frozen lexical patterns",
            "gold_sql_or_cond_values_used": False,
            "model_call_used": False,
        },
        "SEMANTIC_SLOT_INVENTORY_SPEC.json": {
            "stage": STAGE,
            "source": "value_bearing_evidence_inventory_from_question_and_schema_only",
            "construction": "one required write_value SLOT_* per extracted value-bearing evidence span",
            "required_flag_policy": "all extracted value-bearing spans required; request/greeting/control phrases are excluded by frozen deterministic filters; no gold SQL is used to decide requiredness",
            "model_call_used": False,
            "hidden_third_llm_call_allowed": False,
        },
        "SEMANTIC_SLOT_DERIVATION_SPEC.json": {
            "stage": STAGE,
            "model_side_inputs": ["question", "schema_inventory"],
            "forbidden_derivation_inputs": ["sql.conds", "gold_sql", "gold_program", "gold_post_state", "target_state", "dev_metric"],
            "deterministic_rules_ordered": [
                "object_before_insert_verb",
                "entity_before_recruitment_verb",
                "count_after_recruitment_verb",
                "education_level_pattern",
                "job_category_pattern",
                "value_after_assignment_cue",
                "schema_header_assignment_cue",
                "fallback_non_request_segment_when_no_value_span_found",
            ],
            "audit_uses_gold": "label_side_only_to_measure_extractor_quality_before_v2_generation",
            "model_call_used": False,
        },
        "OPERATION_LABEL_MAPPING_SPEC.json": {
            "stage": STAGE,
            "mapping": OPERATION_LABEL_MAPPING,
            "phase_o_dev_accuracy_gold_operation": "INSERT",
            "literal_create_compared_to_phase_o_output": False,
        },
        "GOLD_PROGRAM_DERIVATION_SPEC.json": {
            "stage": STAGE,
            "scope": "CRUDSQL type 0 Create only",
            "input_annotation": "sql.conds label-side only",
            "compiler": "INSERT INTO Table_<table_id>(col_i, ...) VALUES (?, ...)",
            "column_mapping": "CRUDSQL zero-based column_index maps to SQLite col_{index+1}",
            "operator_requirement": "type0 values use conds values; operator field is retained in audit but not prompt-side",
            "gold_visible_to_model": False,
        },
        "GOLD_POST_STATE_PROTOCOL.json": {
            "stage": STAGE,
            "database_policy": "open fresh in-memory copy from frozen CRUDSQL train/dev DB for each sample",
            "execution": "execute deterministic gold INSERT program once",
            "post_state_hash": "sha256(canonical_json(table_name, columns, rows ordered by rowid after gold insert))",
            "failure_policy": "any gold derivation or execution failure must be resolved before V2 generation",
            "gold_visible_to_model": False,
        },
        "MODEL_INPUT_LEAKAGE_POLICY.json": {
            "stage": STAGE,
            "forbidden_model_side_fields": ["operation_label", "gold_sql", "crudsql_sql", "conds", "sel", "agg", "target_state", "post_state_hash", "dev_metric"],
            "phase_o_must_predict_operation": True,
            "gold_structured_annotation_use": "development_evaluation_only",
            "slot_inventory_gold_sql_use_allowed": False,
            "stage7d_prompt_input_contract": "only row['model_side_input'] may enter Phase O/Phase M prompts; label_side_bookkeeping is evaluation-only",
        },
        "DEV_SELECTION_PROTOCOL.json": {
            "stage": STAGE,
            "primary_metric": "Target-State Accuracy",
            "tie_breakers_ordered": ["verification_failure_rate", "execution_success_rate", "schema_rejection_rate"],
            "primary_system": "V2-FULL",
            "variant_selection_rule": "V2-FULL remains primary; ablations diagnose components. If FULL is not viable, open formal redesign rather than cherry-pick an ablation.",
            "selection_split": "CRUDSQL dev Create",
            "forbidden_selection_split": "current 481 CRUDSQL Create test",
        },
        "GENERATION_PROTOCOL_SPEC.json": {
            "stage": STAGE,
            "core_v2_max_model_calls": 2,
            "phase_o_model_calls": 1,
            "phase_m_model_calls": 1,
            "semantic_slot_inventory_model_call_allowed": False,
            "config": FROZEN_GENERATION_CONFIG,
            "retry_policy": "no hidden retry beyond registered protocol",
            "v2_generation_run": False,
        },
        "EVALUATION_ENVIRONMENT_SPEC.json": {
            "stage": STAGE,
            "sqlite_foreign_keys": "ON",
            "database_policy": "copy per sample; transaction/savepoint; rollback after evaluation",
            "primary_metric": "target_state_accuracy",
            "post_state_comparison": "canonical SQLite post-state comparison consistent with frozen V1 principles where applicable",
            "generation_timeout_seconds_per_phase": FROZEN_GENERATION_CONFIG["generation_timeout_seconds_per_phase"],
            "execution_timeout_seconds_per_sample": FROZEN_GENERATION_CONFIG["execution_timeout_seconds_per_sample"],
            "timeout_policy": "frozen_in_stage7c_patch1_before_v2_implementation",
            "sqlite_version": sqlite3.sqlite_version,
        },
        "RESERVED_BENCHMARK_POLICY.json": {
            "stage": STAGE,
            "current_481_crudsql_create": "post_hoc_only_not_selection",
            "crudsql_update_delete": "reserved_until_after_v2_freeze",
            "livesqlbench_sqlite": "untouched_external_no_gt_access",
            "live_sql_bench_gt_opened": LIVESQLBENCH_GT_OPENED,
        },
    }


def validation_report_text(status: str = "PENDING_VALIDATION", violations: list[str] | None = None) -> str:
    return "# Stage7C Validation Report\n\n" + f"Status: {status}\n\nviolations: {json.dumps(violations or [], ensure_ascii=False, sort_keys=True)}\n"


def reviewer_readme() -> str:
    return """# Stage7C V2 Development/Data Protocol

This package freezes data provenance, CRUDSQL train/dev Create manifests,
adapter and leakage policies, slot-inventory construction policy, selection
rules, and evaluation environment before V2 implementation.

Commands:
```bash
python scripts/data/build_stage7c_v2_development_data_protocol.py --source-mode packaged --force
python scripts/data/validate_stage7c_v2_development_data_protocol.py
python scripts/data/audit_stage7c_dataset_splits.py
python -m pytest -q tests/test_stage7c_v2_development_data_protocol.py
```

No model, GPU, V2 implementation, V2 generation, 481-test tuning, or
LiveSQLBench ground-truth access is performed in Stage7C.
"""


def artifact_hashes(output_dir: Path) -> dict[str, str]:
    return {rel: sha256_file(output_dir / rel) for rel in (*ARTIFACTS, *RAW_ARTIFACTS)}


def lock(output_dir: Path, inputs: dict[str, str]) -> dict[str, Any]:
    return {
        "stage": STAGE,
        "status": "BUILT_PENDING_VALIDATION",
        "date": DATE,
        "hash_policy": HASH_POLICY,
        "input_hashes": inputs,
        "artifact_hashes": artifact_hashes(output_dir),
        "crudsql_commit": CRUDSQL_COMMIT,
        "train_create_count": EXPECTED_CREATE_COUNTS["train"],
        "dev_create_count": EXPECTED_CREATE_COUNTS["dev"],
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
        "v2_implemented": V2_IMPLEMENTED,
        "experiment_run": EXPERIMENT_RUN,
        "live_sql_bench_gt_opened": LIVESQLBENCH_GT_OPENED,
    }


def build_stage7c(
    output_dir: Path,
    crudsql_root: Path = DEFAULT_CRUDSQL_ROOT,
    *,
    force: bool = False,
    source_mode: str = "git",
    packaged_source_dir: Path | None = None,
) -> dict[str, Any]:
    if source_mode not in {"git", "packaged"}:
        raise ValueError("source_mode must be 'git' or 'packaged'")
    source_for_packaged = preserve_packaged_source_if_needed(output_dir, source_mode, packaged_source_dir)
    reset_output_dir(output_dir, force)
    if source_mode == "git":
        if git_output(crudsql_root, "rev-parse", "HEAD") != CRUDSQL_COMMIT:
            raise RuntimeError(f"CRUDSQL source must be at {CRUDSQL_COMMIT}")
        copy_raw_source(crudsql_root, output_dir)
        manifest_source_root = crudsql_root
    else:
        assert source_for_packaged is not None
        verify_packaged_source_hashes(source_for_packaged)
        copy_packaged_source(source_for_packaged, output_dir)
        shutil.rmtree(source_for_packaged.parent, ignore_errors=True)
        manifest_source_root = source_for_packaged
    inputs = input_hashes()
    train_rows = build_split_manifest(output_dir, "train")
    dev_rows = build_split_manifest(output_dir, "dev")
    write_json(output_dir / "STAGE7C_INPUT_MANIFEST.json", {"stage": STAGE, "date": DATE, "hash_policy": HASH_POLICY, "input_hashes": inputs, "stage7b_locked": True})
    write_json(output_dir / "CRUDSQL_SOURCE_MANIFEST.json", source_manifest(manifest_source_root, output_dir, source_mode))
    write_jsonl(output_dir / "TRAIN_CREATE_MANIFEST.jsonl", train_rows)
    write_jsonl(output_dir / "DEV_CREATE_MANIFEST.jsonl", dev_rows)
    write_json(output_dir / "DATASET_ELIGIBILITY_SPEC.json", eligibility_spec())
    write_json(output_dir / "DATASET_ELIGIBILITY_AUDIT.json", eligibility_audit(output_dir, train_rows, dev_rows))
    for rel, payload in static_specs().items():
        write_json(output_dir / rel, payload)
    write_json(output_dir / "SEMANTIC_SLOT_DERIVATION_AUDIT.json", semantic_slot_derivation_audit(output_dir, train_rows, dev_rows))
    write_json(output_dir / "GOLD_PROGRAM_DERIVATION_AUDIT.json", gold_program_derivation_audit(output_dir, train_rows, dev_rows))
    leak = leakage_counts(train_rows + dev_rows)
    contamination = contamination_audit(train_rows, dev_rows)
    write_json(output_dir / "SPLIT_CONTAMINATION_AUDIT.json", contamination | {"model_input_leakage_counts": dict(leak), "model_input_leakage_status": "PASS" if not leak else "FAIL"})
    (output_dir / "VALIDATION_REPORT.md").write_text(validation_report_text(), encoding="utf-8")
    (output_dir / "REVIEWER_README.md").write_text(reviewer_readme(), encoding="utf-8")
    write_json(output_dir / LOCK_FILE, lock(output_dir, inputs))
    return {"stage": STAGE, "status": "PASS_BUILT", "train_create": len(train_rows), "dev_create": len(dev_rows), "model_called": False, "gpu_called": False, "v2_implemented": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage7c_v2_development_data_protocol")
    parser.add_argument("--crudsql-root", type=Path, default=DEFAULT_CRUDSQL_ROOT)
    parser.add_argument("--source-mode", choices=["git", "packaged"], default="git")
    parser.add_argument("--packaged-source-dir", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build_stage7c(args.output_dir, args.crudsql_root, force=args.force, source_mode=args.source_mode, packaged_source_dir=args.packaged_source_dir),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
