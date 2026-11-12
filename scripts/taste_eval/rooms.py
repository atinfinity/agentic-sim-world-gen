"""End to end on real scenes: a 書斎 and a リビング with coherent picks, then one planted off-taste
pick; check_taste should flag the planted object and offer candidates that fit. Network (SimReady
search) and the model (see eval.py). Writes the scenes under the given directory.

    HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES= PYTHONPATH=src python scripts/taste_eval/rooms.py /tmp/taste_rooms [--pool N] [--old]

--old: the residual baseline is each object's scene candidates (before the wider pool); --pool: its size.
"""
import argparse
import json
from pathlib import Path

from agentic_sim_world_gen import service, taste

ROOMS = {
    "study": {
        "room": {"width": 3.6, "depth": 2.7, "height": 2.4, "place": "home"},
        "objects": [
            {"id": "desk", "query": "desk", "alt_queries": ["writing desk", "computer desk"], "layout": {"against_wall": "north"}},
            {"id": "chair", "query": "office chair", "alt_queries": ["desk chair", "task chair"], "layout": {"in_front_of": "desk"}},
            {"id": "bookshelf", "query": "bookshelf", "alt_queries": ["bookcase", "shelving unit"], "layout": {"against_wall": "west"}},
            {"id": "cabinet", "query": "filing cabinet", "alt_queries": ["drawer cabinet"], "layout": {"against_wall": "east"}},
            {"id": "trash", "query": "trash can", "alt_queries": ["waste bin"], "layout": {"next_to": "desk"}},
        ],
        # coherent (white / light gray) picks, then the planted one
        "picks": {"desk": "sm_desk_a01_01.usd", "chair": "sm_chair_hospital_b03_01.usd",
                  "bookshelf": "sm_cabinet_file_a02_01.usd", "cabinet": "sm_cabinet_file_d04_01.usd",
                  "trash": "pedal_trash_can.usda"},
        "plants": {"bookshelf": "sm_shelve_steel_heavyDuty_a06_01.usd", "chair": "sm_chair_office_amf_black_a01_01.usd",
                   "cabinet": "sm_equipment_rolling_tool_cabinet_a01_01.usd",
                   "trash": "sm_trashCan_domed_blue_b01_01.usd"},
    },
    "living": {
        "room": {"width": 4.5, "depth": 3.6, "height": 2.4, "place": "home"},
        "objects": [
            {"id": "sofa", "query": "sofa", "alt_queries": ["couch"], "layout": {"against_wall": "south"}},
            {"id": "tv_stand", "query": "tv stand", "alt_queries": ["media console", "tv cabinet"], "layout": {"against_wall": "north"}},
            {"id": "armchair", "query": "armchair", "alt_queries": ["lounge chair"], "layout": {"against_wall": "west"}},
            {"id": "side_table", "query": "side table", "alt_queries": ["end table"], "layout": {"next_to": "sofa"}},
            {"id": "shelf", "query": "bookshelf", "alt_queries": ["bookcase", "display shelf"], "layout": {"against_wall": "east"}},
        ],
        "picks": {"sofa": "sm_sofa_soft_b02_01.usd", "tv_stand": "sm_cabinet_file_c02_01.usd",
                  "armchair": "sm_sofa_soft_b01_01.usd", "side_table": "sm_table_bedside_b01_01.usd",
                  "shelf": "sm_cabinet_file_a02_01.usd"},
        "plants": {"shelf": "sm_rack_composition_l03_01.usd", "armchair": "sm_chair_hospital_b01_01.usd",
                   "side_table": "sm_facilitySystem_beverageStation_breakArea_a01_01.usd"},
    },
}


def pick(d, oid, name):
    st = service.status(d)
    o = next(o for o in st["objects"] if o["id"] == oid)
    i = next((i for i, c in enumerate(o["candidates"]) if c["name"] == name), None)
    if i is None:  # not among the scene's candidates: search wider, as check_taste does
        so = service.load(d).obj(oid)
        c = next((c for c in service._find_candidates(so, 60) if c["name"] == name), None)
        if c is None:
            print(f"  ({oid}: {name} not found for {so.query!r})")
            return False
        s = service.load(d)
        p = service._placement(s, oid)
        p.candidates.append(c)
        s.save(service._resolved_path(d))
        i = len(p.candidates) - 1
    service.select_asset(d, oid, i)
    return True


def show(tag, r):
    print(f"  {tag}: median fit {r.get('median_fit')}, fit {r.get('fit')}")
    for o in r.get("outliers") or []:
        print(f"    -> {o['object']} {o['asset']} fit {o['fit']} looks {o.get('looks')} vs {o.get('others_look')}; "
              f"alternatives {[(a['index'], a['name'], a['fit']) for a in o['alternatives']]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--pool", type=int, default=taste.POOL)
    ap.add_argument("--old", action="store_true")
    a = ap.parse_args()
    out = a.out
    taste.POOL = a.pool
    if a.old:
        service._taste_pools = lambda *x, **k: None
    e = taste.SiglipEmbedder(taste.MODEL)
    res = {}
    for name, r in ROOMS.items():
        d = out / name
        service.create_scene({"name": name, "room": r["room"], "objects": r["objects"]}, d, preferences=False)
        for oid, n in r["picks"].items():
            pick(d, oid, n)
        print(name)
        base = service.check_taste(d, embedder=e)["taste"]
        show("coherent", base)
        res[name] = {"coherent": [o["object"] for o in base["outliers"]]}
        for oid, n in r["plants"].items():
            if not pick(d, oid, n):
                continue
            t = service.check_taste(d, embedder=e)["taste"]
            show(f"planted {oid}={n}", t)
            res[name][oid] = [o["object"] for o in t["outliers"]]
            if t["outliers"] and t["outliers"][0]["object"] == oid:
                res[name][oid + "_alternatives"] = [x["name"] for x in t["outliers"][0]["alternatives"]]
            pick(d, oid, r["picks"][oid])
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
