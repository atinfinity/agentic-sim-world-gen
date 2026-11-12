"""Render a scene's room look (materials, lighting) in Isaac Sim RTX (headless) and measure brightness.

Two views per scene:
  dollhouse  above the south-west corner, ceiling and near walls hidden (same as check_scene.py)
  interior   eye height (1.5 m) in the south-west corner, looking at the far corner, ceiling shown
             (outdoor: the site's south-west corner, so the sky is in view)

For each image the report has the mean / 5th / 95th percentile of the luma (0..255, sRGB) and the
share of clipped (>= 250) and black (<= 5) pixels, plus the RTX tonemapper / exposure settings.

    $ISAACSIM_PYTHON scripts/isaac/look_views.py out/looks/home/scene.usda --out out/looks/home/look

Writes <out>_dollhouse.png, <out>_interior.png and <out>.json. Verified on Isaac Sim 6.1.0 (pip), 2026-10-25.
"""

from __future__ import annotations

import argparse
import json

ap = argparse.ArgumentParser()
ap.add_argument("usd")
ap.add_argument("--out", required=True, help="output prefix")
ap.add_argument("--frames", type=int, default=120, help="frames to wait before each capture")
args = ap.parse_args()

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": 1280, "height": 720})

import carb.settings  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Usd, UsdGeom  # noqa: E402
from view import capture, dollhouse_camera, show, wait_frames  # noqa: E402

ctx = omni.usd.get_context()
assert ctx.open_stage(args.usd), f"failed to open {args.usd}"
for _ in range(600):
    app.update()
    if ctx.get_stage_loading_status()[2] == 0:
        break
stage = ctx.get_stage()


def stats(path: str) -> dict:
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
    y = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    return {"mean": round(float(y.mean()), 1), "p5": round(float(np.percentile(y, 5)), 1),
            "p95": round(float(np.percentile(y, 95)), 1),
            "clipped": round(float((y >= 250).mean()), 3), "black": round(float((y <= 5).mean()), 3)}


def shoot(name: str) -> dict:
    wait_frames(app, args.frames)
    path = f"{args.out}_{name}.png"
    capture(path)
    wait_frames(app, 10)
    return {"image": path}  # stats after all captures: the file is written a few frames later


report = {"usd": args.usd}
settings = carb.settings.get_settings()
report["rtx"] = {k: settings.get(k) for k in (
    "/rtx/post/tonemap/op", "/rtx/post/tonemap/filmIso", "/rtx/post/tonemap/cameraShutter",
    "/rtx/post/tonemap/fNumber", "/rtx/post/histogram/enabled", "/rtx/rendermode")}

# dollhouse
hidden = dollhouse_camera(stage)
report["dollhouse"] = shoot("dollhouse")
for name in hidden:
    path = f"/World/{name}" if name.startswith("Objects/") else f"/World/Room/{name}"  # doors of hidden walls
    show(stage.GetPrimAtPath(path))

# interior: camera in the south-west corner at eye height, looking at the far corner
site = stage.GetPrimAtPath("/World/Room").GetCustomDataByKey("agentic_sim_world_gen:site")
floor = stage.GetPrimAtPath("/World/Room/Floor")
indoor = any(p.GetName().startswith("Wall_") for p in stage.GetPrimAtPath("/World/Room").GetChildren())
if site is not None and not indoor:  # outdoor: the site's south-west corner, the sky in view
    x0, y0, x1, y1 = list(site)
    z, t = 0.0, 0.0
else:
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    rng = cache.ComputeWorldBound(floor).ComputeAlignedRange()
    (x0, y0, _), (x1, y1, z) = rng.GetMin(), rng.GetMax()
    t = 0.15  # stay inside the walls (the slab reaches under them)
eye = Gf.Vec3d(x0 + 2 * t + 0.1, y0 + 2 * t + 0.1, z + 1.5)
target = Gf.Vec3d(x1 - t, y1 - t, z + 0.9)
cam = UsdGeom.Camera.Define(stage, "/World/InteriorCamera")
xf = UsdGeom.Xformable(cam)
xf.ClearXformOpOrder()
xf.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye, target, Gf.Vec3d(0, 0, 1)).GetInverse())
cam.CreateFocalLengthAttr(12.0)
cam.CreateClippingRangeAttr(Gf.Vec2f(0.05, 1000.0))
from omni.kit.viewport.utility import get_active_viewport  # noqa: E402

get_active_viewport().camera_path = "/World/InteriorCamera"
report["interior"] = shoot("interior")

views = ["dollhouse", "interior"]
if site is not None and indoor:  # a room with its surroundings: from the street south of it, at eye height
    sx0, sy0, sx1, sy1 = list(site)
    ey = max(sy0 + 1.0, y0 - 9.0)
    eye = Gf.Vec3d(x0 - 2.0, ey, 1.6)
    target = Gf.Vec3d((x0 + x1) / 2, y0, 1.4)
    cam = UsdGeom.Camera.Define(stage, "/World/ExteriorCamera")
    xf = UsdGeom.Xformable(cam)
    xf.ClearXformOpOrder()
    xf.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye, target, Gf.Vec3d(0, 0, 1)).GetInverse())
    cam.CreateFocalLengthAttr(14.0)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.05, 2000.0))
    get_active_viewport().camera_path = "/World/ExteriorCamera"
    report["exterior"] = shoot("exterior")
    views.append("exterior")

wait_frames(app, 30)
for view in views:
    report[view].update(stats(report[view]["image"]))
text = json.dumps(report, indent=2, ensure_ascii=False)
print(text)
with open(f"{args.out}.json", "w") as f:
    f.write(text)
app.close()
