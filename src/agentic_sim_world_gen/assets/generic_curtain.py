"""A pleated window curtain no asset source has, built in compose like the plain boards.

SimReady Central's "curtain" is a hospital cubicle curtain or a blind, the Isaac index has none and only a local:
folder of one's own may have a window curtain, so fill_windows left curtain_<window> in not_found. This is
the fallback: fabric surfaces that follow a wave (pleats) across the window, hung from a thin rail above it. Not
generated 3D: a few strips of quads, like the plain window and the boards.

A search for a curtain over a window (ObjectSpec.covers, a query that names a curtain / drape) gets one more
candidate through service._with_generic: source "generic", uri
"generic:curtain?w=<width>&h=<height>&style=<style>[&color=%23rrggbb]". It goes first when no candidate is a real
window curtain (all weak, made for another place such as a hospital cubicle curtain, or a shower / cubicle / bead
curtain), last otherwise (a local: folder's own curtain stays first). The solver stretches it to its window
(layout.solver._cover: width + COVER_WIDER, and the height to `drop`); compose writes the USD file
(agentic_sim_world_gen.usd.curtain).

Styles: open (two opaque panels gathered to the sides: the default), closed (two panels meeting in the middle),
sheer (one translucent white panel across the window: no shadow, the daylight comes through), open_sheer (a sheer
panel across the window and the opaque panels gathered to the sides in front of it; the usual Japanese home).

Asset frame (like the boards): Z-up, meters, front +X (the room side); width along Y, bottom at z = 0.
"""

from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import parse_qs, urlencode

from agentic_sim_world_gen.assets.record import AssetRecord

KIND = "curtain"
REF = "generic:curtain"
STYLES = ("open", "closed", "sheer", "open_sheer")
DRAPE_COLOR = "#d8cfc0"  # beige: the usual opaque curtain
SHEER_COLOR = "#f5f5f2"  # white lace
DEPTH = 0.14  # wall side of the rail bracket to the front of the pleats [m]
SILL_DROP = 0.15  # a curtain over a waist window hangs this far below the sill [m]
FLOOR_GAP = 0.01  # a floor-length curtain stops this far above the floor [m]
SIZE = (2.0, 2.2)  # default width, height before the solver fits it to its window [m]
X_SHEER = -DEPTH / 2 + 0.035  # the sheer's rail: next to the window
X_DRAPE = -DEPTH / 2 + 0.09  # the opaque panels' rail: in front of the sheer
# (pleat wavelength, amplitude) [m]: gathered panels fold tighter and deeper
PLEAT = {"gathered": (0.09, 0.03), "drawn": (0.16, 0.022), "sheer": (0.12, 0.012)}
GATHER = 0.18  # an open panel takes this share of the width (at least MIN_GATHER)
MIN_GATHER = 0.22
OVERLAP = 0.03  # closed panels overlap in the middle
STEPS = 10  # vertices per pleat
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
MAX_SIZE = (20.0, 10.0)  # no curtain is built over a window wider / taller than this [m]
# curtains that are not the window's, by the word right before "curtain": a shower / hospital cubicle / bead / door
# curtain (a blackout window curtain "for bedroom privacy" or "a shade of ivory" in its caption is still one)
_NOT_WINDOW = {"shower", "bath", "bathtub", "cubicle", "hospital", "medical", "stage", "theater", "theatre", "bead",
               "beaded", "door", "doorway", "partition", "booth", "strip", "pvc", "welding", "bed", "canopy"}
_WORDS = ("curtain", "curtains", "drape", "drapes", "drapery", "draperies")
_PANEL = ("panel", "panels")  # "curtain panels"
_COVERS = {"blind", "blinds", "shade", "shades", "shutter", "shutters"}  # other window covers


