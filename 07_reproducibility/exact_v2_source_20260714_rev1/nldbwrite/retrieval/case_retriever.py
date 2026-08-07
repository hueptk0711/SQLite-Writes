import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from nldbwrite.common import load_json, read_id_file, sha256_file
from nldbwrite.data.annotate_complexity import infer_input_type
from nldbwrite.retrieval.schema_retriever import bm25_scores, tokenize


ORACLE_HYBRID_RETRIEVERS = {
    'hybrid_oracle',
    'hybrid_oracle_mmr',
    'hybrid_dense_oracle',
}
HYBRID_RETRIEVERS = {
    'hybrid',
    'hybrid_mmr',
    'hybrid_dense',
    *ORACLE_HYBRID_RETRIEVERS,
}


def source_group(sample: dict[str, Any]) -> str:
    return str(sample.get('source_group_id') or sample.get('provenance', {}).get('source_sample_id') or sample.get('id'))


def source_seed(sample: dict[str, Any]) -> str:
    provenance = sample.get('provenance') or {}
    return str(provenance.get('source_sample_id') or sample.get('source_id') or source_group(sample))


def gold_signature(sample: dict[str, Any]) -> str:
    payload = {
        'db_id': sample.get('db_id'),
        'operation_type': sample.get('operation_type'),
        'gold_tables': sorted(sample.get('gold_tables') or []),
        'gold_columns': sorted(sample.get('gold_columns') or []),
        'gold_records': sample.get('gold_records') or [],
        'gold_sql': sample.get('gold_sql') or [],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def leakage_reasons(
    query: dict[str, Any],
    candidate: dict[str, Any],
    include_gold_signature: bool = True,
) -> list[str]:
    reasons = []
    if str(query.get('id')) == str(candidate.get('id')):
        reasons.append('same_sample_id')
    if source_group(query) == source_group(candidate):
        reasons.append('same_source_group')
    if source_seed(query) == source_seed(candidate):
        reasons.append('same_source_seed')
    if include_gold_signature and gold_signature(query) == gold_signature(candidate):
        reasons.append('same_gold_signature')
    return reasons


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def normalize_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if math.isclose(lo, hi):
        return [1.0 if value > 0 else 0.0 for value in values]
    return [(value - lo) / (hi - lo) for value in values]


def case_schema_terms(sample: dict[str, Any]) -> set[str]:
    terms = set(str(x).casefold() for x in sample.get('gold_tables') or [])
    terms.update(str(x).casefold() for x in sample.get('gold_columns') or [])
    return terms


def query_schema_terms(linked_columns: list[dict[str, Any]] | None, sample: dict[str, Any]) -> set[str]:
    terms = set()
    for item in linked_columns or []:
        table = item.get('table')
        column = item.get('column')
        if table:
            terms.add(str(table).casefold())
        if table and column:
            terms.add(f'{table}.{column}'.casefold())
    if not terms:
        for token in tokenize(sample.get('input_text', '')):
            terms.add(token.casefold())
    return terms


def slim_case_log(case: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in case.items() if k not in {'input_text', 'gold_records', 'gold_sql'}}


class CaseBank:
    def __init__(self, data_path: str | Path, split_ids_path: str | Path | None = None):
        self.data_path = Path(data_path)
        self.split_ids_path = Path(split_ids_path) if split_ids_path else None
        data = load_json(self.data_path)
        allowed = read_id_file(self.split_ids_path) if self.split_ids_path and self.split_ids_path.exists() else None
        self.samples = [x for x in data if allowed is None or str(x.get('id')) in allowed]
        self.docs = [tokenize(x.get('input_text', '')) for x in self.samples]
        self._dense_model = None
        self._dense_embeddings = None
        self._dense_model_name = None
        self.data_sha256 = sha256_file(self.data_path)
        self.split_sha256 = sha256_file(self.split_ids_path) if self.split_ids_path and self.split_ids_path.exists() else None

    def metadata(self) -> dict[str, Any]:
        return {
            'data_path': str(self.data_path),
            'split_ids_path': str(self.split_ids_path) if self.split_ids_path else None,
            'num_cases': len(self.samples),
            'data_sha256': self.data_sha256,
            'split_sha256': self.split_sha256,
        }

    def _candidate_pool(
        self,
        query: dict[str, Any],
        setting: str,
        include_gold_signature: bool = True,
    ) -> tuple[list[dict[str, Any]], list[int], dict[str, int]]:
        setting = str(setting or 'mixed').lower()
        pool = []
        original_indices = []
        leakage_counts = Counter()
        for idx, candidate in enumerate(self.samples):
            reasons = leakage_reasons(query, candidate, include_gold_signature)
            if reasons:
                leakage_counts.update(reasons)
                continue
            same_db = candidate.get('db_id') == query.get('db_id')
            if setting in {'same_db', 'same-database'} and not same_db:
                continue
            if setting in {'cross_db', 'cross-database'} and same_db:
                continue
            pool.append(candidate)
            original_indices.append(idx)
        return pool, original_indices, dict(leakage_counts)

    def retrieve(
        self,
        query: dict[str, Any],
        k: int = 3,
        retriever: str = 'bm25',
        setting: str = 'mixed',
        linked_columns: list[dict[str, Any]] | None = None,
        weights: dict[str, float] | None = None,
        use_mmr: bool = False,
        mmr_lambda: float = 0.75,
        dense_model_name: str = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
        dense_batch_size: int = 64,
        metadata_policy: str | None = None,
        unique_source_groups: bool = True,
        max_demo_source_records: int = 0,
        max_demo_sql_chars: int = 0,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        retriever_name = str(retriever).lower()
        metadata_policy = str(
            metadata_policy or ('oracle' if retriever_name in ORACLE_HYBRID_RETRIEVERS else 'deployable')
        ).lower()
        if metadata_policy not in {'deployable', 'oracle'}:
            raise ValueError(f'Unsupported CBR metadata policy: {metadata_policy}')
        uses_gold_query_metadata = metadata_policy == 'oracle'
        pool, original_indices, leakage_counts = self._candidate_pool(
            query,
            setting,
            include_gold_signature=uses_gold_query_metadata,
        )
        if not pool or k <= 0:
            return [], {
                'retriever': retriever,
                'retrieval_setting': setting,
                'retrieved_case_ids': [],
                'retrieved_source_groups': [],
                'retrieval_scores': [],
                'same_db_or_cross_db': [],
                'leakage_check_passed': True,
                'filtered_leakage_counts': leakage_counts,
                'candidate_pool_size': len(pool),
                'metadata_policy': metadata_policy,
                'gold_query_metadata_used': uses_gold_query_metadata,
            }
        demo_size_filtered = 0
        filtered_pool = []
        filtered_indices = []
        for candidate, original_index in zip(pool, original_indices):
            record_count = len(candidate.get('gold_records') or [])
            sql_chars = len('\n'.join(candidate.get('gold_sql') or []))
            if max_demo_source_records > 0 and record_count > max_demo_source_records:
                demo_size_filtered += 1
                continue
            if max_demo_sql_chars > 0 and sql_chars > max_demo_sql_chars:
                demo_size_filtered += 1
                continue
            filtered_pool.append(candidate)
            filtered_indices.append(original_index)
        if len(filtered_pool) >= k:
            pool = filtered_pool
            original_indices = filtered_indices
        else:
            demo_size_filtered = 0

        query_tokens = tokenize(query.get('input_text', ''))
        text_raw = bm25_scores([self.docs[i] for i in original_indices], query_tokens)
        text_scores = normalize_scores(text_raw)
        dense_scores = [0.0] * len(pool)
        if retriever_name in {'dense', 'hybrid_dense', 'hybrid_dense_oracle', 'dense_mmr'}:
            dense_scores = self._dense_scores(
                query.get('input_text', ''),
                original_indices,
                dense_model_name,
                dense_batch_size,
            )
        q_schema = query_schema_terms(linked_columns, query)
        weights = weights or {}
        alpha = float(weights.get('text', 1.0))
        beta = float(weights.get('operation', 0.15))
        gamma = float(weights.get('schema', 0.20))
        delta = float(weights.get('difficulty', 0.05))
        epsilon = float(weights.get('input_type', 0.05))
        query_input_type = infer_input_type(query.get('input_text', ''))
        scored = []
        for idx, candidate in enumerate(pool):
            operation_match = 0.0
            if uses_gold_query_metadata:
                operation_match = 1.0 if str(candidate.get('operation_type')) == str(query.get('operation_type')) else 0.0
            schema_overlap = jaccard(q_schema, case_schema_terms(candidate))
            difficulty_match = 0.0
            if uses_gold_query_metadata:
                difficulty_match = 1.0 if str(candidate.get('auto_difficulty') or candidate.get('difficulty')) == str(query.get('auto_difficulty') or query.get('difficulty')) else 0.0
            candidate_input_type = infer_input_type(candidate.get('input_text', ''))
            input_type_match = 1.0 if candidate_input_type == query_input_type else 0.0
            if retriever_name in HYBRID_RETRIEVERS:
                text_component = (
                    (text_scores[idx] + dense_scores[idx]) / 2.0
                    if retriever_name in {'hybrid_dense', 'hybrid_dense_oracle'}
                    else text_scores[idx]
                )
                score = alpha * text_component + beta * operation_match + gamma * schema_overlap + delta * difficulty_match + epsilon * input_type_match
            elif retriever_name in {'dense', 'dense_mmr'}:
                score = dense_scores[idx]
            else:
                score = text_scores[idx]
            scored.append({
                'sample': candidate,
                'score': float(score),
                'components': {
                    'text_similarity': float(text_scores[idx]),
                    'dense_similarity': float(dense_scores[idx]),
                    'operation_match': operation_match,
                    'schema_overlap': schema_overlap,
                    'difficulty_match': difficulty_match,
                    'input_type_match': input_type_match,
                },
            })
        if unique_source_groups:
            grouped: dict[str, dict[str, Any]] = {}
            for item in sorted(scored, key=lambda x: x['score'], reverse=True):
                group = source_group(item['sample'])
                if group not in grouped:
                    grouped[group] = item
            scored = list(grouped.values())
        selected = self._select_mmr(scored, k, mmr_lambda, unique_source_groups=unique_source_groups) if use_mmr or retriever_name in {'hybrid_mmr', 'hybrid_oracle_mmr', 'dense_mmr'} else sorted(scored, key=lambda x: x['score'], reverse=True)[:k]
        cases = [
            self._case_log_entry(
                query,
                item['sample'],
                item['score'],
                item['components'],
                include_gold_signature=uses_gold_query_metadata,
            )
            for item in selected
        ]
        log = {
            'retriever': retriever,
            'retrieval_setting': setting,
            'retrieved_case_ids': [case['case_id'] for case in cases],
            'retrieved_source_groups': [case['source_group_id'] for case in cases],
            'retrieval_scores': [case['score'] for case in cases],
            'same_db_or_cross_db': [case['same_db_or_cross_db'] for case in cases],
            'leakage_check_passed': all(not case['leakage_reasons'] for case in cases),
            'filtered_leakage_counts': leakage_counts,
            'candidate_pool_size': len(pool),
            'filtered_demo_size_count': demo_size_filtered,
            'cases': [slim_case_log(case) for case in cases],
            'metadata_policy': metadata_policy,
            'gold_query_metadata_used': uses_gold_query_metadata,
            'unique_source_groups': bool(unique_source_groups),
            'query_signals': {
                'text': True,
                'predicted_schema': retriever_name in HYBRID_RETRIEVERS,
                'raw_input_type': retriever_name in HYBRID_RETRIEVERS,
                'gold_operation': uses_gold_query_metadata,
                'gold_difficulty': uses_gold_query_metadata,
            },
        }
        return cases, log

    def _dense_scores(
        self,
        query_text: str,
        original_indices: list[int],
        model_name: str,
        batch_size: int,
    ) -> list[float]:
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                'Dense CBR requires sentence-transformers. Install requirements-server.txt first.'
            ) from exc
        if self._dense_model is None or self._dense_model_name != model_name:
            self._dense_model = SentenceTransformer(model_name)
            self._dense_model_name = model_name
            self._dense_embeddings = self._dense_model.encode(
                [sample.get('input_text', '') for sample in self.samples],
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        query_embedding = self._dense_model.encode(
            [query_text],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]
        matrix = np.asarray(self._dense_embeddings)[original_indices]
        similarities = matrix @ np.asarray(query_embedding)
        return [float(max(0.0, value)) for value in similarities]

    def static_cases(
        self,
        query: dict[str, Any],
        k: int = 3,
        setting: str = 'mixed',
        preferred_ids: list[str] | None = None,
        selection: str = 'curated',
        seed: int = 2026,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        pool, _, leakage_counts = self._candidate_pool(query, setting, include_gold_signature=False)
        selected = []
        if preferred_ids:
            wanted = {str(x) for x in preferred_ids}
            selected.extend([x for x in pool if str(x.get('id')) in wanted])
        seen_groups = set(source_group(item) for item in selected)
        ordered = sorted(pool, key=lambda x: (int(x.get('row_count') or x.get('num_records') or 9999), x.get('operation_type', ''), x.get('db_id', ''), x.get('id', '')))
        if str(selection).lower() == 'random':
            import random
            rng = random.Random(f'{seed}:{query.get("id")}')
            rng.shuffle(ordered)
        for item in ordered:
            group = source_group(item)
            if group in seen_groups:
                continue
            if str(item.get('id')) in {str(x.get('id')) for x in selected}:
                continue
            selected.append(item)
            seen_groups.add(group)
            if len(selected) >= k:
                break
        cases = [self._case_log_entry(query, item, 1.0, {'static_example': 1.0}, include_gold_signature=False) for item in selected[:k]]
        return cases, {
            'retriever': f'static_{selection}',
            'retrieval_setting': setting,
            'retrieved_case_ids': [case['case_id'] for case in cases],
            'retrieved_source_groups': [case['source_group_id'] for case in cases],
            'retrieval_scores': [case['score'] for case in cases],
            'same_db_or_cross_db': [case['same_db_or_cross_db'] for case in cases],
            'leakage_check_passed': all(not case['leakage_reasons'] for case in cases),
            'filtered_leakage_counts': leakage_counts,
            'candidate_pool_size': len(pool),
            'cases': [slim_case_log(case) for case in cases],
            'metadata_policy': 'deployable',
            'gold_query_metadata_used': False,
        }

    def _select_mmr(self, scored: list[dict[str, Any]], k: int, mmr_lambda: float, unique_source_groups: bool = True) -> list[dict[str, Any]]:
        remaining = sorted(scored, key=lambda x: x['score'], reverse=True)
        selected: list[dict[str, Any]] = []
        selected_groups: set[str] = set()
        while remaining and len(selected) < k:
            best_item = None
            best_value = -1e9
            for item in remaining:
                group = source_group(item['sample'])
                if unique_source_groups and group in selected_groups:
                    continue
                candidate_tokens = set(tokenize(item['sample'].get('input_text', '')))
                if selected:
                    diversity_penalty = max(jaccard(candidate_tokens, set(tokenize(sel['sample'].get('input_text', '')))) for sel in selected)
                else:
                    diversity_penalty = 0.0
                value = mmr_lambda * item['score'] - (1.0 - mmr_lambda) * diversity_penalty
                if value > best_value:
                    best_value = value
                    best_item = item
            if best_item is None:
                break
            selected.append(best_item)
            selected_groups.add(source_group(best_item['sample']))
            remaining.remove(best_item)
        return selected

    def _case_log_entry(
        self,
        query: dict[str, Any],
        candidate: dict[str, Any],
        score: float,
        components: dict[str, Any],
        include_gold_signature: bool = True,
    ) -> dict[str, Any]:
        same_db = candidate.get('db_id') == query.get('db_id')
        return {
            'case_id': str(candidate.get('id')),
            'db_id': candidate.get('db_id'),
            'source_group_id': source_group(candidate),
            'operation_type': candidate.get('operation_type'),
            'input_type': candidate.get('input_type'),
            'auto_difficulty': candidate.get('auto_difficulty') or candidate.get('difficulty'),
            'gold_tables': candidate.get('gold_tables') or [],
            'gold_columns': candidate.get('gold_columns') or [],
            'score': float(score),
            'score_components': components,
            'same_db_or_cross_db': 'same_db' if same_db else 'cross_db',
            'leakage_reasons': leakage_reasons(query, candidate, include_gold_signature),
            'input_text': candidate.get('input_text', ''),
            'gold_records': candidate.get('gold_records') or [],
            'gold_sql': candidate.get('gold_sql') or [],
        }


def truncate_text(text: str, max_chars: int) -> str:
    text = str(text or '').strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + ' ...'


def limited_records(records: list[dict[str, Any]], max_records: int) -> list[dict[str, Any]]:
    if max_records <= 0:
        return records
    return records[:max_records]


def limited_sql_statements(sqls: list[str], max_chars: int, max_statements: int = 0) -> list[str]:
    if max_chars <= 0:
        out = list(sqls)
        return out[:max_statements] if max_statements > 0 else out
    kept: list[str] = []
    total = 0
    for sql in sqls:
        sql = str(sql).strip()
        if not sql:
            continue
        if max_statements > 0 and len(kept) >= max_statements:
            break
        projected = total + len(sql) + (1 if kept else 0)
        if kept and projected > max_chars:
            break
        kept.append(sql)
        total = projected
        if total >= max_chars:
            break
    return kept


def format_cases(cases: list[dict[str, Any]], demo_type: str = 'json', max_input_chars: int = 700, max_records: int = 5, max_sql_chars: int = 900) -> str:
    demo_type = str(demo_type or 'json').lower()
    blocks = []
    for i, case in enumerate(cases, start=1):
        header = (
            f'Example {i} '
            f'(case_id={case["case_id"]}, db={case.get("db_id")}, operation={case.get("operation_type")}, '
            f'{case.get("same_db_or_cross_db")}):'
        )
        input_text = truncate_text(case.get('input_text', ''), max_input_chars)
        records = limited_records(case.get('gold_records') or [], max_records)
        sqls = limited_sql_statements(case.get('gold_sql') or [], max_sql_chars, len(records) if records else max_records)
        if demo_type == 'sql':
            output = '\n'.join(sqls)
            label = 'SQL'
        elif demo_type in {'sql_json', 'json_sql'}:
            common_n = min(len(records), len(sqls)) if records and sqls else max(len(records), len(sqls))
            records = records[:common_n]
            sqls = sqls[:common_n]
            output = json.dumps({'records': records}, ensure_ascii=False, indent=2)
            output += '\nSQL:\n' + '\n'.join(sqls)
            label = 'JSON then SQL'
        elif demo_type == 'facts_json':
            facts = []
            for rec_idx, rec in enumerate(case.get('gold_records') or [], start=1):
                for col, value in (rec.get('values') or {}).items():
                    facts.append({
                        'record_id': f'r{rec_idx}',
                        'attribute': col,
                        'value': value,
                    })
            output = json.dumps({
                'facts': facts[:max_records * 10],
                'records': records,
            }, ensure_ascii=False, indent=2)
            label = 'FACTS then JSON'
        else:
            output = json.dumps({'records': records}, ensure_ascii=False, indent=2)
            label = 'JSON'
        blocks.append(f'{header}\nInput:\n{input_text}\n{label}:\n{output}')
    return '\n\n'.join(blocks)
