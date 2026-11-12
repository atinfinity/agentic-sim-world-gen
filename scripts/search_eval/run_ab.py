"""A = current pipeline (query + alt, SimReady + Isaac, metadata rerank). B = raw SimReady hybrid search.
A_ja / B_ja = the Japanese query as is. Output: {system: {qid: [corpus keys]}}."""
import json, sys
from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.providers.simready_central import SimReadyCentralProvider
from agentic_sim_world_gen.scene.spec import ObjectSpec

E = sys.argv[1]
queries = json.load(open(f"{E}/queries.json"))
keys = {c["name"] for c in json.load(open(f"{E}/corpus.json"))}
out = {"A": {}, "A_ja": {}, "B": {}, "B_ja": {}}
raw = SimReadyCentralProvider()
miss = set()
def k(names):
    for n in names:
        if n not in keys: miss.add(n)
    return [n for n in names if n in keys]
for q in queries:
    out["A"][q["id"]] = k([c["name"] for c in service._find_candidates(ObjectSpec(id="x", query=q["q"], alt_queries=q["alt"]), 10)])
    out["A_ja"][q["id"]] = k([c["name"] for c in service._find_candidates(ObjectSpec(id="x", query=q["ja"]), 10)])
    out["B"][q["id"]] = k([r.name for r in raw.search(q["q"], limit=10)])
    out["B_ja"][q["id"]] = k([r.name for r in raw.search(q["ja"], limit=10)])
    print(q["id"], out["A"][q["id"]][:3], flush=True)
json.dump(out, open(f"{E}/runs_ab.json", "w"), indent=1)
print("not in corpus:", sorted(miss)[:20], len(miss))
