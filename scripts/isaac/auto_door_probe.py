"""Probe the automatic sliding doors of a scene in Isaac Sim (headless). For each auto_sliding door a
robot-sized kinematic box walks from inside the room, through the door, to the outside (and back with
--back). The panels' opening is logged over time from PhysX.

  ok = the door was closed at the start, opened before the box reached the wall, and closed again
       after the box left the sensor range

Door data comes from scene.resolved.json next to the USD (agentic-sim-world-gen from src/, needs only pydantic).
    $ISAACSIM_PYTHON scripts/isaac/auto_door_probe.py out/konbini/scene.usda --report out/konbini/auto_door.json \
        --frames out/konbini/door_frames --gif out/konbini/auto_door.gif

Verified on Isaac Sim 6.1.0 (pip), 2026-10-26.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("usd")
ap.add_argument("--size", default="0.45,0.45,0.6", help="[m] walker box (x, y, z)")
ap.add_argument("--speed", type=float, default=1.0, help="[m/s] walking speed")
ap.add_argument("--start", type=float, default=2.5, help="[m] start / end distance from the wall")
ap.add_argument("--wait", type=float, default=1.0, help="[s] standing still before and after the walk")
ap.add_argument("--back", action="store_true", help="walk back in after going out")
ap.add_argument("--report")
ap.add_argument("--frames", help="save PNG frames of the run in this folder")
ap.add_argument("--gif", help="assemble the frames into this GIF (needs ffmpeg)")
args = ap.parse_args()
SX, SY, SZ = (float(v) for v in args.size.split(","))

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from agentic_sim_world_gen.layout.room import RoomGeometry  # noqa: E402
from agentic_sim_world_gen.scene.spec import ResolvedScene  # noqa: E402

scene = ResolvedScene.load(Path(args.usd).parent / "scene.resolved.json")
geom = RoomGeometry(scene.spec.room)
doors = [(w, o) for w in geom.walls for o in w.openings if o.style == "auto_sliding"]
if not doors:
    sys.exit("no auto_sliding doors in this scene")

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": 1280, "height": 720})

from isaacsim.core.utils.extensions import enable_extension  # noqa: E402

enable_extension("omni.physx.graph")
for _ in range(5):  # let it load before a stage opens (opening right away crashed Kit)
    app.update()

import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Usd, UsdGeom, UsdPhysics  # noqa: E402
from view import FrameRecorder, hide_guides, wait_frames  # noqa: E402

ctx = omni.usd.get_context()
assert ctx.open_stage(args.usd), f"failed to open {args.usd}"
for _ in range(300):
    app.update()
    if ctx.get_stage_loading_status()[2] == 0:
        break
stage = ctx.get_stage()
if stage.GetPrimAtPath("/World/Room/Ceiling"):
    UsdGeom.Imageable(stage.GetPrimAtPath("/World/Room/Ceiling")).MakeInvisible()

from omni.physx import get_physx_interface  # noqa: E402

phx = get_physx_interface()
timeline = omni.timeline.get_timeline_interface()
results = []
for w, o in doors:
    base = f"/World/Room/Doors/{o.id}"
    info = dict(stage.GetPrimAtPath(base).GetCustomDataByKey("agentic_sim_world_gen:door") or {})
    inner = geom.inner_side(w, (sum(w.interval(o))) / 2)
    um = sum(w.interval(o)) / 2
    sill, _ = o.resolved(w.height)
    nx, ny = w.normal
    path = f"/World/Probe/{o.id}"
    c = UsdGeom.Cube.Define(stage, path)
    c.CreateSizeAttr(1.0)
    c.CreateDisplayColorAttr([Gf.Vec3f(0.95, 0.55, 0.1)])
    xf = UsdGeom.Xformable(c)
    tr = xf.AddTranslateOp()
    xf.AddRotateZOp().Set(w.yaw_deg)
    xf.AddScaleOp().Set(Gf.Vec3f(SX, SY, SZ))
    UsdPhysics.CollisionAPI.Apply(c.GetPrim())
    UsdPhysics.RigidBodyAPI.Apply(c.GetPrim()).CreateKinematicEnabledAttr(True)

    def place(n: float) -> None:
        x, y = w.at(um, n)
        tr.Set(Gf.Vec3d(x, y, sill + SZ / 2 + 0.01))

    # path: inside (+inner side) -> outside, optionally back; n = signed distance from the centerline
    legs = [(inner * args.start, -inner * args.start)]
    if args.back:
        legs.append((-inner * args.start, inner * args.start))
    place(legs[0][0])
    # camera inside the room, looking at the door from an angle
    cam = UsdGeom.Camera.Define(stage, f"/World/DoorCam_{o.id}")
    ex, ey = w.at(um + 1.6, inner * 3.6)
    tx, ty = w.at(um, 0.0)
    m = Gf.Matrix4d().SetLookAt(Gf.Vec3d(ex, ey, 1.7), Gf.Vec3d(tx, ty, sill + 0.9), Gf.Vec3d(0, 0, 1)).GetInverse()
    cxf = UsdGeom.Xformable(cam)
    cxf.ClearXformOpOrder()
    cxf.AddTransformOp().Set(m)
    cam.CreateFocalLengthAttr(16.0)
    from omni.kit.viewport.utility import get_active_viewport

    get_active_viewport().camera_path = cam.GetPath().pathString
    hide_guides()
    wait_frames(app, 30)
    rec = FrameRecorder(args.frames, every=3)

    panels = [f"{base}/Panel_L", f"{base}/Panel_R"]
    closed = {}
    log = []

    def opening() -> float:
        """Mean panel travel from the closed pose [m]."""
        d = []
        for p in panels:
            t = phx.get_rigidbody_transformation(p)
            if not t.get("ret_val"):
                return float("nan")
            pos = t["position"]
            closed.setdefault(p, pos)
            d.append(math.dist(pos[:2], closed[p][:2]))
        return sum(d) / len(d)

    timeline.play()
    wait_frames(app, 2)
    frame = 0
    hz = 60

    def tick(n: float) -> None:
        global frame
        place(n)
        app.update()
        rec.tick()
        if frame % 6 == 0:
            log.append((round(frame / hz, 2), round(n, 2), round(opening(), 3)))
        frame += 1

    first = opening()
    for _ in range(int(args.wait * hz)):
        tick(legs[0][0])
    wall_open = None  # opening when the walker's front reached the wall face
    for a, b in legs:
        steps = int(abs(b - a) / args.speed * hz)
        for i in range(steps + 1):
            n = a + (b - a) * i / steps
            front = abs(n) - SY / 2 - w.thickness / 2
            if wall_open is None and front <= 0.0:
                wall_open = opening()
            tick(n)
    for _ in range(int(args.wait * 2.5 * hz)):
        tick(legs[-1][1])
    last = opening()
    timeline.stop()
    travel = info.get("travel", (w.interval(o)[1] - w.interval(o)[0]) / 2)
    peak = max(x[2] for x in log)
    r = {"door": o.id, "wall": w.id, "travel_m": travel, "start_m": round(first, 3),
         "at_wall_m": round(wall_open, 3) if wall_open is not None else None, "peak_m": round(peak, 3),
         "end_m": round(last, 3), "log": log}
    r["ok"] = first < 0.02 and (wall_open or 0) > SX / 2 and peak > 0.9 * travel and last < 0.03
    results.append(r)
    gif = rec.gif(args.gif)
    if gif:
        r["gif"] = gif
    stage.RemovePrim(path)

report = {"usd": args.usd, "walker_m": [SX, SY, SZ], "speed": args.speed, "doors": results,
          "ok": all(r["ok"] for r in results)}
text = json.dumps(report, indent=1)
print(text)
if args.report:
    with open(args.report, "w") as f:
        f.write(text)
app.close()
