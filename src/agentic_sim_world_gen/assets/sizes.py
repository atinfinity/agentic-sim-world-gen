"""Typical sizes per kind of object, used when the agent gave no min_size / max_size.

A "dining table" search listed packing workbenches with overhead shelves, a "cardboard box" search a pasta box
and a "forklift" search a hand-pushed platform: the head noun matched, the size did not. TYPICAL holds the usual
range of each kind as (footprint long side, short side, height) in meters, the same order as min_size / max_size
and `fits()` (so it does not depend on the asset's orientation). The skill's size table (SKILL.md "Typical sizes")
lists the same values for the agent to pass as min/max_size; tests/test_sizes.py checks the two agree.

Without explicit limits the range is widened (LOW x min, HIGH x max): real variants stay inside (a 4-seat sofa,
a 100" TV, a 2 m fridge are 25 % past the typical max), while the wrong things are 2x or more off. SHAPE adds a
ratio check where the size alone does not say it (a floor lamp is tall and thin, a rug is flat). Assets outside
go after the others (not dropped: when nothing else exists they are still the nearest substitute).
"""

from __future__ import annotations

from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.assets.rerank import noun_last, words

Vec3 = tuple[float, float, float]

LOW, HIGH = 0.6, 1.5  # widening of the typical range when the agent gave no limits

# kind (head noun phrase, singular, noun last) -> (min, max) as (long, short, height) in m
TYPICAL: dict[str, tuple[Vec3, Vec3]] = {
    # seating
    "office chair": ((0.45, 0.45, 0.8), (0.8, 0.8, 1.3)),
    "dining chair": ((0.35, 0.35, 0.75), (0.6, 0.6, 1.1)),
    "chair": ((0.35, 0.35, 0.7), (0.8, 0.8, 1.3)),
    "armchair": ((0.6, 0.6, 0.7), (1.0, 1.0, 1.1)),
    "stool": ((0.3, 0.3, 0.4), (0.6, 0.6, 0.85)),
    "sofa": ((1.4, 0.7, 0.6), (2.4, 1.1, 1.0)),
    "couch": ((1.4, 0.7, 0.6), (2.4, 1.1, 1.0)),
    "loveseat": ((1.1, 0.7, 0.6), (1.7, 1.0, 1.0)),
    "bench": ((0.9, 0.3, 0.4), (2.4, 0.8, 0.9)),
    "wheelchair": ((0.6, 0.55, 0.8), (1.2, 0.75, 1.1)),
    # tables and desks
    "office desk": ((1.0, 0.5, 0.65), (1.9, 1.0, 0.85)),
    "desk": ((0.8, 0.45, 0.65), (2.0, 1.0, 0.85)),
    "dining table": ((1.1, 0.7, 0.65), (1.8, 1.0, 0.8)),
    "kitchen table": ((1.1, 0.7, 0.65), (1.8, 1.0, 0.8)),
    "conference table": ((1.8, 0.9, 0.7), (5.0, 1.6, 0.8)),
    "meeting table": ((1.8, 0.9, 0.7), (5.0, 1.6, 0.8)),
    "coffee table": ((0.6, 0.4, 0.3), (1.4, 0.8, 0.5)),
    "side table": ((0.3, 0.3, 0.4), (0.6, 0.6, 0.75)),
    "bedside table": ((0.3, 0.3, 0.4), (0.6, 0.6, 0.75)),
    "nightstand": ((0.3, 0.3, 0.4), (0.6, 0.6, 0.75)),
    "workbench": ((1.2, 0.6, 0.75), (2.4, 1.0, 1.6)),
    # storage
    "bookshelf": ((0.6, 0.25, 1.2), (1.2, 0.5, 2.2)),
    "bookcase": ((0.6, 0.25, 1.2), (1.2, 0.5, 2.2)),
    "filing cabinet": ((0.4, 0.35, 0.6), (1.0, 0.7, 1.5)),
    "locker": ((0.3, 0.3, 1.2), (1.5, 0.6, 2.1)),
    "wardrobe": ((0.8, 0.5, 1.7), (2.0, 0.65, 2.4)),
    # beds
    "bed": ((1.9, 0.9, 0.3), (2.2, 1.6, 1.2)),
    # appliances and electronics
    "tv": ((0.8, 0.05, 0.5), (1.8, 0.4, 1.1)),
    "television": ((0.8, 0.05, 0.5), (1.8, 0.4, 1.1)),
    "refrigerator": ((0.55, 0.55, 1.2), (0.9, 0.8, 2.0)),
    "fridge": ((0.55, 0.55, 1.2), (0.9, 0.8, 2.0)),
    "microwave": ((0.4, 0.3, 0.22), (0.6, 0.5, 0.4)),
    "microwave oven": ((0.4, 0.3, 0.22), (0.6, 0.5, 0.4)),
    "washing machine": ((0.55, 0.55, 0.8), (0.7, 0.7, 1.1)),
    "vending machine": ((0.6, 0.6, 1.6), (1.3, 1.0, 2.0)),
    "computer monitor": ((0.3, 0.05, 0.25), (0.9, 0.35, 0.7)),
    "pc monitor": ((0.3, 0.05, 0.25), (0.9, 0.35, 0.7)),
    "laptop": ((0.28, 0.2, 0.015), (0.42, 0.3, 0.3)),
    "server rack": ((0.6, 0.6, 1.8), (1.3, 1.2, 2.5)),
    # lighting, decor
    "desk lamp": ((0.12, 0.12, 0.25), (0.5, 0.5, 0.75)),
    "table lamp": ((0.12, 0.12, 0.25), (0.5, 0.5, 0.75)),
    "floor lamp": ((0.25, 0.25, 1.2), (0.6, 0.6, 1.9)),
    "rug": ((0.6, 0.4, 0.003), (3.5, 3.0, 0.03)),
    "potted plant": ((0.1, 0.1, 0.15), (1.0, 1.0, 2.0)),
    "whiteboard": ((0.6, 0.03, 0.4), (2.4, 0.7, 2.0)),
    "mug": ((0.07, 0.06, 0.07), (0.14, 0.1, 0.13)),
    "trash can": ((0.2, 0.15, 0.25), (0.7, 0.7, 1.1)),
    "waste bin": ((0.2, 0.15, 0.25), (0.7, 0.7, 1.1)),
    "garbage can": ((0.2, 0.15, 0.25), (0.7, 0.7, 1.1)),
    "fire extinguisher": ((0.12, 0.08, 0.3), (0.35, 0.25, 0.9)),
    # warehouse, outdoor
    "warehouse rack": ((1.5, 0.8, 1.8), (3.0, 1.2, 6.0)),
    "pallet rack": ((1.5, 0.8, 1.8), (3.0, 1.2, 6.0)),
    "pallet": ((1.0, 0.8, 0.1), (1.3, 1.2, 0.2)),
    "cardboard box": ((0.25, 0.15, 0.08), (1.2, 1.0, 1.0)),
    "hand truck": ((0.4, 0.35, 1.0), (1.3, 0.6, 1.5)),
    "forklift": ((1.8, 0.8, 1.9), (4.0, 1.5, 3.0)),
    "traffic cone": ((0.2, 0.2, 0.3), (0.5, 0.5, 1.0)),
    "drum": ((0.3, 0.3, 0.4), (0.7, 0.7, 1.0)),
    "barrel": ((0.3, 0.3, 0.4), (0.7, 0.7, 1.0)),
    "car": ((3.5, 1.5, 1.2), (5.5, 2.1, 2.0)),
}

