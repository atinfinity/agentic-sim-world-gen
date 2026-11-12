"""Probe the room physics of a generated scene in Isaac Sim (headless): floor collider, floor friction,
wall colliders. Adds three 20 cm test cubes (in memory only, the file is not changed):

  drop   dropped from 1 m at the room center    -> must rest on the floor (z = 0.1), not sink
  slide  launched at --speed along +x           -> stops after v^2 / (2 mu g), mu = floor dynamic friction
  wall   launched at 6 m/s toward the west wall -> must stay inside the room (no tunneling)

The cubes use friction combine mode "min" with a high own friction, so the contact friction is the
floor's. Run with Isaac Sim's Python, on a scene with an empty middle (e.g. no objects):
    $ISAACSIM_PYTHON scripts/isaac/physics_probe.py out/empty/scene.usda --report out/empty/probe.json

Verified on Isaac Sim 6.1.0 (pip), 2026-10-25.
"""

from __future__ import annotations

import argparse
import json
import math

ap = argparse.ArgumentParser()
ap.add_argument("usd")
ap.add_argument("--speed", type=float, default=2.0, help="[m/s] slide cube launch speed")
ap.add_argument("--seconds", type=float, default=3.0)
ap.add_argument("--report")
ap.add_argument("--frames", help="save PNG frames of the run in this folder")
ap.add_argument("--gif", help="assemble the frames into this GIF (needs ffmpeg)")
args = ap.parse_args()

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": 1280, "height": 720})

import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, PhysxSchema, Usd, UsdGeom, UsdPhysics, UsdShade  # noqa: E402
from view import FrameRecorder, dollhouse_camera  # noqa: E402

ctx = omni.usd.get_context()
assert ctx.open_stage(args.usd), f"failed to open {args.usd}"
for _ in range(300):
    app.update()
    if ctx.get_stage_loading_status()[2] == 0:
        break
stage = ctx.get_stage()
G = 9.81

floor = stage.GetPrimAtPath("/World/Room/Floor")
rng = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]).ComputeWorldBound(floor).ComputeAlignedRange()
(x0, y0, zb), (x1, y1, z_floor) = rng.GetMin(), rng.GetMax()
t = z_floor - zb  # the slab reaches under the walls by the wall thickness (= slab thickness)
x0, y0, x1, y1 = x0 + t, y0 + t, x1 - t, y1 - t  # room interior
mat, _ = UsdShade.MaterialBindingAPI(floor).ComputeBoundMaterial("physics")
mu_s = mu_d = None
if mat:
    m = UsdPhysics.MaterialAPI(mat.GetPrim())
    mu_s, mu_d = m.GetStaticFrictionAttr().Get(), m.GetDynamicFrictionAttr().Get()

# probe material: friction combine "min" + high own friction => contact friction = floor friction
pm = UsdShade.Material.Define(stage, "/World/Probe/Material")
UsdPhysics.MaterialAPI.Apply(pm.GetPrim()).CreateStaticFrictionAttr(10.0)
UsdPhysics.MaterialAPI(pm.GetPrim()).CreateDynamicFrictionAttr(10.0)
UsdPhysics.MaterialAPI(pm.GetPrim()).CreateRestitutionAttr(0.0)
px = PhysxSchema.PhysxMaterialAPI.Apply(pm.GetPrim())
px.CreateFrictionCombineModeAttr("min")
px.CreateRestitutionCombineModeAttr("min")

S = 0.2
cx, cy = (x0 + x1) / 2, (y0 + y1) / 2


def cube(name: str, pos, vel=(0, 0, 0), color=(0.8, 0.8, 0.8)) -> str:
    path = f"/World/Probe/{name}"
    c = UsdGeom.Cube.Define(stage, path)
    c.CreateSizeAttr(S)
    c.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    UsdGeom.XformCommonAPI(c).SetTranslate(Gf.Vec3d(*pos))
    p = c.GetPrim()
    UsdPhysics.CollisionAPI.Apply(p)
    rb = UsdPhysics.RigidBodyAPI.Apply(p)
    rb.CreateVelocityAttr(Gf.Vec3f(*vel))
    UsdPhysics.MassAPI.Apply(p).CreateMassAttr(5.0)
    b = UsdShade.MaterialBindingAPI.Apply(p)
    b.Bind(pm, UsdShade.Tokens.weakerThanDescendants, "physics")
    return path


z0 = z_floor + S / 2 + 0.001
slide_x0 = x0 + 0.5
probes = {
    "drop": cube("drop", (cx, cy, z_floor + 1.0), color=(0.85, 0.2, 0.15)),
    "slide": cube("slide", (slide_x0, y0 + (y1 - y0) * 0.25, z0), (args.speed, 0, 0), color=(0.15, 0.65, 0.25)),
    "wall": cube("wall", (cx, y0 + (y1 - y0) * 0.75, z0), (-6.0, 0, 0), color=(0.15, 0.35, 0.85)),
}
hidden = dollhouse_camera(stage)
for _ in range(30):  # let RTX settle before recording
    app.update()
rec = FrameRecorder(args.frames)

from omni.physx import get_physx_interface  # noqa: E402

phx = get_physx_interface()


def pos(path):
    t = phx.get_rigidbody_transformation(path)
    return tuple(t["position"]) if t.get("ret_val") else None


timeline = omni.timeline.get_timeline_interface()
timeline.play()
app.update()
min_x_wall = math.inf
for _ in range(int(args.seconds * 60)):
    app.update()
    rec.tick()
    w = pos(probes["wall"])
    if w:
        min_x_wall = min(min_x_wall, w[0])
end = {k: pos(p) for k, p in probes.items()}
timeline.pause()

slide = end["slide"][0] - slide_x0  # from the spawn point: the first read after play() is already moving
expected = args.speed**2 / (2 * mu_d * G) if mu_d else None
report = {
    "usd": args.usd,
    "floor_top_z": round(z_floor, 4),
    "floor_friction": {"static": mu_s, "dynamic": mu_d},
    "drop": {"rest_z": round(end["drop"][2], 4), "expected_z": round(z_floor + S / 2, 4),
             "ok": abs(end["drop"][2] - (z_floor + S / 2)) < 0.01},
    "slide": {"distance_m": round(slide, 3), "expected_m": round(expected, 3) if expected else None,
              "ok": expected is not None and abs(slide - expected) < max(0.1, 0.25 * expected)},
    "wall": {"min_x": round(min_x_wall, 3), "end_x": round(end["wall"][0], 3), "room_x0": round(x0, 3),
             "ok": end["wall"][0] - S / 2 > x0 - 0.01},
}
gif = rec.gif(args.gif)
if gif:
    report["gif"] = gif
text = json.dumps(report, indent=2)
print(text)
if args.report:
    with open(args.report, "w") as f:
        f.write(text)
app.close()
