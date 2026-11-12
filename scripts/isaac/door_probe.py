"""Probe the doors of a floor-plan scene in Isaac Sim (headless): a robot-sized test cube slides through
each door, and a control cube slides into a solid part of the same wall. Cubes are added in memory only.

  door:<id>     launched along the wall normal at the door center   -> must end up on the other side
  control:<id>  same, at the middle of the longest solid part        -> must stay on its side (wall collider)

Door positions come from scene.resolved.json next to the USD (agentic-sim-world-gen is imported from src/; it needs
only pydantic, which Isaac Sim's Python has; no usd-core). Use a scene without furniture in the way:
    $ISAACSIM_PYTHON scripts/isaac/door_probe.py out/plan_empty/scene.usda --report out/plan_empty/doors.json \
        --frames out/plan_empty/frames --gif out/plan_empty/doors.gif

Verified on Isaac Sim 6.1.0 (pip), 2026-10-25.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("usd")
ap.add_argument("--size", type=float, default=0.3, help="[m] test cube edge (robot proxy)")
ap.add_argument("--speed", type=float, default=3.0, help="[m/s] launch speed")
ap.add_argument("--start", type=float, default=0.6, help="[m] start distance from the wall centerline")
ap.add_argument("--seconds", type=float, default=3.0)
ap.add_argument("--report")
ap.add_argument("--frames", help="save PNG frames of the run in this folder")
ap.add_argument("--gif", help="assemble the frames into this GIF (needs ffmpeg)")
args = ap.parse_args()

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from agentic_sim_world_gen.layout.room import RoomGeometry  # noqa: E402
from agentic_sim_world_gen.scene.spec import ResolvedScene  # noqa: E402

scene = ResolvedScene.load(Path(args.usd).parent / "scene.resolved.json")
geom = RoomGeometry(scene.spec.room)

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": 1280, "height": 720})

import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, UsdGeom, UsdPhysics  # noqa: E402
from view import FrameRecorder, dollhouse_camera, wait_frames  # noqa: E402

ctx = omni.usd.get_context()
assert ctx.open_stage(args.usd), f"failed to open {args.usd}"
for _ in range(300):
    app.update()
    if ctx.get_stage_loading_status()[2] == 0:
        break
stage = ctx.get_stage()
S = args.size


def cube(name: str, pos, vel, color) -> str:
    path = f"/World/Probe/{name.replace(':', '_')}"
    c = UsdGeom.Cube.Define(stage, path)
    c.CreateSizeAttr(S)
    c.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    UsdGeom.XformCommonAPI(c).SetTranslate(Gf.Vec3d(*pos))
    p = c.GetPrim()
    UsdPhysics.CollisionAPI.Apply(p)
    UsdPhysics.RigidBodyAPI.Apply(p).CreateVelocityAttr(Gf.Vec3f(*vel))
    UsdPhysics.MassAPI.Apply(p).CreateMassAttr(10.0)
    return path


probes = []  # (name, wall, u, expect_pass, path)
for w in geom.walls:
    doors = [o for o in w.openings if o.kind in ("door", "opening")]
    if not doors:
        continue
    nx, ny = w.normal
    z = S / 2 + 0.005
    for o in doors:
        u0, u1 = w.interval(o)
        if u1 - u0 < S + 0.05:
            print(f"skip {o.id}: {u1 - u0:.2f} m is narrower than the cube")
            continue
        um = (u0 + u1) / 2
        x, y = w.at(um, args.start)
        probes.append((f"door:{o.id}", w, um, True,
                       cube(f"door:{o.id}", (x, y, z), (-nx * args.speed, -ny * args.speed, 0), (0.15, 0.65, 0.25))))
    solid = max((p for p in w.pieces() if p[4] == "wall"), key=lambda p: p[1] - p[0])
    uc = (solid[0] + solid[1]) / 2
    x, y = w.at(uc, args.start)
    probes.append((f"control:{w.id}", w, uc, False,
                   cube(f"control:{w.id}", (x, y, z), (-nx * args.speed, -ny * args.speed, 0), (0.85, 0.2, 0.15))))

hidden = dollhouse_camera(stage)
wait_frames(app, 30)
rec = FrameRecorder(args.frames)

from omni.physx import get_physx_interface  # noqa: E402

phx = get_physx_interface()
timeline = omni.timeline.get_timeline_interface()
timeline.play()
for _ in range(int(args.seconds * 60)):
    app.update()
    rec.tick()
results = []
for name, w, u, expect_pass, path in probes:
    t = phx.get_rigidbody_transformation(path)
    pos = tuple(t["position"]) if t.get("ret_val") else None
    # signed distance from the wall centerline (start side is +)
    n = (pos[0] - w.a[0]) * w.normal[0] + (pos[1] - w.a[1]) * w.normal[1] if pos else None
    passed = n is not None and n < -(w.thickness / 2 + S / 2) + 0.01
    stayed = n is not None and n > w.thickness / 2 + S / 2 - 0.01
    results.append({"probe": name, "wall": w.id, "end_n_m": round(n, 3) if n is not None else None,
                    "expected": "pass" if expect_pass else "blocked",
                    "ok": passed if expect_pass else stayed})
timeline.pause()

report = {"usd": args.usd, "cube_m": S, "speed": args.speed, "probes": results, "ok": all(r["ok"] for r in results)}
gif = rec.gif(args.gif)
if gif:
    report["gif"] = gif
text = json.dumps(report, indent=2)
print(text)
if args.report:
    with open(args.report, "w") as f:
        f.write(text)
app.close()
