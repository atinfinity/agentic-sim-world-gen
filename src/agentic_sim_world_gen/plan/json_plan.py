"""Floor plan JSON -> Room fields. Format: agentic_sim_world_gen.scene.plan (module docstring)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from agentic_sim_world_gen.scene.plan import FloorPlan, Opening

UNITS = {"m": 1.0, "cm": 0.01, "mm": 0.001, "px": None}
DEFAULT_THICKNESS = 0.12


def normalize(plan: dict[str, Any], default_thickness: float = DEFAULT_THICKNESS) -> dict[str, Any]:
    """Shift a plan (walls, zones) so the outer faces of its walls start at (0, 0). Also rounds to 0.1 mm."""
    xs, ys = [], []
    for w in plan["walls"]:
        h = (w.get("thickness") or default_thickness) / 2
        for x, y in (w["start"], w["end"]):
            xs += [x - h, x + h]
            ys += [y - h, y + h]
    dx, dy = -min(xs), -min(ys)

    def mv(p):
        return [round(p[0] + dx, 4), round(p[1] + dy, 4)]

    out = dict(plan)
    out["walls"] = [{**w, "start": mv(w["start"]), "end": mv(w["end"])} for w in plan["walls"]]
    out["zones"] = [{**z, "polygon": [mv(p) for p in z["polygon"]]} for z in plan.get("zones", [])]
    if plan.get("underlay"):
        u = plan["underlay"]
        sx, sy = u.get("shift", (0.0, 0.0))
        out["underlay"] = {**u, "shift": [round(sx + dx, 4), round(sy + dy, 4)]}
    return out


def _scale(d: dict[str, Any], k: float, flip_y: bool = False) -> dict[str, Any]:
    """Plan lengths * k (meters). flip_y: image pixels (y down) -> y up. Heights/sills scale only
    when not tracing pixels (they are meters then)."""
    if k == 1.0 and not flip_y:
        return d
    d = json.loads(json.dumps(d))
    fy = -1.0 if flip_y else 1.0

    def pt(p):
        return [p[0] * k, p[1] * k * fy]

    vertical = 1.0 if flip_y else k
    for w in d.get("walls", []):
        w["start"], w["end"] = pt(w["start"]), pt(w["end"])
        if w.get("thickness") is not None:
            w["thickness"] *= k
        if w.get("height") is not None:
            w["height"] *= vertical
    for z in d.get("zones", []):
        z["polygon"] = [pt(p) for p in z["polygon"]]
    for o in d.get("openings", []):
        for f in ("offset", "width", "clearance"):
            if o.get(f) is not None:
                o[f] *= k
        for f in ("height", "sill"):
            if o.get(f) is not None:
                o[f] *= vertical
    if d.get("wall_thickness") is not None:
        d["wall_thickness"] *= k
    if d.get("height") is not None:
        d["height"] *= vertical
    return d


def from_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Plan JSON (dict) -> {"plan", "openings"[, "height", "wall_thickness"]} in meters, shifted to (0, 0)."""
    units = d.get("units", "m")
    if units not in UNITS:
        raise ValueError(f"units must be one of {sorted(UNITS)}")
    underlay = d.get("underlay")
    if units == "px":
        if not underlay:
            raise ValueError('units "px" needs "underlay": {"image": ..., "m_per_px": ...}')
        d = _scale(d, underlay["m_per_px"], flip_y=True)
        d["underlay"] = {**underlay, "origin_px": [0.0, 0.0]}
    else:
        d = _scale(d, UNITS[units])
    t = d.get("wall_thickness") or DEFAULT_THICKNESS
    plan = normalize({"walls": d["walls"], "zones": d.get("zones", []), "underlay": d.get("underlay")}, t)
    if plan.get("underlay") is None:
        plan.pop("underlay")
    FloorPlan.model_validate(plan)  # fail early with a clear message
    openings = [Opening.model_validate(o).model_dump(exclude_none=True) for o in d.get("openings", [])]
    out: dict[str, Any] = {"plan": plan, "openings": openings}
    for f in ("height", "wall_thickness"):
        if d.get(f) is not None:
            out[f] = round(d[f], 4)
    return out


def load_plan(path: str | Path) -> dict[str, Any]:
    path = Path(path).expanduser()
    d = json.loads(path.read_text(encoding="utf-8"))
    u = d.get("underlay")
    if u and not Path(u["image"]).expanduser().is_absolute():  # image path relative to the JSON file
        d["underlay"] = {**u, "image": str((path.parent / u["image"]).resolve())}
    return from_dict(d)


# --- helpers shared by the DXF / IFC importers ----------------------------------------


def project_opening(walls: list[dict[str, Any]], center: tuple[float, float], max_dist: float = 0.5):
    """Nearest wall to a point -> (wall id, offset along it from start, distance), or None."""
    best = None
    for w in walls:
        (ax, ay), (bx, by) = w["start"], w["end"]
        L = math.hypot(bx - ax, by - ay)
        if L == 0:
            continue
        u = ((center[0] - ax) * (bx - ax) + (center[1] - ay) * (by - ay)) / L
        if not -0.01 <= u <= L + 0.01:
            continue
        px, py = ax + (bx - ax) * u / L, ay + (by - ay) * u / L
        dist = math.hypot(center[0] - px, center[1] - py)
        if dist <= max_dist and (best is None or dist < best[2]):
            best = (w["id"], round(min(max(u, 0.0), L), 4), dist)
    return best
