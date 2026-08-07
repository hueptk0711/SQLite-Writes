from typing import Any

from nldbwrite.facts.gold_facts import normalize_text, normalize_value


def coerce_fact_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        facts = payload.get('facts') or payload.get('predicted_facts') or []
    elif isinstance(payload, list):
        facts = payload
    else:
        facts = []
    return [dict(fact) for fact in facts if isinstance(fact, dict)]


def canonical_fact_key(fact: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(fact.get('record_id') or ''),
        normalize_text(fact.get('attribute') or fact.get('gold_column') or ''),
        normalize_value(fact.get('value')),
    )


def assign_fact_ids(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for idx, fact in enumerate(facts, start=1):
        item = dict(fact)
        item.setdefault('record_id', f'r{idx:04d}')
        item['fact_id'] = f'f{idx:04d}'
        output.append(item)
    return output


def merge_fact_payloads(llm_payload: Any, deterministic_facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    deterministic = [dict(fact) for fact in (deterministic_facts or []) if isinstance(fact, dict)]
    llm_facts = coerce_fact_list(llm_payload)
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for source, facts in [('deterministic', deterministic), ('llm', llm_facts)]:
        for fact in facts:
            item = dict(fact)
            item.setdefault('source', source)
            key = canonical_fact_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

    payload = llm_payload if isinstance(llm_payload, dict) else {}
    return {
        'facts': assign_fact_ids(merged),
        'uncertain_facts': payload.get('uncertain_facts') or [],
        'ignored_text': payload.get('ignored_text') or [],
        'fact_merge_stats': {
            'deterministic_fact_count': len(deterministic),
            'llm_fact_count': len(llm_facts),
            'merged_fact_count': len(merged),
        },
    }
