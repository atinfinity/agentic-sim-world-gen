"""The taste check on real scenes, many sets: the scenes rooms.py made (each object's 6 scene
candidates and its wider pool, taste_pool.json), with picks sampled from hand-graded lists ("ok" = goes with
a light / white room, "off" = clearly does not; graded from the pool thumbnails). Coherent sets take one "ok"
pick per object; planted sets swap one object for an "off" pick. No model and no network: the embeddings
come from the cache check_taste filled (run rooms.py first).

    PYTHONPATH=src python scripts/taste_eval/scenes.py /tmp/taste_rooms [-n 40]

Rows: the residual baseline is the scene's candidates (before) or the wider pool (after), and margin / gain.
Detected = the planted object is the first flag; false flags = a coherent set gets any flag; 1st alternative
ok = the first suggestion for a detected plant is on the object's "ok" list.
"""
import argparse
import json
import random
from pathlib import Path

from agentic_sim_world_gen import taste
from agentic_sim_world_gen.scene.spec import ResolvedScene

GRADES = {
    "study": {
        "desk": {"ok": ["sm_desk_a01_01.usd"], "off": ["sm_facilitySystem_beverageStation_breakArea_a01_01.usd"]},
        "chair": {"ok": ["sm_chair_hospital_b03_01.usd", "sm_chair_surgical_b01_01.usd"],
                  "off": ["sm_chair_office_fabric_manager_a01_01.usd", "sm_chair_hospital_b01_01.usd",
                          "sm_chair_hospital_b02_01.usd", "sm_chair_office_amf_black_a01_01.usd"]},
        "bookshelf": {"ok": ["sm_cabinet_file_a02_01.usd", "sm_cabinet_file_d02_01.usd"],
                      "off": ["sm_shelve_steel_heavyDuty_a06_01.usd", "sm_storage_shelf_basic_a01_01.usd",
                              "sm_rack_bulk_storage_a03_01.usd", "sm_rack_composition_l05_01.usd"]},
        "cabinet": {"ok": ["sm_cabinet_file_d04_01.usd", "sm_cabinet_file_d01_01.usd", "sm_cabinet_file_a01_01.usd",
                           "sm_cabinet_file_c03_01.usd", "sm_cabinet_file_b03_01.usd"],
                    "off": ["sm_equipment_rolling_tool_cabinet_a01_01.usd",
                            "sm_equipment_rolling_tool_cabinet_heavyDuty_b01_01.usd", "sm_equipment_tools_trolley_a01_01.usd"]},
        "trash": {"ok": ["pedal_trash_can.usda", "sm_bin_waste_a04_01.usd", "sm_bin_waste_a01_01.usd", "sm_bin_waste_b01_01.usd"],
                  "off": ["sm_container_recycle_green_b02_01.usd", "sm_trashCan_domed_blue_b01_01.usd",
                          "sm_wasteManagement_oilyWasteCan_fireSafe_a01_01.usd"]},
    },
    "living": {
        "sofa": {"ok": ["sm_sofa_soft_b02_01.usd", "sm_sofa_soft_b03_01.usd"], "off": []},
        "tv_stand": {"ok": ["sm_cabinet_file_c02_01.usd", "sm_cabinet_file_c01_01.usd", "sm_cabinet_file_a01_01.usd",
                            "sm_cabinet_file_d03_01.usd", "sm_cabinet_file_c03_01.usd", "sm_cabinet_file_d01_01.usd"], "off": []},
        "armchair": {"ok": ["sm_sofa_soft_b01_01.usd"],
                     "off": ["sm_chair_hospital_b01_01.usd", "sm_chair_hospital_b02_01.usd", "sm_chair_surgical_a01_01.usd"]},
        "side_table": {"ok": ["sm_table_bedside_b01_01.usd"], "off": ["sm_facilitySystem_beverageStation_breakArea_a01_01.usd"]},
        "shelf": {"ok": ["sm_cabinet_file_a02_01.usd", "sm_cabinet_file_d02_01.usd"],
                  "off": ["sm_rack_composition_l03_01.usd", "sm_rack_f04_01.usd", "sm_shelve_steel_heavyDuty_a06_01.usd",
                          "sm_rack_large_empty_a02_01.usd"]},
    },
}
ALSO_OK = {  # suggestions that also go with the light room (prefixes), for "1st alternative ok"
    "study": {
        "chair": ["sm_chair_hospital_b03", "sm_chair_surgical_b01"],
        "bookshelf": ["sm_cabinet_file_", "sm_cabinet_for_drugs_"],
        "cabinet": ["sm_cabinet_file_", "sm_cabinet_for_drugs_", "sm_fixture_cabinet_a01", "chest_of_drawers", "sm_table_bedside"],
        "trash": ["pedal_trash", "sm_bin_waste_", "D00920", "sm_trashCan_office_beige", "sm_furniture_trashBin_lid"],
    },
    "living": {
        "sofa": ["sm_sofa_soft_"],
        "tv_stand": ["sm_cabinet_file_", "sm_cabinet_for_drugs_", "sm_fixture_cabinet_a01", "sm_fixture_cabinet_d01"],
        "armchair": ["sm_sofa_soft_", "sm_chair_hospital_b03"],
        "side_table": ["sm_table_bedside", "sm_table_metal_stainless"],
        "shelf": ["sm_cabinet_file_", "sm_cabinet_for_drugs_", "sm_fixture_cabinet_d01"],
    },
}


