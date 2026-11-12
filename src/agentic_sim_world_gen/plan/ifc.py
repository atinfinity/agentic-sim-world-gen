"""IFC floor plan -> Room fields (needs `pip install ifcopenshell`, extra "ifc").

One storey is read (`storey` = name or index; default: the lowest storey that has walls):
- IfcWall / IfcWallStandardCase / IfcCurtainWall: the shape is measured along the wall's own X axis
  (its placement), giving a centerline, thickness and height. Curved walls become straight ones.
- IfcDoor / IfcWindow: snapped to the nearest wall; width = extent along the wall, height and sill
  from the shape (sill relative to the storey's floor).
- IfcSpace: footprint polygon -> zone (LongName or Name as display name).
- IfcFurnishingElement (IfcFurniture, IfcSystemFurnitureElement, ...): `furniture` entries with position
  (footprint center), bottom z, size along the element's own X / Y and height, facing, the zone it stands in,
  its kind (class, PredefinedType, names, classification) and a guessed English search query.
  Front convention: the element's local -Y (BIM furniture families face "down" in plan; fix per object
  with front_offset_deg when an asset ends up turned).
Geometry comes from ifcopenshell.geom in world coordinates, in the file's length unit, scaled to meters
here (the kernel's own conversion is skipped by some files, e.g. ones with an IfcProjectLibrary).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from agentic_sim_world_gen.plan.json_plan import from_dict, project_opening

WALL_CLASSES = ("IfcWall", "IfcCurtainWall")  # IfcWallStandardCase is a subtype of IfcWall
FURNITURE_CLASSES = ("IfcFurnishingElement",)  # IFC4: IfcFurniture / IfcSystemFurnitureElement are subtypes
FRONT_LOCAL_DEG = -90.0  # element front = its local -Y

# PredefinedType / words in the names -> English search query (Claude refines it, e.g. from a Japanese name)
QUERY_BY_TYPE = {
    "CHAIR": "chair", "TABLE": "table", "DESK": "desk", "BED": "bed", "FILECABINET": "file cabinet",
    "SHELF": "shelf", "SOFA": "sofa", "WORKSURFACE": "desk", "PANEL": "office partition panel",
}
QUERY_BY_WORD = [  # first match wins; checked against Name / ObjectType / type name, lowercased
    (r"office chair|task chair", "office chair"), (r"chair|stool|seat|椅子|いす|イス|チェア|スツール", "chair"),
    (r"desk|デスク|机", "desk"), (r"table|テーブル|卓", "table"), (r"sofa|couch|ソファ", "sofa"),
    (r"bed|ベッド", "bed"), (r"bookcase|bookshelf|本棚", "bookshelf"),
    (r"gondola|ゴンドラ", "gondola shelf"), (r"fridge|refrigerat|cooler|冷蔵|冷凍", "refrigerator"),
    (r"checkout|register|レジ", "checkout counter"), (r"counter|カウンター", "counter"),
    (r"locker|ロッカー", "locker"), (r"cabinet|cupboard|キャビネット|収納|書庫|戸棚", "cabinet"),
    (r"shelf|shelv|rack|棚|ラック", "shelf"), (r"wardrobe|closet|クローゼット|ワードローブ", "wardrobe"),
    (r"whiteboard|ホワイトボード", "whiteboard"), (r"partition|パーティション", "office partition panel"),
    (r"plant|植物|観葉", "potted plant"), (r"tv|television|テレビ", "television"),
]


def _verts(settings, e, scale: float = 1.0):
    import ifcopenshell.geom
    import numpy as np

    try:
        shape = ifcopenshell.geom.create_shape(settings, e)
    except Exception:  # elements without a body representation
        return None, None
    v = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3) * scale
    f = np.array(shape.geometry.faces, dtype=int).reshape(-1, 3)
    return (v, f) if len(v) else (None, None)


def _x_axis(e) -> tuple[float, float]:
    import ifcopenshell.util.placement

    m = ifcopenshell.util.placement.get_local_placement(e.ObjectPlacement)
    x, y = float(m[0][0]), float(m[1][0])
    L = math.hypot(x, y) or 1.0
    return x / L, y / L


def _wall(e, v) -> dict[str, Any]:
    dx, dy = _x_axis(e)
    nx, ny = -dy, dx
    u = v[:, 0] * dx + v[:, 1] * dy
    n = v[:, 0] * nx + v[:, 1] * ny
    if (u.max() - u.min()) < (n.max() - n.min()):  # placement X is across the wall: use the long side
        dx, dy, nx, ny = nx, ny, -dx, -dy
        u = v[:, 0] * dx + v[:, 1] * dy
        n = v[:, 0] * nx + v[:, 1] * ny
    nc = (n.max() + n.min()) / 2
    a = (dx * u.min() + nx * nc, dy * u.min() + ny * nc)
    b = (dx * u.max() + nx * nc, dy * u.max() + ny * nc)
    return {"start": [float(a[0]), float(a[1])], "end": [float(b[0]), float(b[1])],
            "thickness": round(float(n.max() - n.min()), 4), "height": round(float(v[:, 2].max() - v[:, 2].min()), 4),
            "_z0": float(v[:, 2].min()), "_name": e.Name}


def _footprint(v, f) -> list[list[float]] | None:
    """Outline of a space seen from above (union of its triangles)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    tris = []
    for a, b, c in f:
        p = Polygon([tuple(v[a][:2]), tuple(v[b][:2]), tuple(v[c][:2])])
        if p.area > 1e-8:
            tris.append(p)
    if not tris:
        return None
    shape = unary_union(tris).buffer(0.001).buffer(-0.001).simplify(0.005)
    if shape.geom_type == "MultiPolygon":
        shape = max(shape.geoms, key=lambda g: g.area)
    return [[round(x, 4), round(y, 4)] for x, y in list(shape.exterior.coords)[:-1]]


