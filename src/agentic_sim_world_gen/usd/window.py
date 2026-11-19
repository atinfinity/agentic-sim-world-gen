"""Plain windows for window openings no asset fills.

Without a window asset (the asset sources have none that fits: fill_windows reports not_found) a window
opening stays a hole and the renders show a gray void through it. Here it gets a plain window instead: an
aluminium frame (four bars) and a glass pane in the middle of the wall, a static collider like the wall.

    /World/Room/Windows/<opening>     Xform, customData agentic_sim_world_gen:window {opening, wall, generic: true}
        Frame_B, Frame_T, Frame_L, Frame_R, Glass   (<opening>: the id made a valid prim name, door.prim_name)

An opening too small for the frame (MIN_FRAMED) gets the glass alone.
"""

from __future__ import annotations

from pxr import Sdf, Usd, UsdGeom

from agentic_sim_world_gen.layout.room import RoomGeometry, Wall
from agentic_sim_world_gen.scene.plan import Opening
from agentic_sim_world_gen.usd.door import _bind, _cube, _material, prim_name

BAR = 0.05  # frame bar width [m]
FRAME_D = 0.06  # frame depth (across the wall) [m]
GLASS_T = 0.008
MIN_FRAMED = 2 * BAR + 0.05  # narrower or lower than this: glass only, no frame [m]


def window_frame(w: Wall, o: Opening) -> dict:
    """Geometry of a plain window in world coordinates (also used by tests)."""
    u0, u1 = w.interval(o)
    sill, top = o.resolved(w.height)
    return {"u0": u0, "u1": u1, "sill": sill, "top": top, "yaw": w.yaw_deg,
            "depth": min(FRAME_D, w.thickness)}


def unfilled_windows(geom: RoomGeometry, filled: set[str]) -> list[tuple[Wall, Opening]]:
    """Window openings that no object (a window asset, opening=<id>) fills."""
    return [(w, o) for w in geom.walls for o in w.openings if o.kind == "window" and o.id not in filled]


def build_windows(stage: Usd.Stage, geom: RoomGeometry, root: str, label, filled: set[str]) -> list[str]:
    """Author a plain window in every window opening not in `filled`. Returns the window prim paths."""
    todo = unfilled_windows(geom, filled)
    if not todo:
        return []
    UsdGeom.Xform.Define(stage, f"{root}/Windows")
    glass = _material(stage, f"{root}/Looks/WindowGlass", (0.78, 0.86, 0.9), 0.2, 0.05, 0.0)
    alu = _material(stage, f"{root}/Looks/WindowFrame", (0.72, 0.73, 0.75), 1.0, 0.35, 0.8)
    paths, used = [], set()
    for w, o in todo:
        f = window_frame(w, o)
        base = f"{root}/Windows/{prim_name(o.id, used)}"
        wp = UsdGeom.Xform.Define(stage, base).GetPrim()
        wp.SetCustomDataByKey("agentic_sim_world_gen:window", {"opening": o.id, "wall": w.id, "generic": True})
        wp.SetCustomDataByKey("agentic_sim_world_gen:opening", o.id)
        label(wp, "window")
        yaw, mid = f["yaw"], (f["u0"] + f["u1"]) / 2
        width, height = f["u1"] - f["u0"], f["top"] - f["sill"]
        zc, d = (f["sill"] + f["top"]) / 2, f["depth"]
        x, y = w.at(mid)
        framed = width >= MIN_FRAMED and height >= MIN_FRAMED
        bars = (("Frame_B", 0.0, f["sill"] + BAR / 2, width, BAR),
                ("Frame_T", 0.0, f["top"] - BAR / 2, width, BAR),
                ("Frame_L", -(width - BAR) / 2, zc, BAR, height - 2 * BAR),
                ("Frame_R", (width - BAR) / 2, zc, BAR, height - 2 * BAR)) if framed else ()
        (dx, dy) = w.dir
        for name, du, z, sw, sh in bars:
            c = _cube(stage, f"{base}/{name}", (x + dx * du, y + dy * du, z), (sw, d, sh), yaw, collider=True)
            _bind(c.GetPrim(), alu)
        inset = 2 * BAR if framed else 0.0
        g = _cube(stage, f"{base}/Glass", (x, y, zc), (width - inset, GLASS_T, height - inset), yaw,
                  collider=True)
        _bind(g.GetPrim(), glass)
        # RTX: a pane of opacity 0.2 still casts a full shadow and the daylight through the window is gone
        # (the room went dark); real glass lets it through
        UsdGeom.PrimvarsAPI(g.GetPrim()).CreatePrimvar("doNotCastShadows", Sdf.ValueTypeNames.Bool).Set(True)
        paths.append(base)
    return paths

