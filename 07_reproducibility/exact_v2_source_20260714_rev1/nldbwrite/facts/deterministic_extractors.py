import ast
import json
import re
from typing import Any

from nldbwrite.facts.gold_facts import infer_value_type, normalize_text, normalize_value


KEY_HINT_RE = re.compile(r'(^|[_\s-])(id|code|key|uuid|email|number|no)([_\s-]|$)', re.I)


def _split_markdown_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith('|'):
        text = text[1:]
    if text.endswith('|'):
        text = text[:-1]
    return [cell.strip() for cell in text.split('|')]


def _is_markdown_separator(line: str) -> bool:
    cells = _split_markdown_row(line)
    return bool(cells) and all(re.fullmatch(r':?-{3,}:?', cell.strip()) for cell in cells)


def _is_identifier(attribute: str, value: Any) -> bool:
    attr = normalize_text(attribute)
    if KEY_HINT_RE.search(attr):
        return True
    text = str(value if value is not None else '')
    return bool(re.fullmatch(r'[A-Za-z0-9_.@:-]{3,}', text) and any(ch.isdigit() for ch in text))


def _fact(record_id: str, attribute: str, value: Any, evidence: str, source: str) -> dict[str, Any]:
    return {
        'record_id': record_id,
        'attribute': str(attribute).strip(),
        'value': value,
        'value_type': infer_value_type(value),
        'evidence': evidence.strip(),
        'source': source,
        'is_identifier_or_key': _is_identifier(str(attribute), value),
        'confidence': 1.0,
    }


def _dedupe(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for fact in facts:
        key = (
            str(fact.get('record_id') or ''),
            normalize_text(fact.get('attribute') or ''),
            normalize_value(fact.get('value')),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(fact)
    return out


def extract_markdown_table_facts(input_text: str) -> list[dict[str, Any]]:
    lines = input_text.splitlines()
    facts: list[dict[str, Any]] = []
    table_idx = 0
    i = 0
    while i < len(lines) - 1:
        if '|' not in lines[i] or '|' not in lines[i + 1] or not _is_markdown_separator(lines[i + 1]):
            i += 1
            continue
        headers = _split_markdown_row(lines[i])
        if not headers:
            i += 1
            continue
        table_idx += 1
        row_idx = 0
        j = i + 2
        while j < len(lines) and '|' in lines[j]:
            cells = _split_markdown_row(lines[j])
            if len(cells) >= 2:
                row_idx += 1
                record_id = f'md{table_idx}_r{row_idx}'
                for header, value in zip(headers, cells):
                    if str(value).strip():
                        facts.append(_fact(record_id, header, value, lines[j], 'markdown_table'))
            j += 1
        i = j
    return _dedupe(facts)


def _json_candidates(input_text: str) -> list[Any]:
    text = input_text.strip()
    candidates = [text]
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if 0 <= start < end:
            candidates.append(text[start:end + 1])
    parsed = []
    for candidate in candidates:
        try:
            parsed.append(json.loads(candidate))
            continue
        except Exception:
            pass
        try:
            parsed.append(ast.literal_eval(candidate))
        except Exception:
            pass
    return parsed


def _iter_json_records(obj: Any, prefix: str = '') -> list[dict[str, Any]]:
    if isinstance(obj, list):
        records = []
        for item in obj:
            if isinstance(item, dict):
                records.append(item)
        return records
    if isinstance(obj, dict):
        scalar_items = {k: v for k, v in obj.items() if not isinstance(v, (dict, list))}
        records = [scalar_items] if scalar_items else []
        for key, value in obj.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        records.append({f'{key}.{k}': v for k, v in item.items()})
            elif isinstance(value, dict):
                records.extend(_iter_json_records(value, f'{prefix}{key}.'))
        return records
    return []


def extract_json_like_facts(input_text: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for obj in _json_candidates(input_text):
        records = _iter_json_records(obj)
        if not records:
            continue
        for rec_idx, record in enumerate(records, start=1):
            record_id = f'json_r{rec_idx}'
            for attribute, value in record.items():
                if value is None or isinstance(value, (str, int, float, bool)):
                    facts.append(_fact(record_id, attribute, value, json.dumps(record, ensure_ascii=False), 'json_like'))
        break
    return _dedupe(facts)


PAIR_RE = re.compile(r'([^:;,\n=]{1,60})\s*(?::|=)\s*([^;,\n]+)')
BULLET_RE = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s+(.+)$')


def extract_bullet_list_facts(input_text: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    row_idx = 0
    for line in input_text.splitlines():
        match = BULLET_RE.match(line)
        if not match:
            continue
        body = match.group(1).strip()
        pairs = [(k.strip(), v.strip()) for k, v in PAIR_RE.findall(body) if k.strip() and v.strip()]
        if not pairs:
            continue
        row_idx += 1
        record_id = f'bullet_r{row_idx}'
        for key, value in pairs:
            facts.append(_fact(record_id, key, value, line, 'bullet_list'))
    return _dedupe(facts)


def extract_deterministic_facts(input_text: str, max_facts: int = 500) -> list[dict[str, Any]]:
    facts = []
    facts.extend(extract_markdown_table_facts(input_text or ''))
    facts.extend(extract_json_like_facts(input_text or ''))
    facts.extend(extract_bullet_list_facts(input_text or ''))
    return _dedupe(facts)[:max_facts]
