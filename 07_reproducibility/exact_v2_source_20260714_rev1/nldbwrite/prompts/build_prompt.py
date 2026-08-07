import json
import re
from collections import OrderedDict
from pathlib import Path


def compact_sample_value(value, max_chars=80):
    text = str(value).replace('\n', ' ').strip()
    text = re.sub(r'\s+', ' ', text)
    if len(text) > max_chars:
        if text.lstrip().startswith(('<', '{', '[')):
            return '<long structured text omitted>'
        return text[:max_chars].rstrip() + '...'
    return text


def compact_schema_context(
    profile,
    linked_columns=None,
    max_columns=40,
    max_sample_values_per_column=3,
    max_sample_value_chars=80,
):
    tables={table['name']: table for table in profile.get('tables', [])}
    columns={
        (table['name'], col['name']): col
        for table in profile.get('tables', [])
        for col in table.get('columns', [])
    }
    ordered=[]
    if linked_columns is None:
        ordered=[(table['name'], col['name']) for table in profile.get('tables', []) for col in table.get('columns', [])]
    else:
        seen=set()
        for item in linked_columns:
            key=(item.get('table'), item.get('column'))
            if key in columns and key not in seen:
                seen.add(key); ordered.append(key)
    grouped=OrderedDict()
    for table_name, column_name in ordered[:max_columns]:
        grouped.setdefault(table_name, []).append(columns[(table_name, column_name)])
    lines=[]
    for table_name, selected in grouped.items():
        table=tables[table_name]
        unique_columns={c for idx in table.get('unique_indexes', []) for c in idx.get('columns', [])}
        lines.append(f"TABLE {table_name}:")
        constraints=[]
        pk_cols=table.get('primary_keys') or [
            col.get('name') for col in table.get('columns', []) if col.get('is_primary_key')
        ]
        pk_cols=[col for col in pk_cols if col]
        if pk_cols:
            constraints.append(f"PRIMARY KEY ({', '.join(pk_cols)})")
        for idx in table.get('unique_indexes', []) or []:
            cols=idx.get('columns') or []
            if cols:
                name=f" {idx.get('name')}" if idx.get('name') else ''
                constraints.append(f"UNIQUE{name} ({', '.join(cols)})")
        for fk in table.get('foreign_keys', []) or []:
            from_cols=fk.get('from_columns') or fk.get('columns') or ([fk.get('from_column')] if fk.get('from_column') else [])
            to_cols=fk.get('to_columns') or ([fk.get('to_column')] if fk.get('to_column') else [])
            if from_cols and fk.get('to_table') and to_cols:
                constraints.append(f"FOREIGN KEY ({', '.join(from_cols)}) REFERENCES {fk['to_table']}({', '.join(to_cols)})")
        if constraints:
            lines.append('Constraints: ' + '; '.join(constraints))
        for col in selected:
            pk=' PK' if col.get('is_primary_key') else ''; fk=''
            if col.get('is_foreign_key') and col.get('foreign_key'):
                ref=col['foreign_key']; fk=f" FK->{ref['to_table']}.{ref['to_column']}"
            notnull=' NOT_NULL' if col.get('not_null') else ''
            unique=' UNIQUE_KEY' if col.get('name') in unique_columns else ''
            default=f" DEFAULT={col.get('default')}" if col.get('default') is not None else ''
            samples=', '.join(
                compact_sample_value(value, max_sample_value_chars)
                for value in col.get('sample_values', [])[:max_sample_values_per_column]
            )
            lines.append(f"- {col['name']} ({col.get('type')}){pk}{fk}{notnull}{unique}{default}; samples: {samples}")
    return '\n'.join(lines)

def full_schema_context(profile, max_columns=200, max_sample_values_per_column=3, max_sample_value_chars=80):
    return compact_schema_context(
        profile,
        None,
        max_columns,
        max_sample_values_per_column=max_sample_values_per_column,
        max_sample_value_chars=max_sample_value_chars,
    )
def load_template(path): return Path(path).read_text(encoding='utf-8')
def render_prompt(template, schema_context, input_text, matched_values=None, examples='', facts_context='', schema_light_hints=''):
    return template.format(
        schema_context=schema_context,
        input_text=input_text,
        matched_values=json.dumps(matched_values or [], ensure_ascii=False, indent=2),
        examples=examples or '',
        facts_context=facts_context or '',
        schema_light_hints=schema_light_hints or '',
    )