def _snap_ends(walls: list[dict[str, Any]]) -> None:
    """Wall bodies run to the outer corner (joined / mitered) or stop at the face of the wall they meet
    (T-junction). Move each end onto the centerline of the wall it meets, as the plan format expects."""
    for w in walls:
        (ax, ay), (bx, by) = w["start"], w["end"]
        L = math.hypot(bx - ax, by - ay)
        if L < 1e-6:
            continue
        dx, dy = (bx - ax) / L, (by - ay) / L
        for key, sign in (("start", 1), ("end", -1)):
            px, py = w[key]
            best = None
            for v in walls:
                if v is w:
                    continue
                (cx, cy), (ex, ey) = v["start"], v["end"]
                M = math.hypot(ex - cx, ey - cy)
                if M < 1e-6:
                    continue
                vx, vy = (ex - cx) / M, (ey - cy) / M
                den = dx * vy - dy * vx
                if abs(den) < 0.2:  # (nearly) parallel
                    continue
                t = ((cx - px) * vy - (cy - py) * vx) / den  # px + d t lies on v's line
                s_ = ((cx - px) * dy - (cy - py) * dx) / den
                reach = v["thickness"] * 0.75 + 0.01
                if abs(t) > reach or not -reach <= s_ <= M + reach:
                    continue
                if t * sign < -reach:  # would flip the wall
                    continue
                if best is None or abs(t) < abs(best):
                    best = t
            if best is not None:
                w[key] = [px + dx * best, py + dy * best]


def _storeys(model):
    out = sorted(model.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation or 0.0)
    return out


