from difflib import SequenceMatcher

try: from rapidfuzz import fuzz
except Exception: fuzz=None


def partial_ratio(needle, haystack):
    if fuzz is not None:
        return float(fuzz.partial_ratio(needle, haystack))
    if needle in haystack:
        return 100.0
    if not needle or not haystack:
        return 0.0
    shorter, longer = sorted((needle, haystack), key=len)
    matcher = SequenceMatcher(None, shorter, longer)
    best = 0.0
    for block in matcher.get_matching_blocks():
        start = max(0, block[1] - block[0])
        window = longer[start:start + len(shorter)]
        best = max(best, 100.0 * SequenceMatcher(None, shorter, window).ratio())
    return best

def match_values(input_text, profile, threshold=80, max_matches=30):
    text=input_text.lower(); matches=[]
    for table in profile.get('tables', []):
        for col in table.get('columns', []):
            for v in col.get('sample_values', []):
                if v is None or not str(v).strip(): continue
                score=partial_ratio(str(v).lower(), text)
                if score>=threshold: matches.append({'table':table['name'],'column':col['name'],'value':str(v),'score':float(score)})
    matches.sort(key=lambda x:x['score'], reverse=True)
    return matches[:max_matches]
