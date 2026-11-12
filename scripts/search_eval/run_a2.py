"""A2 = A with the query rules: the head noun must name the asset, and weak hits are dropped when a good one exists.
Writes data/runs_a2.json {qid: {"names": [...], "matched": [...], "substitute": bool}} (needs network).

    PYTHONPATH=src python scripts/search_eval/run_a2.py
"""
import json
from pathlib import Path

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene.spec import ObjectSpec, Placement

D = Path(__file__).parent / "data"
out = {}
for q in json.loads((D / "queries.json").read_text()):
    cands = service._find_candidates(ObjectSpec(id="x", query=q["q"], alt_queries=q["alt"]), 10)
    out[q["id"]] = {"names": [c["name"] for c in cands], "matched": [c["match"]["matched"] for c in cands], "substitute": Placement(object_id="x", candidates=cands).substitute}
    print(q["id"], len(cands), out[q["id"]]["substitute"], out[q["id"]]["names"][:3], flush=True)
(D / "runs_a2.json").write_text(json.dumps(out, indent=1))
