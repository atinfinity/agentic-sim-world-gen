"""Plain boards no asset source has: a chalkboard and a whiteboard built from boxes in compose.

The catalogs have no wall-mounted chalkboard or whiteboard (Poly Haven's standing_chalkboard_01 is an A-frame
sign on the floor; the Isaac index and SimReady Central have no whiteboard), so a search for one found cutting
boards and wall displays. Like the plain window and the sliding doors, the board is simple geometry, not
generated 3D: a flat surface (matte dark green / glossy white), a thin aluminium frame and a chalk / marker tray.

A search whose main query names a board kind (`kind_of`) gets one more candidate, a synthetic AssetRecord of
source "generic" and uri "generic:<kind>?w=<width>&h=<height>[&tray=0]", sized from the object's min_size /
max_size (`board_size`). It goes first when no candidate is the real thing (every match weak), or when the object
hangs on a wall (layout.wall_height) and the real ones stand on the floor (an A-frame sign); otherwise last.
Everything downstream treats it like any asset (solve, validate, preview, LICENSES): only compose turns the uri
into a USD file in the scene (agentic_sim_world_gen.usd.board.scene_ref, assets/generic/<stem>.usda) and references it.

Asset frame (like most assets): Z-up, meters, front +X; width along Y, the bottom of the bounding box at z = 0.

A rug is the other generic kind: agentic_sim_world_gen.assets.rug (words, size, CC0 PBR material, its candidates) and
agentic_sim_world_gen.usd.rug (the slab); kind_of / parse / candidates / hint here hand it over.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlencode

from agentic_sim_world_gen.assets.record import AssetRecord

SOURCE = "generic"
REF = "generic:"
LICENSE = "CC0-1.0"  # made by agentic-sim-world-gen from boxes: nothing to credit
AUTHOR = "agentic-sim-world-gen (generic geometry)"

# kind -> defaults: size (width, height) [m], surface color (sRGB), roughness, words that ask for it
KINDS: dict[str, dict[str, Any]] = {
    "chalkboard": {
        "size": (3.6, 1.2), "color": "#2f4a3a", "roughness": 0.9, "label": "黒板",
        "caption": "plain wall-mounted green chalkboard with a thin aluminium frame and a chalk tray (generic box "
                   "geometry built by agentic-sim-world-gen)",
        "words": ("chalkboard", "chalk board", "blackboard", "black board", "greenboard", "green board"),
    },
    "whiteboard": {
        "size": (1.8, 0.9), "color": "#f2f3f3", "roughness": 0.15, "label": "ホワイトボード",
        "caption": "plain wall-mounted white marker board with a thin aluminium frame and a marker tray (generic "
                   "box geometry built by agentic-sim-world-gen)",
        "words": ("whiteboard", "white board", "marker board", "dry erase board", "dryerase board",
                  "dry wipe board", "magnetic board"),
    },
}
DEPTH = 0.03  # frame depth: the board's thickness off the wall [m]
TRAY_D = 0.08  # the tray sticks out this far from the wall [m]
# words of a board that stands on the floor: not what an object hung on a wall asks for
_STANDING = {"standing", "freestanding", "easel", "stand", "mobile", "rolling", "wheeled", "aframe", "sandwich",
             "floor", "tripod", "portable"}


def _norm(text: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower().replace("-", "")))


def kind_of(query: str | None, alt_queries: list[str] | None = None) -> str | None:
    """'chalkboard' / 'whiteboard' when the main query names that kind ("green chalkboard", "classroom blackboard",
    "white board"); a bare "board" query takes the kind from its synonyms. None otherwise."""
    def one(q: str | None) -> str | None:
        t = _norm(q)
        for k, d in KINDS.items():
            if any(t == w or t.endswith(" " + w) for w in d["words"]):
                return k
        return None

    from agentic_sim_world_gen.assets import rug

    if rug.kind_of(query):
        return rug.KIND
    k = one(query)
    if k is None and _norm(query).endswith("board"):
        k = next((x for x in map(one, alt_queries or []) if x), None)
    return k


def _clamp(v: float, lo: float | None, hi: float | None) -> float:
    if lo is not None and lo > 0 and v < lo:
        v = lo
    if hi is not None and hi > 0 and v > hi:
        v = hi
    return v


def board_size(kind: str, min_size=None, max_size=None) -> tuple[float, float, bool]:
    """(width, height, tray) from the object's min_size / max_size (footprint long side, short side, height):
    the kind's default size kept inside them; no tray when max_size's short side is below the tray depth."""
    w, h = KINDS[kind]["size"]
    lo, hi = min_size or (None,) * 3, max_size or (None,) * 3
    w = _clamp(w, lo[0], hi[0])
    h = _clamp(h, lo[2], hi[2])
    tray = not (hi[1] is not None and hi[1] < TRAY_D)
    return round(w, 3), round(h, 3), tray


def uri_for(kind: str, w: float, h: float, tray: bool = True) -> str:
    q = {"w": f"{w:g}", "h": f"{h:g}", **({} if tray else {"tray": "0"})}
    return f"{REF}{kind}?{urlencode(q)}"


def parse(uri: str) -> dict[str, Any] | None:
    """{"kind", "w", "h", "tray"} of a generic uri (a curtain's: agentic_sim_world_gen.assets.generic_curtain.parse),
    or None."""
    if not uri.startswith(REF):
        return None
    if uri.startswith("generic:curtain"):  # a pleated window curtain
        from agentic_sim_world_gen.assets import generic_curtain

        return generic_curtain.parse(uri)
    kind, _, query = uri.removeprefix(REF).partition("?")
    if kind == "rug":
        from agentic_sim_world_gen.assets import rug

        return rug.parse_query(query)
    if kind not in KINDS:
        return None
    q = {k: v[0] for k, v in parse_qs(query).items()}
    dw, dh = KINDS[kind]["size"]
    try:
        w, h = float(q.get("w", dw)), float(q.get("h", dh))
    except ValueError:
        return None
    if not (0.05 <= w <= 20 and 0.05 <= h <= 10):
        return None
    return {"kind": kind, "w": w, "h": h, "tray": q.get("tray", "1") != "0"}


def record(kind: str, min_size=None, max_size=None) -> AssetRecord:
    w, h, tray = board_size(kind, min_size, max_size)
    d = TRAY_D if tray else DEPTH
    uri = uri_for(kind, w, h, tray)
    k = KINDS[kind]
    return AssetRecord(
        id=uri, source=SOURCE, uri=uri, name=f"generic_{kind}_{w:g}x{h:g}",
        bbox_min=(-d / 2, -w / 2, 0.0), bbox_max=(d / 2, w / 2, h),
        semantic_class=kind, caption=k["caption"], tags=[kind, "board", "wall mounted", "generic"],
        license=LICENSE, author=AUTHOR, front_deg=0.0,
    )


def candidate(kind: str, min_size=None, max_size=None) -> dict[str, Any]:
    """The board as a search candidate (a record dict with its match, like _find_candidates returns)."""
    from agentic_sim_world_gen.scene.spec import record_to_dict

    d = record_to_dict(record(kind, min_size, max_size))
    d["match"] = {"score": 3.0, "matched": [f"generic {kind}: a plain board built in compose"],
                  "weak": False, "generic": True}
    return d


def candidates(kind: str, query: str | None = None, alt_queries: list[str] | None = None, min_size=None,
               max_size=None) -> list[dict[str, Any]]:
    """The search candidates of a generic kind: one board, or a rug per material."""
    if kind == "rug":
        from agentic_sim_world_gen.assets import rug

        return rug.candidates(query, alt_queries, min_size, max_size)
    return [candidate(kind, min_size, max_size)]


def hint(kind: str) -> str:
    """kind_availability's generic_hint: what the search offers instead of a substitute."""
    if kind == "rug":
        from agentic_sim_world_gen.assets import rug

        w, d = rug.SIZE
        return ("no source has a floor rug, but a search for one always offers a plain rug built in compose (candidates "
                f"of source 'generic', one per CC0 carpet / fabric texture, default {w:g} x {d:g} m and "
                f"{rug.THICK * 100:g} cm thick; min_size / max_size set the size, tint the color, 'round' in the query "
                "makes it round): use it, do not ask the user for a substitute")
    w, h = KINDS[kind]["size"]
    return (f"no source may have a wall {kind}, but a search for one always offers a plain {kind} built in compose "
            f"(candidate source 'generic', default {w:g} x {h:g} m, min_size / max_size set the width and height): use "
            "it, do not ask the user for a substitute")


def is_rug(a: Any) -> bool:
    """A generic rug: an AssetRecord or a uri."""
    uri = a if isinstance(a, str) else getattr(a, "uri", "") or ""
    return uri.startswith(f"{REF}rug?") or uri == f"{REF}rug"


def wants_standing(query: str | None) -> bool:
    """The query asks for a board on legs or wheels ("mobile whiteboard", "standing chalkboard"): the plain wall
    board is still offered, but last."""
    return bool(_STANDING & set(_norm(query).split()))


def is_board(c: dict[str, Any], kind: str) -> bool:
    """The candidate is that kind of board itself: its class or name says so, not only a tag (a chalk stick tagged
    "chalkboard" is no chalkboard)."""
    for text in (c.get("class") or c.get("semantic_class") or "", c.get("name") or ""):
        t = " " + _norm(text.replace("_", " ")) + " "
        if any(f" {w} " in t for w in KINDS[kind]["words"]):
            return True
    return False


def standing(c: dict[str, Any]) -> bool:
    """A candidate whose class / tags / caption say it stands on the floor (an A-frame chalkboard sign)."""
    text = " ".join([c.get("class") or c.get("semantic_class") or "", *(c.get("tags") or []), c.get("caption") or "",
                     c.get("name") or ""])
    return bool(_STANDING & set(_norm(text.replace("_", " ")).split()))


def origin(uri: str) -> dict[str, Any]:
    """agentic_sim_world_gen.origin for a generic uri: made here, nothing to fetch."""
    p = parse(uri) or {}
    return {"from": "agentic-sim-world-gen (generic geometry)", "url": uri, "version": None, "path": p.get("kind") or uri,
            "pinned": True, "note": None}
