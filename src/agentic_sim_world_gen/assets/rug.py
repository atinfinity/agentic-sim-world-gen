"""A plain rug no asset source has: a thin slab with a CC0 PBR carpet / fabric texture, built in compose.

No catalog has a floor rug: the Isaac index and Poly Haven have none, SimReady Central's "rug" finds cloth
placemats, tea towels and stone tiles, and "carpet" a carpet extractor (the agent dropped the rug). Like the
plain chalkboard (agentic_sim_world_gen.assets.generic), a search whose main query names a rug / carpet / floor mat gets
synthetic candidates of source "generic":

    generic:rug?w=<long side>&d=<short side>&t=<thickness>[&mat=<PBR id>][&shape=round]

one per material: the CC0 PBR carpets / fabrics (agentic_sim_world_gen.pbr, ambientCG / Poly Haven) that best match the query's
words ("beige wool rug" -> Carpet016), at most CANDIDATES; a plain colored one when there is none (AGENTIC_SIM_WORLD_GEN_PBR=0).
The size comes from min_size / max_size (footprint long side, short side, thickness), "round" / "circular" in the
query makes it round (an ellipse in the footprint). The color is ObjectSpec.tint as for any asset (multiplied with
the texture). compose writes the slab into the scene (agentic_sim_world_gen.usd.rug) and references it.

Asset frame: Z-up, meters; the long side along X, the bottom at z = 0 (front +X, which means nothing for a rug).
Physics: the slab has no collider; a 1 mm static box at the floor stands for it (agentic_sim_world_gen.usd.rug), so furniture
stands "in" the pile without being pushed up and robots drive over it. layout.validate.flat lets furniture overlap it.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlencode

from agentic_sim_world_gen.assets.record import AssetRecord

KIND = "rug"
SIZE = (2.0, 1.4)  # default footprint (long side, short side) [m]: a living-room area rug
THICK = 0.012  # default thickness [m]
THICK_RANGE = (0.004, 0.03)
CANDIDATES = 3  # materials offered as candidates
PLAIN_COLOR = "#b9a68a"  # a rug with no texture: light greige
MIN_LONG = 0.6  # [m] a real candidate shorter than this is a placemat / cloth, not a floor rug
# the head noun of a query that asks for a floor rug ("area rug", "shag carpet", "door mat", "kilim")
RUGS = {"rug", "rugs", "carpet", "carpets", "kilim", "kilims", "dhurrie", "doormat", "doormats"}
MATS = {"mat", "mats"}
# the word right before "mat" that makes it no floor mat: on a table / desk / in a car, a kitchen tool, or thick
# (crash / landing / trampoline) -- unless "under" comes before it ("under desk mat" is a chair mat)
_NOT_MAT = {"table", "place", "mouse", "cutting", "desk", "laptop", "dish", "drying", "bar", "sink", "beer", "car",
            "baking", "silicone", "sushi", "bamboo", "crash", "landing", "trampoline", "tatami", "gym", "wrestling",
            "litter", "heating", "grill", "pastry", "dough", "rolling"}
_NOT_RUG = {"wall", "hanging", "tapestry"}  # right before rug / carpet: hung, not laid
_THING_AFTER = {"extractor", "cleaner", "cleaners", "shampooer", "tile", "tiles", "sample", "samples", "beetle",
                "knife", "tape", "cleaning"}  # "carpet extractor": a thing for carpets
_RUNNER_NOT = {"table", "bed", "dresser", "drawer"}  # "table runner" lies on a table
_ROUND = {"round", "circular", "circle", "oval"}
# default footprint (long, short side) and thickness by the word before the head noun; others: SIZE, THICK
_DEFAULTS = {"door": (0.8, 0.5, 0.012), "doormat": (0.8, 0.5, 0.012), "entrance": (0.8, 0.5, 0.012),
             "welcome": (0.8, 0.5, 0.012), "bath": (0.8, 0.5, 0.012), "bathroom": (0.8, 0.5, 0.012),
             "shower": (0.6, 0.6, 0.008), "kitchen": (1.8, 0.6, 0.008), "yoga": (1.83, 0.61, 0.006),
             "exercise": (1.83, 0.61, 0.006), "hallway": (2.4, 0.75, 0.01), "hall": (2.4, 0.75, 0.01),
             "runner": (2.4, 0.75, 0.01), "chair": (1.2, 0.9, 0.004), "office": (1.2, 0.9, 0.004),
             "anti": (0.9, 0.6, 0.015), "fatigue": (0.9, 0.6, 0.015)}
CATEGORIES = ("Carpet", "Fabric", "Textiles")


def _norm(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _head(t: list[str]) -> int | None:
    """Index of the head noun that makes `t` a floor rug, or None. The first rug word counts ("rug under coffee
    table", "dining table rug", "rug pad"); what decides is the word right before it, or a thing named after it."""
    for i, w in enumerate(t):
        before = t[i - 1] if i else None
        after = t[i + 1] if i + 1 < len(t) else None
        under = "under" in t[:i]
        if w in RUGS:
            if before in _NOT_RUG or after in _THING_AFTER:
                return None
            return i
        if w in MATS:
            if (before in _NOT_MAT and not under) or before in ("place", "table") or after in _THING_AFTER:
                return None
            return i
        if w == "runner":
            if before in _RUNNER_NOT or before is None and i + 1 == len(t):
                return None  # "table runner"; a bare "runner" may be one too
            return i
    return None


def kind_of(query: str | None) -> str | None:
    """'rug' when the main query names a floor rug / carpet / mat ("beige area rug", "shag carpet", "door mat",
    "dining table rug", "kitchen runner"); None for a placemat, a mouse mat, a sushi mat, a crash mat, a table runner,
    a carpet extractor."""
    t = _norm(query)
    if not t or "placemat" in t or "placemats" in t:
        return None
    return KIND if _head(t) is not None else None


def default_size(query: str | None) -> tuple[float, float, float]:
    """(long side, short side, thickness) of a rug of this kind when no size is given: a door / bath mat 0.8 x 0.5,
    a yoga mat 1.83 x 0.61, a hallway runner 2.4 x 0.75, else SIZE."""
    t = _norm(query)
    i = _head(t)
    if i is None:
        return (*SIZE, THICK)
    for w in [*reversed(t[:i]), t[i]]:
        if w in _DEFAULTS:
            return _DEFAULTS[w]
    return (*SIZE, THICK)


def round_asked(query: str | None) -> bool:
    return bool(_ROUND & set(_norm(query)))


def _clamp(v: float, lo: float | None, hi: float | None) -> float:
    if lo is not None and lo > 0 and v < lo:
        v = lo
    if hi is not None and hi > 0 and v > hi:
        v = hi
    return v


def rug_size(min_size=None, max_size=None, shape: str = "rect",
             default: tuple[float, float, float] | None = None) -> tuple[float, float, float]:
    """(long side, short side, thickness) from min_size / max_size (orientation-free like `fits`): the default
    (`default`, or 2.0 x 1.4 m; round: 1.6 m across) kept inside them; thickness 1.2 cm, clamped to 4 mm .. 3 cm
    (`thickness_note` says when it was)."""
    lo, hi = min_size or (None,) * 3, max_size or (None,) * 3
    w0, d0, t0 = default or (*SIZE, THICK)
    if shape == "round":
        w0 = d0 = 1.6 if default is None else max(w0, d0)
    w = _clamp(w0, lo[0], hi[0])
    d = _clamp(d0, lo[1], hi[1])
    if shape == "round" and (lo[1] is None or lo[1] <= 0) and (hi[1] is None or hi[1] <= 0):
        d = w  # a round rug: as wide as it is long unless the short side is given
    w, d = max(w, d), min(w, d)  # a short side asked longer than the default long side widens the rug
    t = max(THICK_RANGE[0], min(THICK_RANGE[1], _clamp(t0, lo[2], hi[2])))
    return round(w, 3), round(d, 3), round(t, 4)


def thickness_note(min_size=None, max_size=None) -> str | None:
    """Why the thickness is not the one asked for: outside THICK_RANGE (a thick mat is not built)."""
    lo, hi = (min_size or (None,) * 3)[2], (max_size or (None,) * 3)[2]
    if lo is not None and lo > THICK_RANGE[1]:
        return (f"thickness {lo * 100:g} cm asked, built {THICK_RANGE[1] * 100:g} cm (a generic rug is "
                f"{THICK_RANGE[0] * 100:g}-{THICK_RANGE[1] * 100:g} cm thick)")
    if hi is not None and 0 < hi < THICK_RANGE[0]:
        return f"thickness at most {hi * 1000:g} mm asked, built {THICK_RANGE[0] * 1000:g} mm"
    return None


def uri_for(w: float, d: float, t: float = THICK, mat: str | None = None, shape: str = "rect") -> str:
    q = {"w": f"{w:g}", "d": f"{d:g}", "t": f"{t:g}", **({"mat": mat} if mat else {}),
         **({"shape": shape} if shape != "rect" else {})}
    return f"generic:{KIND}?{urlencode(q)}"


def parse_query(query: str) -> dict[str, Any] | None:
    """{"kind": "rug", "w", "d", "t", "shape"[, "mat"]} of the part after "generic:rug?", or None."""
    q = {k: v[0] for k, v in parse_qs(query).items()}
    try:
        w, d, t = float(q.get("w", SIZE[0])), float(q.get("d", SIZE[1])), float(q.get("t", THICK))
    except ValueError:
        return None
    shape = q.get("shape", "rect")
    mat = q.get("mat")
    if not (0.1 <= d <= w <= 30 and THICK_RANGE[0] <= t <= THICK_RANGE[1]) or shape not in ("rect", "round"):
        return None
    if mat is not None and not re.fullmatch(r"(ambientCG|PolyHaven)/[A-Za-z0-9][A-Za-z0-9_\-]{0,99}", mat):
        return None
    return {"kind": KIND, "w": w, "d": d, "t": t, "shape": shape, **({"mat": mat} if mat else {})}


def materials_for(query: str | None, alt_queries: list[str] | None = None, n: int = CANDIDATES) -> list[dict[str, Any]]:
    """The CC0 PBR carpets / fabrics that best match the query (rug words read as carpet), best first; the plain
    carpets when nothing matches. Empty with AGENTIC_SIM_WORLD_GEN_PBR=0 or no index."""
    from agentic_sim_world_gen import materials, pbr

    recs = tuple(r for r in pbr.records() if r.get("category") in CATEGORIES)
    if not recs:
        return []
    qs = [q for q in [query, *(alt_queries or [])] if q]
    # the rug's own words are no material words: "area rug" / "door mat" -> carpet
    words = RUGS | MATS | {"runner", "area", "door", "floor"}
    carpet = [" ".join("carpet" if w in words else w for w in _norm(q)) for q in qs]
    found = materials.search(carpet[0] if carpet else "carpet", carpet[1:], recs=recs, limit=n, per_file=1)
    strong = [r for r in found if not r["match"]["weak"]]
    if strong:
        return strong[:n]
    return [r for r in recs if r["category"] == "Carpet"][:n]


def _credit(r: dict[str, Any]) -> dict[str, Any]:
    return {"title": r.get("title") or r["name"], "author": r.get("author") or r["library"], "url": r.get("url"),
            "license": r.get("license") or "CC0-1.0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "subject": "rug texture"}


def record(w: float, d: float, t: float, mat: dict[str, Any] | None = None, shape: str = "rect") -> AssetRecord:
    from agentic_sim_world_gen.assets.generic import AUTHOR, LICENSE, SOURCE

    uri = uri_for(w, d, t, mat["id"] if mat else None, shape)
    look = f"{mat['title'] or mat['name']} ({mat['id']}, CC0)" if mat else "plain color"
    what = "round rug" if shape == "round" else "rug"
    return AssetRecord(
        id=uri, source=SOURCE, uri=uri, name=f"generic_{shape}_rug_{w:g}x{d:g}" + (f"_{mat['name']}" if mat else ""),
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, t), semantic_class=KIND,
        caption=f"plain {what} {w:g} x {d:g} m, {t * 100:g} cm thick, {look}: a thin slab with a beveled edge "
                "(generic geometry built by agentic-sim-world-gen)",
        tags=[KIND, "carpet", "floor mat", "generic", *(mat.get("keywords") or [])[:8]] if mat else [KIND, "generic"],
        license=LICENSE, author=AUTHOR, front_deg=0.0, credits=[_credit(mat)] if mat else [],
        thumbnail_url=mat.get("thumb") if mat else None)


def candidates(query: str | None, alt_queries: list[str] | None = None, min_size=None,
               max_size=None) -> list[dict[str, Any]]:
    """The rug as search candidates (record dicts with their match), one per material, best material first."""
    from agentic_sim_world_gen.scene.spec import record_to_dict

    shape = "round" if round_asked(query) else "rect"
    w, d, t = rug_size(min_size, max_size, shape, default_size(query))
    note = thickness_note(min_size, max_size)
    out = []
    for i, m in enumerate(materials_for(query, alt_queries) or [None]):
        c = record_to_dict(record(w, d, t, m, shape))
        c["match"] = {"score": round(3.0 - 0.01 * i, 2), "weak": False, "generic": True,
                      "matched": ["generic rug: a thin slab built in compose, "
                                  + (f"texture {m['id']}" if m else "plain color")]}
        if note:
            c["match"]["note"] = note
        out.append(c)
    return out


def preview_color(p: dict[str, Any], tint: str | None = None) -> str:
    """Fill color of the rug on layout.svg: the tint, else the material's (cached mean color, or a color word of its
    key words), else greige."""
    if tint:
        return tint
    if p.get("mat"):
        from agentic_sim_world_gen import pbr

        r = pbr.by_id(p["mat"]) or {}
        try:
            m = pbr.cached(r) if r else None
        except pbr.FetchError:
            m = None
        if m and m.get("color"):
            return m["color"]
        for w in r.get("keywords") or []:
            if w in _WORD_COLOR:
                return _WORD_COLOR[w]
    return PLAIN_COLOR


_WORD_COLOR = {"beige": "#cdbb9c", "white": "#e6e2da", "grey": "#9a9a98", "gray": "#9a9a98", "dark": "#4a4a4a",
               "black": "#2e2e2e", "blue": "#4f6a8f", "red": "#9c3b36", "green": "#5d7a4e", "yellow": "#d6bf5a",
               "brown": "#7a5a40", "orange": "#c87a3c", "pink": "#d9a0a8", "purple": "#7a5a8a"}
