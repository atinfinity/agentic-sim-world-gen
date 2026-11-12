"""Parts of an asset that ignore where the asset is placed.

Some SimReady assets (ForgeCAD's range oven, ATM) author their moving parts (knobs, doors, keys) as rigid bodies
nested under the main body with `!resetXformStack!` and a world matrix (`xformOp:transform:simulationWorld`). A
reset makes the matrix absolute in whatever stage the asset lands in, so wherever the asset is placed those parts
stay near the stage origin. Their joints then pull them across the room and the asset flies apart in check.

compose keeps the reset (PhysX wants it on a rigid body nested under another one) and writes, for each such part, the
world matrix it would have with the asset where it is placed: the part's own matrix (its world in the asset's stage)
times the world transform of the prim holding the reference.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

VERSION = 1
OP = "xformOp:transform:agentic_sim_world_gen"


def wanted(a, uri: str) -> bool:
    """Worth opening the asset for: a local file (cheap), or a remote asset known to have more than one rigid
    body (only nested bodies can be reset parts; fetching every remote asset's layers would be slow)."""
    if uri.startswith("polyhaven:"):
        return False
    if not uri.startswith(("http://", "https://")):
        return True
    return (a.rigid_body_count or 0) > 1


def cache_dir() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_CACHE", "~/.cache/agentic-sim-world-gen")).expanduser()


def scan_stage(stage) -> list[dict[str, Any]]:
    """[{path (relative to the default prim), matrix (16 floats, row-major), instance (path of the instanceable
    ancestor, relative, or None)}] for every prim under the default prim that resets the transform stack."""
    from pxr import Usd, UsdGeom

    root = stage.GetDefaultPrim() or next(iter(stage.GetPseudoRoot().GetChildren()), None)
    if not root:
        return []
    out = []
    for p in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
        if p == root or not p.IsA(UsdGeom.Xformable):
            continue
        xf = UsdGeom.Xformable(p)
        if not xf.GetResetXformStack():
            continue
        m = xf.GetLocalTransformation(Usd.TimeCode.Default())
        inst = None
        if p.IsInstanceProxy():
            a = p.GetParent()
            while a and not a.IsInstance():
                a = a.GetParent()
            inst = a.GetPath().MakeRelativePath(root.GetPath()).pathString if a else None
        out.append({"path": p.GetPath().MakeRelativePath(root.GetPath()).pathString,
                    "matrix": [float(v) for row in m for v in row], "instance": inst})
    return out


def _part_paths(stage, test) -> list[dict[str, Any]]:
    """[{path (relative to the default prim), instance (the instanceable ancestor, relative, or None)}] for every
    prim under the default prim, the root itself excluded, for which test(prim) holds."""
    from pxr import Usd

    root = stage.GetDefaultPrim() or next(iter(stage.GetPseudoRoot().GetChildren()), None)
    if not root:
        return []
    out = []
    for p in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
        if p == root or not test(p):
            continue
        inst = None
        if p.IsInstanceProxy():
            a = p.GetParent()
            while a and a.IsInstanceProxy():
                a = a.GetParent()
            inst = a.GetPath().MakeRelativePath(root.GetPath()).pathString if a else None
        out.append({"path": p.GetPath().MakeRelativePath(root.GetPath()).pathString, "instance": inst})
    return out


def body_paths(stage) -> list[dict[str, Any]]:
    """Every rigid body under the default prim, the root itself excluded; see _part_paths."""
    from pxr import UsdPhysics

    return _part_paths(stage, lambda p: p.HasAPI(UsdPhysics.RigidBodyAPI))


def sdf_paths(stage) -> list[dict[str, Any]]:
    """Every mesh collider approximated as an SDF under the default prim (the root included when it is one)."""
    from pxr import Usd, UsdPhysics

    def sdf(p) -> bool:
        return (p.HasAPI(UsdPhysics.MeshCollisionAPI)
                and UsdPhysics.MeshCollisionAPI(p).GetApproximationAttr().Get() == "sdf")

    root = stage.GetDefaultPrim() or next(iter(stage.GetPseudoRoot().GetChildren()), None)
    own = [{"path": ".", "instance": None}] if root and sdf(root) else []
    return own + _part_paths(stage, sdf)


def _scan_cached(kind: str, fn, uri: str, cache_dir: str | Path) -> list[dict[str, Any]]:
    """fn(stage) of the asset at `uri` (local path or http URL; the USD layers of a URL are fetched into the cache),
    cached by uri. [] when it cannot be opened (offline and not fetched yet: nothing is written)."""
    cache = Path(cache_dir)
    key = cache / kind / f"{hashlib.sha1(f'{VERSION}:{uri}'.encode()).hexdigest()[:16]}.json"
    if key.exists():
        return json.loads(key.read_text())
    from pxr import Usd

    path = uri[7:] if uri.startswith("file://") else uri
    if path.startswith("polyhaven:"):
        return []
    if path.startswith(("http://", "https://")):
        from agentic_sim_world_gen.assets.remote_layers import _Fetcher

        path = _Fetcher(cache / "remote").local_path(path)
        if not path:
            return []
    elif not Path(path).exists():
        return []
    else:
        from agentic_sim_world_gen.assets.remote_layers import openable_copy

        path = str(openable_copy(path, cache) or path)
    stage = Usd.Stage.Open(path, load=Usd.Stage.LoadAll)
    out = fn(stage) if stage else []
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps(out))
    return out


