"""Draw the sample pictures of the looks once: a standard room per place group, each mood applied with its
furniture colors, checked in Isaac Sim (headless) and the check view saved as
src/agentic_sim_world_gen/data/moods/<group>_<mood>.jpg (640 px wide).

Run on the Isaac Sim machine (ISAACSIM_PYTHON or ISAACSIM_PATH set), from the repo:
    uv run python scripts/make_mood_images.py [--groups home,office] [--out /tmp/moods]
The bundled pictures were drawn with AGENTIC_SIM_WORLD_GEN_SOURCES=simready-central,polyhaven and
AGENTIC_SIM_WORLD_GEN_MDL=0 (public assets only; the shop group gets substitutes such as plain shelves).
A shop asset folder gives closer shop fixtures: AGENTIC_SIM_WORLD_GEN_SOURCES=+local:<dir>
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene import moods as md

ROOT = Path(__file__).resolve().parent.parent

ROOMS = {
    "home": {"room": {"width": 3.64, "depth": 3.64, "height": 2.4, "place": "home", "lighting": "home", "ceiling": True,
                      "openings": [{"kind": "window", "wall": "S", "offset": 1.8, "width": 1.6}]},
             "objects": [  # the check view looks from the south-west: keep the west and south walls clear
                 {"id": "sofa", "query": "sofa", "layout": {"against_wall": "north", "along": 0.4}},
                 {"id": "tv_stand", "query": "tv stand", "alt_queries": ["media console", "sideboard", "low cabinet"],
                  "min_size": [0.7, 0.25, 0.3], "max_size": [2.0, 0.6, 0.9], "keep": True,
                  "layout": {"against_wall": "east", "along": 0.45}},
                 {"id": "tv", "query": "television", "alt_queries": ["tv", "flat screen tv"], "keep": True,
                  "min_size": [0.8, 0.05, 0.5], "max_size": [1.5, 0.4, 1.0],
                  "layout": {"on": "tv_stand", "facing": "west"}},
                 {"id": "table", "query": "coffee table", "layout": {"in_front_of": "sofa", "gap": 0.4}},
                 {"id": "shelf", "query": "bookshelf", "layout": {"against_wall": "north", "along": 0.88}},
                 {"id": "plant", "query": "potted plant", "layout": {"next_to": "sofa", "side": "right"}}]},
    "office": {"room": {"width": 5.0, "depth": 4.0, "height": 2.7, "place": "office", "lighting": "office", "ceiling": True},
               "objects": [
                   {"id": "desk_a", "query": "office desk", "layout": {"against_wall": "north", "along": 0.3}},
                   {"id": "desk_b", "query": "office desk", "layout": {"against_wall": "north", "along": 0.7}},
                   {"id": "chair_a", "query": "office chair", "layout": {"in_front_of": "desk_a"}},
                   {"id": "chair_b", "query": "office chair", "layout": {"in_front_of": "desk_b"}},
                   {"id": "cabinet", "query": "storage cabinet", "layout": {"against_wall": "east", "along": 0.75}},
                   {"id": "shelf", "query": "bookshelf", "layout": {"against_wall": "east", "along": 0.3}}]},
    "shop": {"room": {"width": 6.0, "depth": 5.0, "height": 2.7, "place": "shop", "lighting": "office", "ceiling": True},
             "objects": [
                 # the store fixtures keep their asset: "white checkout counter" found a kitchen counter with a sink
                 #.
                 {"id": "shelf_a", "query": "gondola shelf", "alt_queries": ["store shelf"], "keep": True,
                  "layout": {"against_wall": "east", "along": 0.3}},
                 {"id": "shelf_b", "query": "gondola shelf", "alt_queries": ["store shelf"], "keep": True,
                  "layout": {"against_wall": "east", "along": 0.7}},
                 {"id": "counter", "query": "checkout counter", "alt_queries": ["cash register counter"], "keep": True,
                  "min_size": [1.0, 0.5, 0.8], "max_size": [2.4, 0.9, 1.2],
                  "layout": {"against_wall": "north", "along": 0.35}},
                 {"id": "table", "query": "display table", "layout": {"center": True}}]},
    "cafe": {"room": {"width": 5.0, "depth": 4.0, "height": 2.7, "place": "cafe", "lighting": "home", "ceiling": True},
             "objects": [
                 {"id": "counter", "query": "counter", "layout": {"against_wall": "north"}},
                 {"id": "table", "query": "dining table", "layout": {"center": True}},
                 {"id": "chair_a", "query": "dining chair", "layout": {"next_to": "table", "side": "left", "facing": "table"}},
                 {"id": "chair_b", "query": "dining chair", "layout": {"next_to": "table", "side": "right", "facing": "table"}},
                 {"id": "shelf", "query": "shelf", "layout": {"against_wall": "east"}},
                 {"id": "plant", "query": "potted plant", "layout": {"against_wall": "west"}}]},
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", default=",".join(ROOMS))
    ap.add_argument("--out", default=str(ROOT / "out" / "moods"))
    ap.add_argument("--images", default=str(md.IMAGES))
    args = ap.parse_args()
    out, images = Path(args.out), Path(args.images)
    images.mkdir(parents=True, exist_ok=True)
    log = {}
    for g in args.groups.split(","):
        place = ROOMS[g]["room"]["place"]
        for m in md.for_place(place):
            d = out / f"{g}_{m.name}"
            if d.exists():
                shutil.rmtree(d)
            # "keep": no color swap (the search for "white tv cabinet" found a lamp, which fell over)
            keep = {o["id"] for o in ROOMS[g]["objects"] if o.get("keep")}
            objects = [{k: v for k, v in o.items() if k != "keep"} for o in ROOMS[g]["objects"]]
            service.create_scene({"name": f"{g}_{m.name}", **ROOMS[g], "objects": objects}, d)
            st = service.apply_mood(d, m.name)
            swaps = {}
            for f in st.get("mood_furniture", []):
                if f["object"] in keep:
                    continue
                r = service.research_object(d, f["object"], f["try"], alt_queries=[f["try"]])
                sel = next(o for o in r["objects"] if o["id"] == f["object"])
                swaps[f["object"]] = {"try": f["try"], "selected": sel.get("selected"), "line": sel.get("asset")}
            service.compose_scene(d)
            r = service.check_in_isaac(d, steps=60, orbit=0)
            shot = d / "check.png"
            if not shot.exists():
                print(g, m.name, "no screenshot", r.get("error"))
                continue
            im = Image.open(shot).convert("RGB")
            im.thumbnail((640, 640))
            dst = images / f"{g}_{m.name}.jpg"
            im.save(dst, quality=82)
            log[f"{g}_{m.name}"] = {"swaps": swaps, "issues": len(r.get("issues", []))}
            print(g, m.name, "->", dst, json.dumps(swaps, ensure_ascii=False))
    (out / "summary.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
