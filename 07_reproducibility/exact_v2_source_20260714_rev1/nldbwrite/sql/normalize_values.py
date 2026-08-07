import re
from difflib import SequenceMatcher


def sqlite_affinity(declared_type: str | None) -> str:
    t = (declared_type or '').upper()
    if 'INT' in t:
        return 'INTEGER'
    if any(x in t for x in ['CHAR', 'CLOB', 'TEXT']):
        return 'TEXT'
    if 'BLOB' in t or not t:
        return 'BLOB'
    if any(x in t for x in ['REAL', 'FLOA', 'DOUB']):
        return 'REAL'
    return 'NUMERIC'


NULL_TEXT = {'null', 'none', 'nil', '', 'không rõ', 'khong ro', 'chưa có', 'chua co', 'n/a', 'na'}
TRUE_TEXT = {'true', 'yes', 'y', 'có', 'co', 'đúng', 'dung'}
FALSE_TEXT = {'false', 'no', 'n', 'không', 'khong', 'sai'}


def normalize_date_text(value: str, col_type: str | None):
    declared = (col_type or '').upper()
    if not any(token in declared for token in ('DATE', 'TIME')) and not re.search(r'\bth[aá]ng\b.*\bn[aă]m\b', value, re.I):
        return None
    match = re.search(r'th[aá]ng\s*(\d{1,2})\s*(?:n[aă]m\s*)?(\d{4})', value, re.I)
    if match:
        return f'{int(match.group(2)):04d}-{int(match.group(1)):02d}'
    return None


def looks_like_calendar_date(value: str) -> bool:
    text = value.strip()
    return bool(
        re.match(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T].*)?$', text)
        or re.match(r'^\d{1,2}[-/]\d{1,2}[-/]\d{4}(?:[ T].*)?$', text)
    )


def numeric_from_text(value: str, integer=False):
    match = re.search(r'[-+]?\d[\d\s.,]*', value)
    if not match:
        raise ValueError('no numeric token')
    token = re.sub(r'\s+', '', match.group(0))
    if integer:
        token = token.replace('.', '').replace(',', '')
        return int(token)
    if '.' in token and ',' in token:
        decimal = '.' if token.rfind('.') > token.rfind(',') else ','
        thousands = ',' if decimal == '.' else '.'
        token = token.replace(thousands, '').replace(decimal, '.')
    elif token.count('.') + token.count(',') == 1:
        separator = '.' if '.' in token else ','
        left, right = token.split(separator)
        token = left + right if len(right) == 3 else left + '.' + right
    else:
        token = token.replace('.', '').replace(',', '')
    number = float(token)
    return int(number) if number.is_integer() else number


def enum_match(value: str, sample_values):
    text = value.strip().casefold()
    candidates = [str(x) for x in sample_values or [] if x is not None]
    for candidate in candidates:
        if candidate.strip().casefold() == text:
            return candidate
    scored = [(SequenceMatcher(None, text, candidate.strip().casefold()).ratio(), candidate) for candidate in candidates]
    if scored:
        score, candidate = max(scored)
        if score >= 0.94:
            return candidate
    return value


def normalize_value_for_sql(value, col_type=None, sample_values=None):
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        lowered = text.casefold()
        if lowered in NULL_TEXT:
            return None
        # Several source databases declare date columns with INTEGER/NUMERIC
        # affinity while storing ISO date text. Never collapse 2024-04-29 to
        # integer 2024 merely because the declaration is misleading.
        if looks_like_calendar_date(text):
            return text
        date_value = normalize_date_text(text, col_type)
        if date_value is not None:
            return date_value

    affinity = sqlite_affinity(col_type)
    try:
        if affinity == 'INTEGER':
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value) if value.is_integer() else value
            if isinstance(value, str):
                lowered = value.strip().casefold()
                if lowered in TRUE_TEXT:
                    return 1
                if lowered in FALSE_TEXT:
                    return 0
                return numeric_from_text(value, integer=True)
        if affinity == 'REAL':
            if isinstance(value, str):
                return float(numeric_from_text(value))
            return float(value)
        if affinity == 'NUMERIC':
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, (int, float)):
                return int(value) if isinstance(value, float) and value.is_integer() else value
            if isinstance(value, str):
                lowered = value.strip().casefold()
                if lowered in TRUE_TEXT:
                    return 1
                if lowered in FALSE_TEXT:
                    return 0
                return numeric_from_text(value)
        if affinity == 'TEXT':
            return enum_match(str(value), sample_values)
    except Exception:
        return enum_match(str(value), sample_values) if isinstance(value, str) else value
    return value