def scan(uri: str, cache_dir: str | Path) -> list[dict[str, Any]]:
    """Reset parts of the asset at `uri` (local path or http URL), cached by uri. [] when it cannot be opened."""
    return _scan_cached("reset_parts", scan_stage, uri, cache_dir)


def scan_bodies(uri: str, cache_dir: str | Path) -> list[dict[str, Any]]:
    """Rigid bodies below the root of the asset at `uri` (body_paths), cached by uri."""
    return _scan_cached("bodies", body_paths, uri, cache_dir)


def disable_bodies(stage, ref_path: str, bodies: list[dict[str, Any]]) -> int:
    """Author physics:rigidBodyEnabled = false on each body under ref_path (the prim holding the reference), in the
    edit target layer with Sdf so it works whether or not the asset resolves here; an instance holding a body is
    made non-instanceable first. Returns how many instances were opened."""
    from pxr import Sdf

    layer = stage.GetEditTarget().GetLayer()
    opened = set()
    for b in bodies:
        if b.get("instance") and b["instance"] not in opened:
            Sdf.CreatePrimInLayer(layer, f"{ref_path}/{b['instance']}").instanceable = False
            opened.add(b["instance"])
        spec = Sdf.CreatePrimInLayer(layer, f"{ref_path}/{b['path']}")
        attr = spec.attributes.get("physics:rigidBodyEnabled") or Sdf.AttributeSpec(
            spec, "physics:rigidBodyEnabled", Sdf.ValueTypeNames.Bool)
        attr.default = False
    return len(opened)


def scan_sdf(uri: str, cache_dir: str | Path) -> list[dict[str, Any]]:
    """SDF mesh colliders of the asset at `uri` (sdf_paths), cached by uri."""
    return _scan_cached("sdf", sdf_paths, uri, cache_dir)


def box_colliders(stage, ref_path: str, colliders: list[dict[str, Any]]) -> int:
    """Author physics:approximation = boundingCube on each collider under ref_path, like disable_bodies (Sdf overs,
    instances holding one opened). Returns how many instances were opened."""
    from pxr import Sdf

    layer = stage.GetEditTarget().GetLayer()
    opened = set()
    for c in colliders:
        if c.get("instance") and c["instance"] not in opened:
            Sdf.CreatePrimInLayer(layer, f"{ref_path}/{c['instance']}").instanceable = False
            opened.add(c["instance"])
        path = ref_path if c["path"] == "." else f"{ref_path}/{c['path']}"
        spec = Sdf.CreatePrimInLayer(layer, path)
        attr = spec.attributes.get("physics:approximation") or Sdf.AttributeSpec(
            spec, "physics:approximation", Sdf.ValueTypeNames.Token, Sdf.VariabilityUniform)
        attr.default = "boundingCube"
    return len(opened)


def place(stage, unit_path: str, ref_path: str, parts: list[dict[str, Any]]) -> int:
    """Author the placed world matrix of each reset part under ref_path (the prim holding the reference). The
    matrix maps the asset's stage to the scene as the unit prim does. Returns how many parts were written."""
    from pxr import Gf, Sdf, Usd, UsdGeom

    if not parts:
        return 0
    unit = UsdGeom.XformCache(Usd.TimeCode.Default()).GetLocalToWorldTransform(stage.GetPrimAtPath(unit_path))
    for p in parts:
        if p.get("instance"):
            stage.OverridePrim(f"{ref_path}/{p['instance']}").SetInstanceable(False)
    n = 0
    for p in parts:
        prim = stage.OverridePrim(f"{ref_path}/{p['path']}")
        v = p["matrix"]
        m = Gf.Matrix4d(*v) * unit
        prim.CreateAttribute(OP, Sdf.ValueTypeNames.Matrix4d, custom=False).Set(m)
        prim.CreateAttribute("xformOpOrder", Sdf.ValueTypeNames.TokenArray, custom=False).Set(["!resetXformStack!", OP])
        n += 1
    return n
