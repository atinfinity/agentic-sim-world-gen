"""Assets made for another line of work than the room's.

A living room asked for a bed, a heater and a stool got a hospital bed, a car radiator and a surgical stool:
the catalog has more professional equipment than home furniture, and the head noun alone does not tell them
apart. Each domain is a list of words that, when the asset's class / caption / tags name them, say the asset
belongs to that line of work. Room.place lists the domains that do not belong there; such assets go after the
others (they are not dropped: when nothing else exists they are still the nearest substitute).
A domain word in the query itself ("hospital bed", "pallet rack") lets that domain in.
"""

from __future__ import annotations

import re

from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.scene.spec import STORE_PLACES

DOMAINS: dict[str, tuple[str, ...]] = {
    "medical": ("medical", "hospital", "patient", "surgical", "surgery", "clinic", "clinical", "dental",
                "radiography", "radiology", "x-ray", "xray", "operating light", "iv pole", "iv poles", "infusion",
                "syringe", "orthopedic", "germicidal", "uvc", "uv-c", "disinfection robot", "sterilization robot", "stretcher", "gurney", "anesthesia", "drainage bag"),
    "automotive": ("automotive", "car", "vehicle", "engine", "automobile", "bumper", "coolant", "antifreeze"),
    "industrial": ("industrial", "warehouse", "pallet", "palletized", "forklift", "factory", "heavy duty",
                   "ibc", "intermediate bulk container"),
    "lab": ("laboratory", "lab", "fume hood", "beaker", "test tube", "centrifuge", "pipette",
            # rigs: SimReady's "articulation test bench" was the first "bench" for a shoe store's try-on seat
            "test bench", "test benches", "test rig", "test stand", "testing machine", "test equipment"),
    "airport": ("airport", "security checkpoint", "gate seating"),
    "retail": ("retail", "store fixture", "shop fixture", "gondola", "point of sale", "pallet display", "drugstore",
               "supermarket", "convenience store", "店舗什器"),
    "agriculture": ("plant factory", "vertical farm", "indoor farm", "hydroponic", "hydroponics", "growing rack", "grow rack",
                    "cultivation", "greenhouse", "agricultural", "agriculture"),
}

_SHOP = ("medical", "automotive", "industrial", "lab", "airport", "agriculture")
# Room.place -> domains whose assets do not belong there (places not listed: none)
FOREIGN: dict[str, tuple[str, ...]] = {
    "home": ("medical", "automotive", "industrial", "lab", "airport", "retail", "agriculture"),
    "office": ("medical", "automotive", "industrial", "lab", "airport", "agriculture"),  # vending machines are retail
    "school": ("medical", "automotive", "industrial", "airport", "retail"),
    "cafe": _SHOP,
    "restaurant": _SHOP,
    "shop": _SHOP,
    **{p: _SHOP for p in STORE_PLACES},
    "drugstore": ("automotive", "industrial", "lab", "airport", "agriculture"),
    "home_center": ("medical", "lab", "airport"),
    # plants and growing gear belong in these shops
    "garden_center": ("medical", "lab", "airport"),
    "florist": ("medical", "automotive", "industrial", "lab", "airport"),
    "greengrocer": ("medical", "automotive", "industrial", "lab", "airport"),
    # tires, pumps and tools are a bicycle shop's own goods
    "bicycle_shop": ("medical", "industrial", "lab", "airport", "agriculture"),
    "datacenter": ("medical", "automotive", "airport", "retail"),
    "factory": ("medical", "airport", "retail"),
    "plant_factory": ("medical", "automotive", "airport", "retail"),
    "warehouse": ("medical", "airport"),
    "lab": ("automotive", "airport", "retail"),
    "hospital": ("automotive", "industrial", "airport", "retail"),
    "station": ("medical", "automotive", "industrial", "lab"),
    "public": ("medical", "automotive", "industrial", "lab"),
}

TOY_VEHICLE = re.compile(r"(?<![a-z0-9])(?:toy|mini|rc|r/c|die-?cast|model|ride-on|remote control)"
                         r"\s+(?:car|cars|vehicle|vehicles|truck|trucks)(?![a-z0-9])")
_PATTERNS = {d: re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(w) for w in ws) + r")(?![a-z0-9])")
             for d, ws in DOMAINS.items()}


def domains_of(text: str) -> set[str]:
    """The domains whose words appear in the text (as whole words)."""
    t = text.lower().replace("_", " ")
    t = re.sub(r"plant factor(?:y|ies)", "vertical farm", t)  # a farm, not an industrial factory
    t = TOY_VEHICLE.sub("toy", t)  # a boxed RC car or a mini car is a toy, not car parts
    return {d for d, p in _PATTERNS.items() if p.search(t)}


def off_place(rec: AssetRecord, place: str | None, queries: list[str]) -> str | None:
    """The first domain of the asset that does not belong to the place, or None. Read from the asset's class,
    caption and tags (not the file name: SimReady's sm_chair_hospital_b01 is a plain task chair)."""
    foreign = FOREIGN.get(place or "")
    if not foreign:
        return None
    have = domains_of(" | ".join([rec.semantic_class or "", rec.caption or "", *rec.tags]))
    if "retail" in have and "retail" not in foreign:
        # a shop fixture made for this kind of place: "pallet display" on a diaper stack table is not warehouse ware
        have -= {"industrial", "agriculture"}
    asked = domains_of(" | ".join(queries))
    for d in foreign:
        if d in have and d not in asked:
            return d
    return None
