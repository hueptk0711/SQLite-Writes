import re

UNSAFE_KEYWORDS = ['DROP', 'ALTER', 'CREATE', 'ATTACH', 'DETACH', 'PRAGMA', 'VACUUM']


def strip_literals_and_comments(sql):
    out = []
    quote = None
    bracket = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ''
        if bracket:
            out.append(' ')
            if ch == ']':
                bracket = False
            i += 1
            continue
        if quote:
            out.append(' ')
            if ch == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    out.append(' ')
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch == '-' and nxt == '-':
            while i < len(sql) and sql[i] not in '\r\n':
                out.append(' ')
                i += 1
            continue
        if ch == '/' and nxt == '*':
            out.extend('  ')
            i += 2
            while i < len(sql):
                if sql[i] == '*' and i + 1 < len(sql) and sql[i + 1] == '/':
                    out.extend('  ')
                    i += 2
                    break
                out.append(' ')
                i += 1
            continue
        if ch in ("'", '"', '`'):
            quote = ch
            out.append(' ')
        elif ch == '[':
            bracket = True
            out.append(' ')
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


def is_safe_sql(sql, allow_update=True):
    visible = strip_literals_and_comments(sql)
    upper = visible.strip().upper()
    for kw in UNSAFE_KEYWORDS:
        if re.search(rf'\b{kw}\b', upper):
            return False, f'unsafe keyword {kw}'
    allowed = ['INSERT', 'REPLACE'] + (['UPDATE'] if allow_update else [])
    if not any(upper.startswith(a) for a in allowed):
        return False, 'only INSERT/REPLACE/UPDATE statements are allowed'
    if upper.startswith('UPDATE') and not re.search(r'\bWHERE\b', upper):
        return False, 'UPDATE statements require a WHERE clause'
    return True, None
