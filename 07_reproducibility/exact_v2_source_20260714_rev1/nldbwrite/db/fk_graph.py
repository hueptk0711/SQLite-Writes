from collections import defaultdict
import heapq

def table_insert_order(profile, selected_tables=None):
    tables={t['name'] for t in profile.get('tables', [])}
    if selected_tables: tables=tables.intersection(set(selected_tables))
    indeg={t:0 for t in tables}; children=defaultdict(set)
    for table in profile.get('tables', []):
        child=table['name']
        if child not in tables: continue
        for fk in table.get('foreign_keys', []):
            parent=fk['to_table']
            if parent in tables and parent!=child and child not in children[parent]:
                children[parent].add(child); indeg[child]+=1
    q=[t for t,d in indeg.items() if d==0]; heapq.heapify(q); out=[]
    while q:
        t=heapq.heappop(q); out.append(t)
        for c in sorted(children[t]):
            indeg[c]-=1
            if indeg[c]==0: heapq.heappush(q, c)
    for t in sorted(tables):
        if t not in out: out.append(t)
    return out
