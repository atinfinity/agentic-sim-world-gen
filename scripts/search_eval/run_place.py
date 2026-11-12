"""The same queries with Room.place = home and office, graded with the committed grades. Each query is
searched once and the place only reorders (the search API answers differently between calls). Scores the queries
that belong in a home or an office, and lists the ones whose order changed (needs network).

    PYTHONPATH=src python scripts/search_eval/run_place.py
"""
import json
from pathlib import Path

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene.spec import ObjectSpec

D = Path(__file__).parent / "data"
grades = json.loads((D / "grades.json").read_text())
for q, extra in json.loads((D / "grades_a2.json").read_text()).items():
    grades[q].update(extra)
queries = json.loads((D / "queries.json").read_text())
# not home or office things: the place does not apply to them
OTHER = {"pallet", "rack", "hand_truck", "forklift", "cone", "barrel", "hosp_bed", "wheelchair", "shelf_retail",
         "car", "server_rack"}


class Memo:
    def __init__(self, p):
        self.p, self.name, self.seen = p, p.name, {}

    def search(self, query, *, limit=10, size=None):
        k = (query, limit, repr(size))
        if k not in self.seen:
            self.seen[k] = self.p.search(query, limit=limit, size=size)
        return self.seen[k]


service.set_provider(Memo(service.get_provider()))
places = [None, "home", "office"]
p1 = {p: 0 for p in places}
wrong = {p: [0, 0] for p in places}
n = 0
for q in queries:
    if q["id"] in OTHER:
        continue
    n += 1
    o = ObjectSpec(id="x", query=q["q"], alt_queries=q["alt"])
    runs = {p: service._find_candidates(o, 10, p) for p in places}
    for p, cands in runs.items():
        g = [grades[q["id"]].get(c["name"]) for c in cands[:5]]
        p1[p] += bool(g) and g[0] == 2
        wrong[p][0] += sum(x == 0 for x in g)
        wrong[p][1] += len(g)
    base = [c["name"] for c in runs[None]]
    for p in places[1:]:
        names = [c["name"] for c in runs[p]]
        if names != base:
            g = grades[q["id"]]
            offs = [f"{c['name']}({c['match']['off_place']}, g={g.get(c['name'])})"
                    for c in runs[p] if c["match"].get("off_place")]
            print(f"{q['id']:13s} {p:6s} top {base[0]}(g={g.get(base[0])}) -> {names[0]}(g={g.get(names[0])})"
                  f"  moved back: {', '.join(offs)}")
print(f"\n{n} home/office queries   P@1   wrong(top5)")
for p in places:
    print(f"place={str(p):7s} {p1[p] / n:5.2f}  {wrong[p][0] / max(wrong[p][1], 1):5.2f}")