def _norm(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _curtain_at(w: list[str]) -> int | None:
    """Where the curtain word of a name is ("window curtain", "curtain panels", "drapery"), or None when it names no
    window curtain (a shower / cubicle / bead one, or none at all)."""
    if len(w) > 1 and w[-1] in _PANEL:
        w = w[:-1]
    if not w or w[-1] not in _WORDS:
        return None
    return None if len(w) > 1 and w[-2] in _NOT_WINDOW else len(w) - 1


def asks_for(query: str | None) -> bool:
    """A query that names a window curtain ("floor-length curtain", "sheer drapes", "curtain panels"), not a shower
    / cubicle one."""
    return _curtain_at(_norm(query)) is not None


def wanted(o: Any) -> bool:
    """An object that hangs over a window (covers) and asks for a curtain (its query or a synonym)."""
    if getattr(o, "covers", None) is None:
        return False
    return asks_for(o.query) or any(asks_for(q) for q in getattr(o, "alt_queries", None) or [])


def drop(sill: float, top: float, room_h: float, rise: float = 0.10) -> tuple[float, float]:
    """(bottom z, height) of the curtain over a window from sill to top: the rail `rise` above the window head
    (no higher than the ceiling); down to the floor for a floor-length window, else SILL_DROP below the sill."""
    z_top = min(top + rise, room_h)
    bottom = FLOOR_GAP if sill <= 0.05 + FLOOR_GAP else max(sill - SILL_DROP, FLOOR_GAP)
    return round(bottom, 4), round(z_top - bottom, 4)


def _clamp(v: float, lo: float | None, hi: float | None) -> float:
    if lo is not None and lo > 0 and v < lo:
        v = lo
    if hi is not None and hi > 0 and v > hi:
        v = hi
    return v


def size_for(min_size=None, max_size=None) -> tuple[float, float]:
    """(width, height) from a curtain's min_size / max_size (fill_windows sets both around the window's width:
    their geometric mean is about it). The solver stretches it to the window anyway."""
    lo, hi = min_size or (None,) * 3, max_size or (None,) * 3
    if lo[0] and hi[0]:
        w = (lo[0] * hi[0]) ** 0.5
    else:
        w = _clamp(SIZE[0], lo[0], hi[0])
    h = _clamp(SIZE[1], lo[2], hi[2])
    return round(w, 3), round(h, 3)


def fits_size(w: float, h: float) -> bool:
    """A size compose builds (a window 25 m wide gets no curtain: it stays not found)."""
    return 0.2 <= w <= MAX_SIZE[0] and 0.2 <= h <= MAX_SIZE[1]


def is_generic(c: dict[str, Any]) -> bool:
    """A candidate that is a curtain agentic-sim-world-gen builds."""
    return parse(c.get("uri") or "") is not None


def style_of(uri: str) -> tuple[str, str | None]:
    """(style, color or None when the default) of a generic curtain uri."""
    q = {k: v[0] for k, v in parse_qs(uri.partition("?")[2]).items()}
    return q.get("style", "open"), q.get("color")


def uri_for(w: float, h: float, style: str = "open", color: str | None = None) -> str:
    q = {"w": f"{w:g}", "h": f"{h:g}", "style": style, **({"color": color.lower()} if color else {})}
    return f"{REF}?{urlencode(q)}"


def check_style(style: str | None, color: str | None) -> None:
    if style is not None and style not in STYLES:
        raise ValueError(f"curtain style must be one of {', '.join(STYLES)}, not {style!r}")
    if color is not None and not _HEX.fullmatch(color):
        raise ValueError(f"curtain color must be #rrggbb, not {color!r}")


def parse(uri: str) -> dict[str, Any] | None:
    """{"kind": "curtain", "w", "h", "style", "color", "sheer_color"} of a generic curtain uri, or None."""
    if not uri.startswith(REF):
        return None
    rest = uri.removeprefix(REF)
    if rest and not rest.startswith("?"):
        return None
    q = {k: v[0] for k, v in parse_qs(rest.lstrip("?")).items()}
    try:
        w, h = float(q.get("w", SIZE[0])), float(q.get("h", SIZE[1]))
    except ValueError:
        return None
    style = q.get("style", "open")
    color = q.get("color")
    if not fits_size(w, h) or style not in STYLES or (color and not _HEX.fullmatch(color)):
        return None
    # the color is the opaque panels' (a sheer one alone takes it as its own)
    drape = color or DRAPE_COLOR
    sheer = (color if style == "sheer" and color else SHEER_COLOR)
    return {"kind": KIND, "w": w, "h": h, "style": style, "color": drape, "sheer_color": sheer}


def caption(style: str) -> str:
    what = {"open": "two opaque panels gathered to the sides", "closed": "two opaque panels drawn closed",
            "sheer": "one translucent sheer panel across the window",
            "open_sheer": "a sheer panel across the window and two opaque panels gathered to the sides"}[style]
    return f"plain pleated window curtain on a thin rail: {what} (generic geometry built by agentic-sim-world-gen)"


def record(w: float, h: float, style: str = "open", color: str | None = None) -> AssetRecord:
    from agentic_sim_world_gen.assets.generic import AUTHOR, LICENSE, SOURCE

    uri = uri_for(w, h, style, color)
    return AssetRecord(
        id=uri, source=SOURCE, uri=uri, name=f"generic_curtain_{style}_{w:g}x{h:g}",
        bbox_min=(-DEPTH / 2, -w / 2, 0.0), bbox_max=(DEPTH / 2, w / 2, h),
        semantic_class=KIND, caption=caption(style),
        tags=["curtain", "window curtain", "drape", "sheer" if "sheer" in style else "opaque", "generic"],
        license=LICENSE, author=AUTHOR, front_deg=0.0,
    )


def candidate(w: float, h: float, style: str = "open", color: str | None = None) -> dict[str, Any]:
    """The curtain as a search candidate (a record dict with its match, like _find_candidates returns)."""
    from agentic_sim_world_gen.scene.spec import record_to_dict

    d = record_to_dict(record(w, h, style, color))
    d["match"] = {"score": 3.0, "matched": [f"generic curtain ({style}): pleated fabric built in compose"],
                  "weak": False, "generic": True}
    return d


def real_curtain(c: dict[str, Any]) -> bool:
    """A candidate that is a window curtain: a good match, made for this kind of place, named a curtain (class,
    name or a tag) with no shower / cubicle / bead / door word right before it. The caption is not read: "for
    bedroom privacy" or "a shade of ivory" there says nothing about the kind."""
    m = c.get("match") or {}
    if m.get("weak") or m.get("off_place") or m.get("generic"):
        return False
    names = [c.get("semantic_class") or c.get("class") or "", c.get("name") or "", *(c.get("tags") or [])]
    words = [_norm(n.replace("_", " ")) for n in names]
    if any(w and w[-1] in _COVERS for w in words[:2]):  # a blind
        return False
    has = [w for w in words if w and (w[-1] in _WORDS or (len(w) > 1 and w[-1] in _PANEL and w[-2] in _WORDS))]
    return bool(has) and all(_curtain_at(w) is not None for w in has)


def with_curtain(o: Any, out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The candidates of a curtain over a window plus the generic one: first when none is a real window curtain."""
    size = size_for(o.min_size, o.max_size)
    if not fits_size(*size):
        return out
    cur = candidate(*size)
    if any(real_curtain(c) for c in out):
        return [*out, cur]
    return [cur, *out]


def panels(p: dict) -> list[tuple[str, float, float, float, str]]:
    """(name, y0, y1, x of its rail, pleat kind) of each fabric panel, y across the width. Seen from the room (it
    looks toward -X), +Y is on the right: Panel_L is the -Y one."""
    w, style = p["w"], p["style"]
    out = []
    if style in ("sheer", "open_sheer"):
        out.append(("Sheer", -w / 2, w / 2, X_SHEER if style == "open_sheer" else X_DRAPE, "sheer"))
    if style in ("open", "open_sheer"):
        g = min(max(GATHER * w, MIN_GATHER), w / 2)
        out += [("Panel_L", -w / 2, -w / 2 + g, X_DRAPE, "gathered"), ("Panel_R", w / 2 - g, w / 2, X_DRAPE, "gathered")]
    elif style == "closed":
        out += [("Panel_L", -w / 2, OVERLAP, X_DRAPE, "drawn"), ("Panel_R", -OVERLAP, w / 2, X_DRAPE, "drawn")]
    return out


def wave(y0: float, y1: float, x0: float, kind: str) -> list[tuple[float, float, float]]:
    """(x, y, dx/dy) along a panel: a whole number of pleats, starting and ending at the rail's x."""
    lam, amp = PLEAT[kind]
    n_pleats = max(1, round((y1 - y0) / lam))
    n = n_pleats * STEPS
    k = 2 * math.pi * n_pleats / (y1 - y0)
    out = []
    for i in range(n + 1):
        y = y0 + (y1 - y0) * i / n
        out.append((x0 + amp * math.sin(k * (y - y0)), y, amp * k * math.cos(k * (y - y0))))
    return out
