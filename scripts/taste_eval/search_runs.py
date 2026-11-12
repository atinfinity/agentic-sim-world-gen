"""Candidates (with thumbnails) of the search-eval queries, as the current pipeline returns them (network).

    PYTHONPATH=src python scripts/taste_eval/search_runs.py scripts/taste_eval/data/search_runs.json
"""
import json
import sys
from pathlib import Path

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene.spec import ObjectSpec

Q = json.load(open(Path(__file__).parents[1] / "search_eval/data/queries.json"))
out = {}
for q in Q:
    cs = service._find_candidates(ObjectSpec(id="x", query=q["q"], alt_queries=q["alt"]), 10)
    out[q["id"]] = [{"name": c["name"], "thumb": c.get("thumbnail_url"), "score": c["match"]["score"],
                     "weak": c["match"]["weak"]} for c in cs]
    print(q["id"], len(cs), file=sys.stderr)
json.dump(out, open(sys.argv[1], "w"), indent=1)