# kind -> {ratio: (lo, hi)}; ratios "height/long", "short/long" (None = no bound). Not widened: already loose.
SHAPE: dict[str, dict[str, tuple[float | None, float | None]]] = {
    "floor lamp": {"height/long": (2.5, None)},  # tall and thin
    "rug": {"height/long": (None, 0.05)},  # flat
    "tv": {"short/long": (None, 0.45)},  # a thin panel (with its stand)
    "television": {"short/long": (None, 0.45)},
    "fire extinguisher": {"height/long": (1.2, None)},  # stands upright (a suppression cylinder lies down)
}


def _norm(phrase: str) -> str:
    return " ".join(noun_last(words(phrase), join=True))


_TABLE = {_norm(k): k for k in TYPICAL}


def kind_of(query: str | None, alt_queries: list[str] | None = None) -> str | None:
    """The TYPICAL kind a query names: the longest known tail of its words ("white office desk" -> "office desk",
    "writing desk" -> "desk", "desk lamp" -> "desk lamp", never "desk"). The main query decides; the synonyms
    only when it names no known kind."""
    for q in [query, *(alt_queries or [])]:
        tw = noun_last(words(q), join=True)
        for k in range(len(tw), 0, -1):
            if (key := _TABLE.get(" ".join(tw[-k:]))) is not None:
                return key
    return None


def default_range(kind: str) -> tuple[Vec3, Vec3]:
    lo, hi = TYPICAL[kind]
    return (tuple(round(v * LOW, 4) for v in lo), tuple(round(v * HIGH, 4) for v in hi))  # type: ignore[return-value]


def off_size(rec: AssetRecord, kind: str | None) -> dict[str, str] | None:
    """Which dimensions (long, short, height) or shape ratios of the asset fall outside the widened typical range
    of the kind, e.g. {"height": "1.93 m over 1.2 m"}; None when it fits, the kind is unknown or the asset has
    no size."""
    if kind is None or kind not in TYPICAL:
        return None
    w, d, h = rec.size_m
    if min(w, d, h) <= 0:
        return None
    dims = {"long": max(w, d), "short": min(w, d), "height": h}
    lo, hi = default_range(kind)
    out = {}
    for (name, v), mn, mx in zip(dims.items(), lo, hi):
        if v > mx:
            out[name] = f"{v:.2f} m over {mx:g} m"
        elif v < mn:
            out[name] = f"{v:.2f} m under {mn:g} m"
    for ratio, (rlo, rhi) in SHAPE.get(kind, {}).items():
        a, b = ratio.split("/")
        r = dims[a] / dims[b]
        if rlo is not None and r < rlo:
            out[ratio] = f"{r:.2f} under {rlo:g}"
        elif rhi is not None and r > rhi:
            out[ratio] = f"{r:.2f} over {rhi:g}"
    return out or None