def load_ifc(path: str | Path, *, storey: str | int | None = None, default_thickness: float = 0.12,
             include_furniture: bool = True) -> dict[str, Any]:
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element
    import ifcopenshell.util.unit

    model = ifcopenshell.open(str(Path(path).expanduser()))
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    settings.set("convert-back-units", True)  # file units; scaled to meters below
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)

    def container(e):
        c = ifcopenshell.util.element.get_container(e)
        if c is None and (e.is_a("IfcDoor") or e.is_a("IfcWindow")):
            # doors / windows often hang off the wall's opening instead of the storey
            for rel in getattr(e, "FillsVoids", []) or []:
                wall = rel.RelatingOpeningElement.VoidsElements[0].RelatingBuildingElement
                return ifcopenshell.util.element.get_container(wall)
        for _ in range(4):  # furniture placed in a room (IfcSpace): walk up to the storey
            if c is None or not c.is_a("IfcSpace"):
                break
            c = ifcopenshell.util.element.get_aggregate(c)
        return c

    walls_all = [e for cls in WALL_CLASSES for e in model.by_type(cls)]
    storeys = _storeys(model)
    by_storey: dict[Any, list] = {}
    for e in walls_all:
        by_storey.setdefault(container(e), []).append(e)
    if isinstance(storey, int):
        chosen = storeys[storey]
    elif isinstance(storey, str):
        chosen = next((s for s in storeys if s.Name == storey or s.LongName == storey), None)
        if chosen is None:
            raise ValueError(f"no storey '{storey}'; storeys: {[s.Name for s in storeys]}")
    else:
        chosen = next((s for s in storeys if by_storey.get(s)), None) if storeys else None
    walls_ifc = by_storey.get(chosen, []) if chosen is not None else walls_all
    if not walls_ifc and chosen is None:
        walls_ifc = walls_all
    if not walls_ifc:
        raise ValueError("no walls found in the IFC file" + (f" on storey {chosen.Name}" if chosen else ""))

    walls, warnings = [], []
    for e in walls_ifc:
        v, _ = _verts(settings, e, scale)
        if v is None:
            warnings.append(f"wall {e.Name or e.GlobalId} has no geometry")
            continue
        walls.append(_wall(e, v))
    if not walls:
        raise ValueError("no wall geometry in the IFC file")
    _snap_ends(walls)
    floor_z = min(w["_z0"] for w in walls)
    for i, w in enumerate(walls):
        w["id"] = f"w{i}"
    heights = sorted(w["height"] for w in walls)
    room_h = heights[len(heights) // 2]

    def on_storey(e) -> bool:
        return chosen is None or container(e) in (chosen, None)

    openings = []
    for cls, kind in (("IfcDoor", "door"), ("IfcWindow", "window")):
        for e in model.by_type(cls):
            if not on_storey(e):
                continue
            v, _ = _verts(settings, e, scale)
            if v is None:
                continue
            c = (float((v[:, 0].max() + v[:, 0].min()) / 2), float((v[:, 1].max() + v[:, 1].min()) / 2))
            hit = project_opening(walls, c, max_dist=0.6)
            if hit is None:
                warnings.append(f"{kind} {e.Name or e.GlobalId} is not near any wall")
                continue
            wid, off, _ = hit
            w = next(w for w in walls if w["id"] == wid)
            L = math.dist(w["start"], w["end"])
            dx, dy = (w["end"][0] - w["start"][0]) / L, (w["end"][1] - w["start"][1]) / L
            proj = v[:, 0] * dx + v[:, 1] * dy
            z0, z1 = float(v[:, 2].min()), float(v[:, 2].max())
            o = {"kind": kind, "wall": wid, "offset": off, "width": round(float(proj.max() - proj.min()), 3),
                 "height": round(z1 - z0, 3)}
            if kind == "window" or z0 - floor_z > 0.05:
                o["sill"] = round(max(z0 - floor_z, 0.0), 3)
            if e.Name and re.match(r"[A-Za-z_]", e.Name):
                o["id"] = re.sub(r"[^A-Za-z0-9_]", "_", e.Name)
            openings.append(o)
    ids = [o.get("id") for o in openings if o.get("id")]
    if len(ids) != len(set(ids)):
        for o in openings:
            o.pop("id", None)

    zones = []
    for i, sp in enumerate(model.by_type("IfcSpace")):
        if not on_storey(sp):
            continue
        v, f = _verts(settings, sp, scale)
        if v is None:
            continue
        poly = _footprint(v, f)
        if poly and len(poly) >= 3:
            zones.append({"id": f"z{i}", "name": sp.LongName or sp.Name, "polygon": poly})

    zone_of_space = {sp.id(): f"z{i}" for i, sp in enumerate(model.by_type("IfcSpace"))}
    furniture = []
    if include_furniture:
        for cls in FURNITURE_CLASSES:
            for e in model.by_type(cls):
                if not on_storey(e):
                    continue
                v, _ = _verts(settings, e, scale)
                if v is None:
                    warnings.append(f"furniture {e.Name or e.GlobalId} has no geometry")
                    continue
                f = _furniture(e, v, floor_z)
                sp = ifcopenshell.util.element.get_container(e)
                if sp is not None and sp.is_a("IfcSpace"):
                    f["zone"] = zone_of_space.get(sp.id())
                furniture.append(f)

    for w in walls:
        w.pop("_z0")
        w.pop("_name")
    raw0 = list(walls[0]["start"])
    out = from_dict({"units": "m", "walls": walls, "openings": openings, "zones": zones,
                     "wall_thickness": default_thickness, "height": room_h})
    if furniture:
        s0 = out["plan"]["walls"][0]["start"]
        _finish_furniture(furniture, (s0[0] - raw0[0], s0[1] - raw0[1]), out["plan"]["zones"])
        out["furniture"] = furniture
    if warnings:
        out["warnings"] = warnings
    return out


def _texts(e) -> list[str]:
    import ifcopenshell.util.element

    ty = ifcopenshell.util.element.get_type(e)
    return [s for s in (e.Name, getattr(e, "ObjectType", None), ty.Name if ty else None,
                        getattr(e, "Description", None)) if s]


def _classification(e) -> list[str]:
    try:
        import ifcopenshell.util.classification as uc

        refs = uc.get_references(e)
    except Exception:
        return []
    out = []
    for r in refs:
        s = " ".join(x for x in (getattr(r, "Identification", None) or getattr(r, "ItemReference", None),
                                  r.Name) if x)
        if s:
            out.append(s)
    return out


def guess_query(predefined: str | None, texts: list[str]) -> tuple[str, str]:
    """(English query, where it came from: name / predefined_type / class)."""
    joined = " ".join(texts).lower()
    for pat, q in QUERY_BY_WORD:
        if re.search(pat, joined):
            return q, "name"
    if predefined and predefined.upper() in QUERY_BY_TYPE:
        return QUERY_BY_TYPE[predefined.upper()], "predefined_type"
    return "furniture", "class"


def _furniture(e, v, floor_z: float) -> dict[str, Any]:
    import ifcopenshell.util.element

    dx, dy = _x_axis(e)
    nx, ny = -dy, dx
    u = v[:, 0] * dx + v[:, 1] * dy
    n = v[:, 0] * nx + v[:, 1] * ny
    uc, nc = (u.max() + u.min()) / 2, (n.max() + n.min()) / 2
    pre = ifcopenshell.util.element.get_predefined_type(e)
    pre = pre if pre and pre not in ("NOTDEFINED", "USERDEFINED") else None
    texts = _texts(e)
    query, source = guess_query(pre, texts)
    f: dict[str, Any] = {
        "ifc_guid": e.GlobalId, "ifc_class": e.is_a(), "name": e.Name,
        "position": [float(dx * uc + nx * nc), float(dy * uc + ny * nc)],
        "z": round(max(float(v[:, 2].min()) - floor_z, 0.0), 3),
        "facing_deg": round(180.0 - (180.0 - math.degrees(math.atan2(dy, dx)) - FRONT_LOCAL_DEG) % 360.0, 2),  # (-180, 180]
        "size": [round(float(u.max() - u.min()), 3), round(float(n.max() - n.min()), 3),
                 round(float(v[:, 2].max() - v[:, 2].min()), 3)],
        "query": query, "query_from": source,
    }
    if pre:
        f["predefined_type"] = pre
    ty = ifcopenshell.util.element.get_type(e)
    if ty is not None and ty.Name:
        f["type_name"] = ty.Name
    for k in ("ObjectType", "Description"):
        if getattr(e, k, None):
            f[k[0].lower() + k[1:].replace("Type", "_type")] = getattr(e, k)
    if cl := _classification(e):
        f["classification"] = cl
    return f


def _finish_furniture(furniture: list[dict[str, Any]], shift: tuple[float, float],
                      zones: list[dict[str, Any]]) -> None:
    """Shift into the room frame, fill zones by point-in-polygon, give each a USD-safe unique id."""
    from shapely.geometry import Point, Polygon

    polys = [(z["id"], Polygon(z["polygon"])) for z in zones]
    known = {zid for zid, _ in polys}
    seen: dict[str, int] = {}
    for f in furniture:
        x, y = f["position"]
        f["position"] = [round(x + shift[0], 4), round(y + shift[1], 4)]
        if f.get("zone") not in known:
            f.pop("zone", None)
        if not f.get("zone"):
            pt = Point(f["position"])
            f["zone"] = next((zid for zid, poly in polys if poly.contains(pt)), None)
        if f.get("zone") is None:
            f.pop("zone", None)
        base = re.sub(r"[^A-Za-z0-9_]", "_", f["query"]).strip("_") or "item"
        seen[base] = seen.get(base, 0) + 1
        f["id"] = f"{base}_{seen[base]}"
