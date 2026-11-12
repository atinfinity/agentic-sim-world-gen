"""Occupancy of a scene from Isaac Sim's Occupancy Map extension (isaacsim.asset.gen.omap): rays against the
colliders in the height band of a robot. The navigation map itself (inside / outside of the room, the robot's
reach, map.pgm / map.yaml) is made by agentic_sim_world_gen.nav from this.

    $ISAACSIM_PYTHON scripts/isaac/nav_map.py out/study/scene.usda --job out/study/nav/isaac_job.json \
        --out out/study/nav/isaac_raw.json

job: {"origin": [x, y, z] (a free spot), "res": cell [m], "min": [dx, dy, dz], "max": [dx, dy, dz]} (bounds relative to
     the origin, as the extension takes them)
out: {"min": [x, y, z] (world), "max": [...], "res", "dims": [w, h, d], "occupied": [y * w + x, ...], "free": count}

The generator needs the physics running (it reads the PhysX scene), so the timeline plays a few frames first.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

T0 = time.time()


def log(msg: str) -> None:
    print(f"[nav_map {time.time() - T0:6.1f} s] {msg}", flush=True)


ap = argparse.ArgumentParser()
ap.add_argument("usd")
ap.add_argument("--job", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--frames", type=int, default=10, help="physics frames before mapping")
args = ap.parse_args()
job = json.loads(Path(args.job).read_text())

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})

import omni.kit.app  # noqa: E402

# before the stage: enabling an extension with a big stage open did not return
omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate("isaacsim.asset.gen.omap", True)
app.update()
log("omap enabled")

import check_core  # noqa: E402  (scripts/isaac/check_core.py)
import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402


async def step() -> None:
    app.update()


# the same opening as check_scene: PhysX graph nodes for auto doors first (opening without them crashed Kit)
stage = check_core.run_sync(check_core.open_stage(args.usd, step))
assert stage is not None, f"failed to open {args.usd}"
check_core.mute_extra_scenes(stage)  # assets that bring their own physics scene
log("stage open")


def make_static(stage) -> dict[str, int]:
    """Every rigid body static and every SDF collider a convex hull (in the session layer; instances that hold one
    are made editable). The map reads only where the colliders are: with the restaurant's ~600 dishes and food as
    moving SDF bodies, the first physics frame did not finish in 20 minutes."""
    from pxr import Usd, UsdPhysics

    def walk():
        return Usd.PrimRange(stage.GetPseudoRoot(), Usd.TraverseInstanceProxies())

    def wanted(p) -> bool:
        if p.HasAPI(UsdPhysics.RigidBodyAPI):
            return True
        return p.HasAPI(UsdPhysics.MeshCollisionAPI) and UsdPhysics.MeshCollisionAPI(p).GetApproximationAttr().Get() == "sdf"

    n = {"bodies": 0, "sdf": 0, "instances": 0}
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        roots = set()
        for p in walk():
            if p.IsInstanceProxy() and wanted(p):
                q = p
                while q and q.IsInstanceProxy():
                    q = q.GetParent()
                if q and q.IsInstance():
                    roots.add(q.GetPath())
        for r in roots:
            stage.GetPrimAtPath(r).SetInstanceable(False)
        n["instances"] = len(roots)
        for p in list(walk()):
            if p.IsInstanceProxy():
                continue
            if p.HasAPI(UsdPhysics.RigidBodyAPI):
                UsdPhysics.RigidBodyAPI(p).CreateRigidBodyEnabledAttr().Set(False)
                n["bodies"] += 1
            if p.HasAPI(UsdPhysics.MeshCollisionAPI):
                a = UsdPhysics.MeshCollisionAPI(p).GetApproximationAttr()
                if a.Get() == "sdf":
                    a.Set("convexHull")
                    n["sdf"] += 1
    return n


log(f"made static: {make_static(stage)}")

import omni.physx  # noqa: E402
from isaacsim.asset.gen.omap.bindings import _omap  # noqa: E402

ctx = omni.usd.get_context()
timeline = omni.timeline.get_timeline_interface()
timeline.play()
log("playing")
for i in range(args.frames):
    app.update()
    log(f"frame {i}")
log(f"physics ran {args.frames} frames")

gen = _omap.Generator(omni.physx.get_physx_interface(), ctx.get_stage_id())
OCC, FREE, UNKNOWN = 4.0, 5.0, 6.0
gen.update_settings(float(job["res"]), OCC, FREE, UNKNOWN)
gen.set_transform(tuple(job["origin"]), tuple(job["min"]), tuple(job["max"]))
gen.generate2d()
log("generated")
buf = gen.get_buffer()
dims = list(gen.get_dimensions())
timeline.stop()
out = {
    "min": list(gen.get_min_bound()),
    "max": list(gen.get_max_bound()),
    "res": float(job["res"]),
    "dims": dims,
    # the generator's rows run from max x down to min x: flip them so x grows with the index like y does
    "occupied": [(k // dims[0]) * dims[0] + dims[0] - 1 - k % dims[0] for k, v in enumerate(buf) if v == OCC],
    "free": sum(1 for v in buf if v == FREE),
    "cells": len(buf),
}
Path(args.out).write_text(json.dumps(out))
print(f"occupancy map {dims} cells, {len(out['occupied'])} occupied, {out['free']} free")
app.close()
