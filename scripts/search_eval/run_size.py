"""The 35 queries without and with the typical-size ranges (assets/sizes.py), graded with the committed grades.
Each query is searched once and the sizes only reorder (the search API answers differently between calls). Lists the
queries whose order changed and every grade-2 asset flagged off_size (a false alarm). Needs network.

    PYTHONPATH=src python scripts/search_eval/run_size.py
"""
import json
from pathlib import Path

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets import sizes
from agentic_sim_world_gen.scene.spec import ObjectSpec

D = Path(__file__).parent / "data"
grades = json.loads((D / "grades.json").read_text())
for q, extra in json.loads((D / "grades_a2.json").read_text()).items():
    grades[q].update(extra)
# Top-5 hits after the size ranges that no system had when judging; graded afterwards from name + caption (not blind)
if (D / "grades_size.json").exists():
    for q, extra in json.loads((D / "grades_size.json").read_text()).items():
        grades[q].update(extra)
queries = json.loads((D / "queries.json").read_text())


class Memo:
    def __init__(self, p):
        self.p, self.name, self.seen = p, p.name, {}

    def search(self, query, *, limit=10, size=None):
        k = (query, limit, repr(size))
        if k not in self.seen:
            self.seen[k] = self.p.search(query, limit=limit, size=size)
        return self.seen[k]


service.set_provider(Memo(service.get_provider()))
kind_of = service.kind_of
stats = {s: {"p1": 0, "p1~": 0, "wrong": 0, "n5": 0, "unjudged": 0} for s in ("before", "after")}
with_kind, false_flags, unjudged = [], [], []
for q in queries:
    o = ObjectSpec(id="x", query=q["q"], alt_queries=q["alt"])
    service.kind_of = lambda *a: None
    before = service._find_candidates(o, 10)
    service.kind_of = kind_of
    after = service._find_candidates(o, 10)
    g = grades[q["id"]]
    for s, cands in (("before", before), ("after", after)):
        top = [g.get(c["name"]) for c in cands[:5]]
        st = stats[s]
        st["p1"] += bool(top) and top[0] == 2
        st["p1~"] += bool(top) and (top[0] or 0) >= 1
        st["wrong"] += sum(x == 0 for x in top)
        st["unjudged"] += sum(x is None for x in top)
        st["n5"] += len(top)
    k = sizes.kind_of(q["q"], q["alt"])
    if k:
        with_kind.append(q["id"])
    for c in {c["name"]: c for c in before[:5] + after[:5]}.values():
        if c["name"] not in g:
            unjudged.append(f"{q['id']}: {c['name']} | {c.get('caption')}")
    for c in after:
        if c["match"].get("off_size") and g.get(c["name"]) == 2:
            false_flags.append(f"{q['id']}: {c['name']} {c['match']['off_size']}")
    b, a = [c["name"] for c in before], [c["name"] for c in after]
    if a != b:
        moved = [f"{c['name']}(g={g.get(c['name'])}; {', '.join(x for x in c['match']['off_size'] if x != 'kind')})"
                 for c in after if c["match"].get("off_size")]
        print(f"{q['id']:13s} [{k}] top {b[0]}(g={g.get(b[0])}) -> {a[0]}(g={g.get(a[0])})  moved back: {', '.join(moved)}")
n = len(queries)
print(f"\n{n} queries, {len(with_kind)} with a typical size")
print("         P@1  P@1~  wrong(top5)  unjudged(top5)")
for s, st in stats.items():
    print(f"{s:7s} {st['p1'] / n:5.2f} {st['p1~'] / n:5.2f}  {st['wrong'] / st['n5']:10.2f}  {st['unjudged']:8d}")
print("grade-2 assets flagged off_size:", false_flags or "none")
if unjudged:
    print("not graded, in a top 5:", *unjudged, sep="\n  ")
