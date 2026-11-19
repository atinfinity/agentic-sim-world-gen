"""Corner pieces: the open corner of a footprint seen from above, for any source's USD.

A corner piece (a corner wall cabinet, an L desk, a corner sofa) has one bbox corner empty and the one across
filled; the solver puts the filled corner into a room corner and turns the open one into the room
(AssetRecord.corner, front_deg). An asset is measured once it is chosen (`find`, from service._ensure_corners),
when its name says corner.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

VERSION = 1  # bump when corner_deg changes: a candidate measured with an older one is measured again


class NoMeshes(ValueError):
    """The stage has no visible mesh: unmeasured, not cached."""


def named(text: str) -> bool:
    """A corner piece by name: "corner" in its name or path. A description is not read: "rounded corners" is said of
    many a box."""
    return "corner" in text.lower()


CORNER_GRID = 12  # cells per side of the footprint raster
CORNER_CELLS = 3  # the cells per side at a bbox corner that tell whether the corner is filled
CORNER_EMPTY, CORNER_FULL = 0.3, 0.9
CORNER_MIN_M = 0.3  # a smaller thing is not put in a room's corner (a jack-in-the-box's open lid, a watch winder)


def corner_deg(usd_file: Path | str) -> int | None:
    """The open corner of a corner piece seen from above: 45 / 135 / 225 / 315 when exactly one bbox corner is
    empty (a corner wall cabinet's diagonal doors, an L desk's inner corner, a carousel cabinet's cut) and the one
    across is filled, which goes into the room's corner; None for anything else (a pentagon, a box, a thing under
    CORNER_MIN_M across)."""
    from pxr import Usd

    return corner_deg_of_stage(Usd.Stage.Open(str(usd_file)))


def corner_deg_of_stage(st) -> int | None:
    """corner_deg of an open stage."""
    from pxr import Usd, UsdGeom

    z_up = UsdGeom.GetStageUpAxis(st) == UsdGeom.Tokens.z
    tris = []
    root = st.GetDefaultPrim() or st.GetPseudoRoot()
    for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):  # instanced parts too (as shelves.part_boxes)
        if not prim.IsA(UsdGeom.Mesh):
            continue
        img = UsdGeom.Imageable(prim)
        if (img.ComputePurpose() not in (UsdGeom.Tokens.default_, UsdGeom.Tokens.render)
                or img.ComputeVisibility() == UsdGeom.Tokens.invisible):  # a proxy / collision hull fills the corner
            continue
        m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        mesh = UsdGeom.Mesh(prim)
        pts = [m.Transform(v) for v in mesh.GetPointsAttr().Get() or []]
        pts = [(v[0], v[1]) if z_up else (v[0], -v[2]) for v in pts]
        counts, idx, k = mesh.GetFaceVertexCountsAttr().Get() or [], mesh.GetFaceVertexIndicesAttr().Get() or [], 0
        for c in counts:
            f = idx[k:k + c]
            k += c
            tris += [(pts[f[0]], pts[f[j]], pts[f[j + 1]]) for j in range(1, c - 1)]
    if not tris:  # nothing to see (a payload that was not fetched): not "no corner piece"
        raise NoMeshes("no meshes in the stage")
    xs, ys = [v[0] for t in tris for v in t], [v[1] for t in tris for v in t]
    x0, y0 = min(xs), min(ys)
    sx, sy = (max(xs) - x0) / CORNER_GRID, (max(ys) - y0) / CORNER_GRID
    if min(sx, sy) * CORNER_GRID * (UsdGeom.GetStageMetersPerUnit(st) or 1.0) < CORNER_MIN_M:
        return None
    filled = set()
    for a, b, c in tris:  # the cells whose centers a triangle covers
        def side(p, q, r):
            return (p[0] - r[0]) * (q[1] - r[1]) - (q[0] - r[0]) * (p[1] - r[1])

        i0 = max(0, int((min(a[0], b[0], c[0]) - x0) / sx - 0.5))
        i1 = min(CORNER_GRID - 1, int((max(a[0], b[0], c[0]) - x0) / sx))
        j0 = max(0, int((min(a[1], b[1], c[1]) - y0) / sy - 0.5))
        j1 = min(CORNER_GRID - 1, int((max(a[1], b[1], c[1]) - y0) / sy))
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                if (i, j) in filled:
                    continue
                p = (x0 + (i + 0.5) * sx, y0 + (j + 0.5) * sy)
                d = (side(p, a, b), side(p, b, c), side(p, c, a))
                if not (min(d) < 0 < max(d)):
                    filled.add((i, j))
    n, k = CORNER_GRID, CORNER_CELLS

    def fill(ci: int, cj: int) -> float:
        cells = [(i, j) for i in (range(k) if ci < 0 else range(n - k, n)) for j in (range(k) if cj < 0 else range(n - k, n))]
        return sum(c in filled for c in cells) / len(cells)

    corners = {45: (1, 1), 135: (-1, 1), 225: (-1, -1), 315: (1, -1)}
    empty = [deg for deg, (ci, cj) in corners.items() if fill(ci, cj) < CORNER_EMPTY]
    if len(empty) != 1:
        return None
    ci, cj = corners[empty[0]]
    return empty[0] if fill(-ci, -cj) >= CORNER_FULL else None


def find(uri: str, cache_dir: str | Path) -> int | None:
    """corner_deg of the asset at `uri` (local path or http URL), cached by uri and, for local files, their layers'
    stamps (as shelves.scan). Poly Haven and generic boards have none."""
    from agentic_sim_world_gen.assets.shelves import _local_stamp

    cache = Path(cache_dir)
    key = cache / "corner" / f"{hashlib.sha1(f'{VERSION}:{uri}{_local_stamp(uri)}'.encode()).hexdigest()[:16]}.json"
    if key.exists():
        return json.loads(key.read_text())["deg"]
    path = uri.removeprefix("file://")
    if path.startswith(("polyhaven:", "generic:")):
        return None
    if path.startswith(("http://", "https://")):
        from agentic_sim_world_gen.assets.remote_layers import _Fetcher

        path = _Fetcher(cache / "remote").local_path(path)
        if not path:
            raise OSError(f"could not fetch {uri}")  # offline: not cached, measured again later
    else:
        from agentic_sim_world_gen.assets.remote_layers import openable_copy

        path = str(openable_copy(path, cache) or path)
    deg = corner_deg(path)
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps({"deg": deg}))
    return deg
