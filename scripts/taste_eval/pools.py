"""Candidate pools for the taste evaluation: what the current search returns for the objects of a
study and a living room, with thumbnails (network).

    PYTHONPATH=src python scripts/taste_eval/pools.py scripts/taste_eval/data/pools.json
"""
import json
import sys

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene.spec import ObjectSpec
ROOMS = {
 "study": [("desk","desk",["writing desk","computer desk"]),("chair","office chair",["desk chair","task chair"]),
           ("bookshelf","bookshelf",["bookcase","shelving unit"]),("lamp","desk lamp",["table lamp"]),
           ("cabinet","filing cabinet",["drawer cabinet","side cabinet"]),("trash","trash can",["waste bin"])],
 "living": [("sofa","sofa",["couch","loveseat"]),("coffee_table","coffee table",["low table"]),
            ("tv_stand","tv stand",["media console","tv cabinet"]),("armchair","armchair",["lounge chair","accent chair"]),
            ("side_table","side table",["end table"]),("floor_lamp","floor lamp",["standing lamp"]),
            ("shelf","bookshelf",["bookcase","display shelf"]),("dining_chair","dining chair",["wooden chair"])],
}
out = {}
for room, objs in ROOMS.items():
    out[room] = {}
    for oid, q, alt in objs:
        cs = service._find_candidates(ObjectSpec(id=oid, query=q, alt_queries=alt), 20)
        out[room][oid] = [{"name": c["name"], "thumb": c.get("thumbnail_url"), "caption": c.get("caption"),
                           "weak": c["match"]["weak"], "score": c["match"]["score"]} for c in cs]
        print(room, oid, len(cs), sum(not c["match"]["weak"] for c in cs), file=sys.stderr)
json.dump(out, open(sys.argv[1], "w"), indent=1)
