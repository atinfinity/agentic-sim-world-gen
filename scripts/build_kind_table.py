"""Build the kind table (src/agentic_sim_world_gen/data/kinds.json.gz): kind -> assets per source that match it strongly.

    PYTHONPATH=src python scripts/build_kind_table.py              # fetch SimReady Central, write the table
    PYTHONPATH=src python scripts/build_kind_table.py --listing L  # reuse a saved listing (json.gz of search_hybrid)
    PYTHONPATH=src python scripts/build_kind_table.py --show "floor lamp" "bench"

SimReady Central has no listing API, but search_hybrid with a limit above the corpus size returns every asset
(3660 on 2026-11-01). It is fetched with a few unrelated queries and merged by path (the service is not
deterministic; the union guards against an asset missing from one answer). The Isaac index is read offline.
The listed kinds (KINDS below, the search-eval queries, the decor table) are kept in the table even when absent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import sys
import time
from pathlib import Path

import requests

from agentic_sim_world_gen.assets import kinds
from agentic_sim_world_gen.assets.providers import simready_central as sc
from agentic_sim_world_gen.assets.providers.isaac_index import IsaacIndexProvider

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 10000  # above the corpus size
LISTING_QUERIES = ("object", "furniture", "zebra")

# kinds people ask for (SKILL.md size table and examples, issue reports); absent ones stay in the table as {}
KINDS = (
    # home
    "sofa", "couch", "loveseat", "armchair", "bed", "single bed", "double bed", "bunk bed", "crib", "futon",
    "dining table", "dining chair", "coffee table", "side table", "kitchen table", "tv stand", "tv", "television",
    "bookshelf", "bookcase", "wardrobe", "dresser", "nightstand", "chest of drawers", "shoe rack", "coat rack",
    "floor lamp", "desk lamp", "table lamp", "pendant light", "ceiling light", "curtain", "rug", "carpet",
    "cushion", "throw pillow", "pillow", "blanket", "mirror", "clock", "wall clock", "picture frame", "painting",
    "vase", "potted plant", "candle", "remote control", "fruit bowl", "mug", "cup", "plate", "bowl", "kettle",
    "refrigerator", "microwave oven", "washing machine", "dishwasher", "oven", "stove", "rice cooker", "toaster",
    "sink", "toilet", "bathtub", "shower", "kotatsu", "tatami", "air conditioner", "heater", "fan", "vacuum cleaner",
    "piano", "guitar", "bicycle", "stroller", "umbrella stand", "trash can", "laundry basket",
    # office / school
    "office desk", "office chair", "conference table", "whiteboard", "projector", "projection screen", "filing cabinet",
    "locker", "computer monitor", "laptop", "keyboard", "printer", "copier", "phone", "desk organizer", "pen holder",
    "document tray", "partition", "sofa bench", "bench", "stool", "school desk", "blackboard", "podium",
    # shop / food
    "store shelf", "gondola shelf", "checkout counter", "cash register", "shopping cart", "shopping basket",
    "vending machine", "refrigerated display case", "freezer", "showcase", "mannequin", "clothes rack", "hanger",
    "bread", "bento", "drink can", "pet bottle", "coffee machine", "bar stool", "menu board", "digital signage",
    # warehouse / factory / datacenter
    "pallet", "warehouse rack", "pallet rack", "cardboard box", "hand truck", "forklift", "conveyor", "klt bin",
    "plastic storage bin", "drum barrel", "ibc tote", "workbench", "tool cabinet", "ladder", "fire extinguisher",
    "server rack", "air handler", "ups", "cable tray", "traffic cone", "safety barrier",
    # hospital / outdoor
    "hospital bed", "wheelchair", "iv pole", "car", "truck", "bus", "street lamp", "tree", "park bench",
    "bus stop", "mailbox", "fence", "bollard", "planter", "picnic table", "trash bin", "bicycle rack",
)


def listed_kinds() -> list[str]:
    from agentic_sim_world_gen.scene.decor import TABLE as DECOR

    out = list(KINDS)
    for q in json.loads((ROOT / "scripts/search_eval/data/queries.json").read_text()):
        out += [q["q"], *q["alt"]]
    for d in DECOR:
        out += [x for x in (d.query, *d.alt) if x]
    return out


def fetch_listing() -> dict:
    by_path: dict[str, dict] = {}
    s = requests.Session()
    for q in LISTING_QUERIES:
        body = sc.SimReadyCentralProvider().build_request(q, limit=LIMIT)
        r = s.post(sc.BASE_URL + sc.SEARCH_PATH, data=json.dumps(body),
                   headers={"Content-Type": "application/json", **sc.BACKEND_HEADER}, timeout=600)
        r.raise_for_status()
        hits = r.json().get("hits") or []
        n0 = len(by_path)
        for h in hits:
            if p := (h.get("source") or {}).get("path"):
                by_path.setdefault(p, h)
        print(f"  '{q}': {len(hits)} hits, {len(by_path) - n0} new", file=sys.stderr)
        time.sleep(2)
    return {"hits": list(by_path.values())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listing", type=Path, help="saved SimReady listing (json.gz); written here when fetched")
    ap.add_argument("--out", type=Path, default=kinds.DATA / "kinds.json.gz")
    ap.add_argument("--show", nargs="*", help="print the table's answer for these queries and exit")
    a = ap.parse_args()
    if a.show is not None:
        t = kinds.load(a.out)
        for q in a.show:
            print(q, "->", kinds.lookup(q, t), kinds.other_kinds(q, t))
        return 0
    if a.listing and a.listing.exists():
        data = json.load(gzip.open(a.listing, "rt"))
    else:
        data = fetch_listing()
        if a.listing:
            json.dump(data, gzip.open(a.listing, "wt"))
    simready = sc.parse_response(data)
    isaac = list(IsaacIndexProvider().records())
    built = dt.date.today().isoformat()
    t = kinds.build({"simready-central": simready, "isaac": isaac}, listed_kinds(), built,
                    notes={"simready-central": f"whole catalog via search_hybrid limit {LIMIT} on {built}",
                           "isaac": "src/agentic_sim_world_gen/data/isaac_6.1.jsonl.gz (also scored at run time)"})
    kinds.write(t, a.out)
    absent = [k for k in t["listed"] if not t["kinds"].get(k)]
    print(f"{a.out}: {len(simready)} SimReady + {len(isaac)} Isaac assets, {len(t['kinds'])} kinds, "
          f"{len(t['listed'])} listed, {len(absent)} listed absent, {a.out.stat().st_size / 1024:.0f} kB")
    print("absent:", ", ".join(absent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