def ok(name, grade):
    """A suggestion goes with the room: on the "ok" list or matching one of its "also_ok" prefixes."""
    return name in grade["ok"] or any(name.startswith(p) for p in grade.get("also_ok", []))


def load_room(d: Path):
    s = ResolvedScene.load(d / "scene.resolved.json")
    pools = {k: v["candidates"] for k, v in json.loads((d / taste.POOL_FILE).read_text()).items()}
    return s, pools


def items(s, pools, pick, use_pool):
    """The set as report() sees it: the scene's candidates (+ the pick, as select_asset(uri=) adds it) and the pool."""
    out = []
    for p in s.placements:
        oid = p.object_id
        cands = list(p.candidates)
        names = [c["name"] for c in cands]
        if pick[oid] not in names:
            cands.append(next(c for c in pools[oid] if c["name"] == pick[oid]))
            names.append(pick[oid])
        it = taste.Item(oid, oid, names.index(pick[oid]),
                        [(i, c["name"], c.get("thumbnail_url"), bool(c["match"]["weak"])) for i, c in enumerate(cands)])
        if use_pool:
            it.pool = [(c["uri"], c["name"], c.get("thumbnail_url"), bool(c["match"]["weak"])) for c in pools[oid]]
        out.append(it)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rooms", type=Path)
    ap.add_argument("-n", type=int, default=40, help="sets per room")
    ap.add_argument("--defaults", action="store_true", help="only the defaults (before / after)")
    a = ap.parse_args()
    cache = taste.Cache(taste.MODEL)
    rooms = {}
    for name in GRADES:
        s, pools = load_room(a.rooms / name)
        urls = {c.get("thumbnail_url") for p in s.placements for c in p.candidates} | {
            c.get("thumbnail_url") for cs in pools.values() for c in cs}
        vecs = {u: v for u in urls if u and (v := cache.get(u)) is not None}
        rooms[name] = (s, pools, vecs)
    ap_grid = [(True, m, g, mk) for mk in (1, 6, 8) for g in (0.15, 0.2, 0.3, 0.4) for m in (0.05, 0.10, 0.15)]
    configs = [(False, taste.MARGIN, taste.FLAG_GAIN, 1), (True, taste.MARGIN, taste.FLAG_GAIN, taste.MIN_KIND)]
    configs += [] if a.defaults else ap_grid
    print(f"{a.n} coherent + {a.n} planted sets per room ({', '.join(GRADES)})")
    print("| baseline | margin | gain | min kind | detected | other objects flagged | false flags (coherent) | 1st alternative ok |")
    print("|---|---|---|---|---|---|---|---|")
    for use_pool, margin, gain, min_kind in configs:
        rng = random.Random(0)
        det = other = fp = alt_ok = alt_n = k = 0
        for name, g in GRADES.items():
            s, pools, vecs = rooms[name]
            for _ in range(a.n):
                pick = {o: rng.choice(v["ok"]) for o, v in g.items()}
                bad = rng.choice([o for o, v in g.items() if v["off"]])
                planted = dict(pick, **{bad: rng.choice(g[bad]["off"])})
                k += 1
                r0 = taste.analyze(items(s, pools, pick, use_pool), vecs, margin=margin, gain=gain, min_kind=min_kind)
                fp += bool(r0["outliers"])
                r1 = taste.analyze(items(s, pools, planted, use_pool), vecs, margin=margin, gain=gain, min_kind=min_kind)
                flagged = [o["object"] for o in r1["outliers"]]
                hit = bool(flagged) and flagged[0] == bad
                det += hit
                other += any(x != bad for x in flagged)
                if hit and r1["outliers"][0]["alternatives"]:
                    alt_n += 1
                    alt_ok += ok(r1["outliers"][0]["alternatives"][0]["name"], {**g[bad], "also_ok": ALSO_OK[name].get(bad, [])})
        print(f"| {'pool' if use_pool else 'scene candidates'} | {margin:.2f} | {gain:.2f} | {min_kind} | {det / k:.2f} | "
              f"{other / k:.2f} | {fp / k:.2f} | {alt_ok / max(1, alt_n):.2f} |")


if __name__ == "__main__":
    main()
