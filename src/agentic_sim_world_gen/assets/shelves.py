"""Shelf surfaces of a fixture, found from its geometry (no metadata needed).

A shelf is a thin, roughly horizontal part (board) big enough to stand things on. Fixtures modeled as one
mesh (SimReady pallet racks) have no board parts; for those the boards are found from the mesh's upward
faces instead: a layer of them at one height that covers enough of its extent (wire decks count). Its clearance is
the distance up to the lowest part above that overlaps a good share of it or of itself (the next
shelf, the cabinet top; parts lying on the board, like grilles, do not count). A board under a solid part that
stands on it and covers most of it (the plinth inside a closed table skirt) is buried, not a shelf. Surfaces are in the asset's Z-up frame in meters, relative to the pivot
(footprint center, bottom), the same frame the placement uses.

    {"z": top height, "x0", "x1", "y0", "y1": extent, "clear": clearance or None (open above), "part": prim}

Hooks are found from the same part boxes: a thin rod sticking out horizontally along the fixture's depth,
one end held by a part that reaches above and below it (the pegboard, the wire mesh), the other free (the tip).
A hook with something big hanging from it (its top just above the rod) is taken (pre-stocked fixtures).

    {"z": rod center height, "base": [x, y], "tip": [x, y], "busy": bool, "part": prim}
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

THIN = 0.05  # max board thickness [m]
MIN_SIDE = 0.12  # min board depth / width [m] (price rails, bumpers, trims are narrower)
MIN_AREA = 0.03  # [m^2]
COVER = 0.3  # a part above blocks a shelf when it covers this share of the shelf
BURY = 0.5  # a tall part standing on a board and covering this share of it buries the board
UP = 0.95  # a face is upward when its normal's z exceeds this
LAYER_GAP = 0.05  # upward faces closer than this in height form one layer [m]
DECK_COVER = 0.12  # a layer is a board when its faces cover this share of its extent (wire decks ~0.25)
TOP_AREA = 0.005  # a board's top is the highest height where upward faces within TOP_BAND add up to this [m^2]
TOP_BAND = 0.003  # [m]
POST_END = 0.12  # parts rising above a board within this of its ends (a rack's uprights) narrow it [m]
POST_RISE = (0.01, 0.3)  # ... between these heights above the board [m]
ROD = 0.012  # max rod cross-section [m]
HOOK_LEN = (0.08, 0.45)  # rod length [m]
TOUCH = 0.006  # the base end is this close to the part holding it [m]
NEAR = 0.03  # ... and that part is within this of the rod sideways (wire mesh pitch ~0.05)
SMALL = 0.05  # parts smaller than this (hook tips, price tags) neither hold a hook nor take it
VERSION = 8  # bump when detection changes (cached results are keyed by it)


def _overlap(a: dict, b: dict) -> float:
    w = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
    d = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
    return max(w, 0.0) * max(d, 0.0)


def _area(p: dict) -> float:
    return (p["x1"] - p["x0"]) * (p["y1"] - p["y0"])


def _is_board(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    w, d = b[0] - a[0], b[1] - a[1]
    return b[2] - a[2] <= THIN and min(w, d) >= MIN_SIDE and w * d >= MIN_AREA


def surfaces_from_boxes(boxes: list[tuple[str, tuple[float, ...], tuple[float, ...]]]) -> list[dict[str, Any]]:
    """boxes: [(part, (x0, y0, z0), (x1, y1, z1))] in the Z-up pivot frame -> shelves, lowest first."""
    parts = [{"part": p, "x0": a[0], "y0": a[1], "z0": a[2], "x1": b[0], "y1": b[1], "z1": b[2]} for p, a, b in boxes]
    out = []
    for s in parts:
        if not _is_board((s["x0"], s["y0"], s["z0"]), (s["x1"], s["y1"], s["z1"])):
            continue
        area = (s["x1"] - s["x0"]) * (s["y1"] - s["y0"])
        if any(q is not s and abs(q["z0"] - s["z1"]) < 0.02 and q["z1"] - q["z0"] > THIN
               and _overlap(s, q) >= BURY * area for q in parts):
            continue  # buried under a solid part (a plinth inside a skirt)
        above = [q["z0"] for q in parts if q is not s and q["z0"] >= s["z1"] + 0.02 and _area(q) >= MIN_AREA
                 and _overlap(s, q) >= COVER * min(area, _area(q))]  # one of several boards above also counts
        out.append({
            "part": s["part"], "z": round(s["z1"], 4),
            "x0": round(s["x0"], 4), "x1": round(s["x1"], 4), "y0": round(s["y0"], 4), "y1": round(s["y1"], 4),
            "clear": round(min(above) - s["z1"], 4) if above else None,
        })
    out.sort(key=lambda s: (s["z"], s["x0"], s["y0"]))
    return out


def _rod(a, b) -> bool:
    return sorted(b[i] - a[i] for i in range(3))[1] <= ROD


def hooks_from_boxes(boxes: list[tuple[str, tuple[float, ...], tuple[float, ...]]]) -> list[dict[str, Any]]:
    """boxes as for surfaces_from_boxes -> hooks, lowest row first."""
    if not boxes:
        return []
    xs = [v for _, a, b in boxes for v in (a[0], b[0])]
    ys = [v for _, a, b in boxes for v in (a[1], b[1])]
    dep = 1 if max(ys) - min(ys) <= max(xs) - min(xs) else 0  # hooks run along the shorter side
    lat = 1 - dep
    big = [(p, a, b) for p, a, b in boxes if max(b[i] - a[i] for i in range(3)) >= SMALL]
    out = []
    for p, a, b in boxes:
        d = [b[i] - a[i] for i in range(3)]
        if not (HOOK_LEN[0] <= d[dep] <= HOOK_LEN[1] and d[lat] <= ROD and d[2] <= ROD):
            continue
        c, z = (a[lat] + b[lat]) / 2, (a[2] + b[2]) / 2

        def held(e: float) -> bool:
            return any(q != p and qa[dep] - TOUCH <= e <= qb[dep] + TOUCH and qa[lat] - NEAR <= c <= qb[lat] + NEAR
                       and qa[2] < z - SMALL and qb[2] > z + SMALL for q, qa, qb in big)

        ends = (held(a[dep]), held(b[dep]))
        if ends[0] == ends[1]:
            continue  # loose rod or a bar between two parts
        base, tip = (a[dep], b[dep]) if ends[0] else (b[dep], a[dep])
        lo, hi = min(base, tip), max(base, tip)
        busy = any(q != p and not _rod(qa, qb) and qa[dep] < hi - 0.01 and qb[dep] > lo + 0.01
                   and qa[lat] < c < qb[lat] and qa[2] < z and z - 0.01 < qb[2] < z + 0.04 for q, qa, qb in big)

        def xy(v: float) -> list[float]:
            pt = [0.0, 0.0]
            pt[dep], pt[lat] = round(v, 4), round(c, 4)
            return pt

        out.append({"part": p, "z": round(z, 4), "base": xy(base), "tip": xy(tip), "busy": busy})
    out.sort(key=lambda h: (h["z"], h["tip"][0], h["tip"][1]))
    return out


def surfaces_of_stage(stage) -> list[dict[str, Any]]:
    """Shelves of an opened asset stage (usd-core)."""
    return scan_stage(stage)["shelves"]


def scan_stage(stage) -> dict[str, list[dict[str, Any]]]:
    """{"shelves", "hooks"} of an opened asset stage (usd-core)."""
    from pxr import Usd, UsdGeom

    up = UsdGeom.GetStageUpAxis(stage).upper()
    mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])

    def zup(v):
        x, y, z = v
        return (x, -z, y) if up == "Y" else (x, y, z)

    root = stage.GetDefaultPrim() or stage.GetPseudoRoot()
    total = cache.ComputeWorldBound(root).ComputeAlignedRange()
    if total.IsEmpty():
        return {"shelves": [], "hooks": []}
    a, b = zup(tuple(total.GetMin())), zup(tuple(total.GetMax()))
    lo, hi = [min(p, q) for p, q in zip(a, b)], [max(p, q) for p, q in zip(a, b)]
    piv = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2])
    boxes = []
    meshes = []
    for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):  # instanceable parts too
        if not prim.IsA(UsdGeom.Gprim):
            continue
        r = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        if r.IsEmpty():
            continue
        a, b = zup(tuple(r.GetMin())), zup(tuple(r.GetMax()))
        mn = tuple((min(p, q) - c) * mpu for p, q, c in zip(a, b, piv))
        mx = tuple((max(p, q) - c) * mpu for p, q, c in zip(a, b, piv))
        boxes.append((str(prim.GetPath()), mn, mx))
        if prim.IsA(UsdGeom.Mesh) and mx[2] - mn[2] > THIN:
            meshes.append(prim)
    out = surfaces_from_boxes(boxes)
    hooks = hooks_from_boxes(boxes)
    if out or not meshes or any(_is_board(a, b) for _, a, b in boxes):
        return {"shelves": out, "hooks": hooks}  # (board parts that are all buried: no shelves, not a one-mesh fixture)
    # no board parts: look for board layers inside the meshes
    for prim in meshes:
        mesh = UsdGeom.Mesh(prim)
        pts, counts, idx = (mesh.GetPointsAttr().Get(), mesh.GetFaceVertexCountsAttr().Get(),
                            mesh.GetFaceVertexIndicesAttr().Get())
        if not pts or not counts or not idx:
            continue
        xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        world = []
        for p in pts:
            v = zup(tuple(xf.Transform(p)))
            world.append(tuple((q - c) * mpu for q, c in zip(v, piv)))
        boxes.extend((f"{prim.GetPath()}#{i}", a, b) for i, (a, b) in enumerate(board_layers(world, counts, idx)))
    return {"shelves": surfaces_from_boxes(boxes), "hooks": hooks}


def board_layers(points, counts, indices) -> list[tuple[tuple[float, ...], tuple[float, ...]]]:
    """Board-like layers of upward faces of one mesh (Z-up points) -> [(min, max)] thin boxes."""
    faces = []  # (z, area, x0, y0, x1, y1)
    spans = []  # every face: (z0, z1, x0, y0, x1, y1)
    o = 0
    for c in counts:
        v = [points[i] for i in indices[o:o + c]]
        o += c
        if c < 3:
            continue
        spans.append((min(p[2] for p in v), max(p[2] for p in v), min(p[0] for p in v), min(p[1] for p in v),
                      max(p[0] for p in v), max(p[1] for p in v)))
        nx = ny = nz = 0.0
        for (x1, y1, z1), (x2, y2, z2) in zip(v, v[1:] + v[:1]):  # Newell's normal (2x area)
            nx += (y1 - y2) * (z1 + z2)
            ny += (z1 - z2) * (x1 + x2)
            nz += (x1 - x2) * (y1 + y2)
        n = (nx * nx + ny * ny + nz * nz) ** 0.5
        if n <= 0 or nz / n < UP:
            continue
        xs, ys = [p[0] for p in v], [p[1] for p in v]
        faces.append((sum(p[2] for p in v) / c, n / 2, min(xs), min(ys), max(xs), max(ys)))
    faces.sort()
    layers, cur = [], []
    for f in faces:
        if cur and (f[0] - cur[-1][0] > LAYER_GAP or f[0] - cur[0][0] > THIN):
            layers.append(cur)
            cur = []
        cur.append(f)
    if cur:
        layers.append(cur)
    out = []
    for lay in layers:
        x0, y0 = min(f[2] for f in lay), min(f[3] for f in lay)
        x1, y1 = max(f[4] for f in lay), max(f[5] for f in lay)
        w, d = x1 - x0, y1 - y0
        if min(w, d) < MIN_SIDE or w * d < MIN_AREA or sum(f[1] for f in lay) < DECK_COVER * w * d:
            continue
        top = _top(lay)
        x0, y0, x1, y1 = _inside_posts(spans, top, x0, y0, x1, y1)
        out.append(((x0, y0, top), (x1, y1, top)))
    return out


def _inside_posts(spans: list[tuple[float, ...]], top: float, x0: float, y0: float, x1: float, y1: float):
    """The board's extent inside the parts that rise above it at its ends: the deck layer of a rack reaches
    into its uprights, and boxes stocked to its ends were pushed 3-6 cm in by them. The long side
    first; then the short side, counting only parts between the new ends (the uprights are at the corners)."""
    lo, hi = top + POST_RISE[0], top + POST_RISE[1]
    up = [f for f in spans if f[0] < hi and f[1] > lo]
    for ax in ((0, 1) if x1 - x0 >= y1 - y0 else (1, 0)):
        b = [x0, y0, x1, y1]
        o = 1 - ax  # the other axis must overlap the board (strictly inside on the second pass)
        near = [f for f in up if f[2 + o] < b[2 + o] - 1e-4 and f[4 + o] > b[o] + 1e-4]
        left = [f[4 + ax] for f in near if f[2 + ax] < b[ax] + POST_END and f[4 + ax] < b[ax] + POST_END]
        right = [f[2 + ax] for f in near if f[4 + ax] > b[2 + ax] - POST_END and f[2 + ax] > b[2 + ax] - POST_END]
        if left:
            b[ax] = max(b[ax], max(left))
        if right:
            b[2 + ax] = min(b[2 + ax], min(right))
        x0, y0, x1, y1 = b
    return x0, y0, x1, y1


def _top(lay: list[tuple[float, ...]]) -> float:
    """Height of a board layer: the highest face, so products start clear of every bar and wire (a lower,
    area-weighted height made PhysX's SDF collider of a pallet rack throw boxes off a cross bar), but only
    where the faces add up to TOP_AREA: the hole edges of the uprights are 4 cm above a rack's deck."""
    lo, area = len(lay), 0.0
    for hi in range(len(lay) - 1, -1, -1):  # lay is sorted by height; window [lo, hi] within TOP_BAND below hi
        while lo > 0 and lay[hi][0] - lay[lo - 1][0] <= TOP_BAND:
            lo -= 1
            area += lay[lo][1]
        if area >= TOP_AREA:
            return lay[hi][0]
        area -= lay[hi][1]
    return lay[-1][0]


def find_shelves(uri: str, cache_dir: str | Path) -> list[dict[str, Any]]:
    """Shelves of the asset at `uri` (local path or http URL), cached by uri."""
    return scan(uri, cache_dir)["shelves"]


def find_hooks(uri: str, cache_dir: str | Path) -> list[dict[str, Any]]:
    """Hooks of the asset at `uri`, from the same cached scan as its shelves."""
    return scan(uri, cache_dir)["hooks"]


USD_SUFFIXES = (".usd", ".usda", ".usdc")
STAMP_FILES = 200  # a big folder of many assets stops counting here; the asset file itself always counts


def _local_stamp(uri: str) -> str:
    """Modification times and sizes of a local asset's USD layers, so a rebuilt asset is scanned again.

    The geometry of an exported asset sits in layers next to it (`payloads/`), so the files in the asset's
    folder and one level below count too. Remote and Poly Haven uris have no stamp (they are pinned)."""
    path = Path(uri[7:] if uri.startswith("file://") else uri)
    if uri.startswith(("http://", "https://", "polyhaven:")) or not path.is_file():
        return ""
    files = [path]
    for f in sorted([*path.parent.glob("*"), *path.parent.glob("*/*")])[:STAMP_FILES]:
        if f != path and f.suffix.lower() in USD_SUFFIXES and f.is_file():
            files.append(f)
    return "".join(f":{f.name}:{f.stat().st_mtime_ns}:{f.stat().st_size}" for f in files)


def scan(uri: str, cache_dir: str | Path) -> dict[str, list[dict[str, Any]]]:
    """{"shelves", "hooks"} of the asset at `uri`, cached by uri (and, for local files, their layers' stamps)."""
    cache = Path(cache_dir)
    stamp = _local_stamp(uri)
    key = cache / "shelves" / f"{hashlib.sha1(f'{VERSION}:{uri}{stamp}'.encode()).hexdigest()[:16]}.json"
    if key.exists():
        return json.loads(key.read_text())
    from pxr import Usd

    none: dict[str, list] = {"shelves": [], "hooks": []}
    path = uri[7:] if uri.startswith("file://") else uri
    if path.startswith("polyhaven:"):
        # Poly Haven models get one static box collider: stock on their shelf boards would sit inside it
        # and be pushed out, so they offer no shelves (fill reports shelf_fit, decor skips them)
        return none
    if path.startswith(("http://", "https://")):
        from agentic_sim_world_gen.assets.remote_layers import _Fetcher

        path = _Fetcher(cache / "remote").local_path(path)
        if not path:
            return none
    else:
        from agentic_sim_world_gen.assets.remote_layers import openable_copy

        path = str(openable_copy(path, cache) or path)
    stage = Usd.Stage.Open(path, load=Usd.Stage.LoadAll)
    out = scan_stage(stage) if stage else none
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps(out))
    return out
