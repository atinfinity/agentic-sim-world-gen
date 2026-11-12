"""Probe how a room with its site (Room.site) looks in Isaac Sim RTX (headless).

For each variant it renders three views and measures the luma (0..255, sRGB, 15 px square mean) at given floor
points (`--point name,x,y,z`, default: the room's floor centre, a sidewalk and the road of a south-facing shop):
  dollhouse  check's view (ceiling and camera-side walls hidden)
  outside    eye height on the sidewalk, looking into the room through its south opening
  inside     eye height in the room, looking out of the south opening
Variants: hide = invisible (MakeInvisible, as check does) / camera (primvars:hideForCamera: hidden from the camera
but still casting shadows), sun = factor on the site's dome and sun.

    $ISAACSIM_PYTHON scripts/isaac/site_exposure_probe.py <scene>/scene.usda --out <dir> --sun 1 0.5 0.25

Writes <dir>/<variant>_<view>.png and <dir>/probe.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("usd")
ap.add_argument("--out", required=True)
ap.add_argument("--sun", type=float, nargs="+", default=[1.0, 0.5, 0.25])
ap.add_argument("--hide", nargs="+", default=["invisible", "camera"])
ap.add_argument("--point", action="append", default=[], help="name,x,y,z")
ap.add_argument("--frames", type=int, default=90)
args = ap.parse_args()

from isaacsim import SimulationApp  # noqa: E402

W, H = 1280, 720
app = SimulationApp({"headless": True, "width": W, "height": H})

import omni.usd  # noqa: E402
from omni.kit.viewport.utility import get_active_viewport  # noqa: E402
from pxr import Gf, Sdf, Usd, UsdGeom  # noqa: E402
from view import capture, dollhouse_camera, wait_frames  # noqa: E402

ctx = omni.usd.get_context()
assert ctx.open_stage(args.usd), args.usd
for _ in range(600):
    app.update()
    if ctx.get_stage_loading_status()[2] == 0:
        break
stage = ctx.get_stage()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)

room = json.loads((Path(args.usd).parent / "scene.resolved.json").read_text())["spec"]["room"]
w, d = room["width"], room["depth"]
points = {"floor": (w / 2, d / 2, 0.0), "sidewalk": (w / 2, -2.0, 0.15), "road": (w / 2, -7.0, 0.0)}
for p in args.point:
    n, *xyz = p.split(",")
    points[n] = tuple(float(v) for v in xyz)

lights = {n: stage.GetPrimAtPath(f"/World/Lights/{n}") for n in ("Dome", "Sun")}
base = {n: p.GetAttribute("inputs:intensity").Get() for n, p in lights.items() if p}


def gprims(p):
    return [q for q in Usd.PrimRange(p) if q.IsA(UsdGeom.Gprim)]


def hide_for_camera(names: list[str], on: bool) -> None:
    root = stage.GetPrimAtPath("/World/Room")
    for n in names:
        p = root.GetChild(n)
        if not p:
            continue
        if on:
            UsdGeom.Imageable(p).MakeVisible()
        for q in gprims(p):
            UsdGeom.PrimvarsAPI(q).CreatePrimvar("hideForCamera", Sdf.ValueTypeNames.Bool).Set(on)


def set_camera(eye, target) -> None:
    cam = UsdGeom.Camera.Define(stage, "/World/PreviewCamera")
    m = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0, 0, 1)).GetInverse()
    xf = UsdGeom.Xformable(cam)
    xf.ClearXformOpOrder()
    xf.AddTransformOp().Set(m)
    cam.CreateFocalLengthAttr(18.0)
    get_active_viewport().camera_path = "/World/PreviewCamera"


def pixel(pt):
    cam = UsdGeom.Camera(stage.GetPrimAtPath("/World/PreviewCamera"))
    m = UsdGeom.Xformable(cam).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).GetInverse()
    x, y, z = m.Transform(Gf.Vec3d(*pt))
    if z >= -0.01:
        return None
    f = cam.GetFocalLengthAttr().Get()
    ha = cam.GetHorizontalApertureAttr().Get()
    u = (f * x / -z) / (ha / 2)
    v = (f * y / -z) / (ha / 2 * H / W)
    px, py = (u + 1) / 2 * W, (1 - v) / 2 * H
    return (px, py) if 7 <= px < W - 7 and 7 <= py < H - 7 else None


def luma(path, at):
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
    y = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    res = {"mean": round(float(y.mean()), 1), "clipped": round(float((y >= 250).mean()), 3)}
    for n, px in at.items():
        if px:
            i, j = int(px[1]), int(px[0])
            res[n] = round(float(y[i - 7:i + 8, j - 7:j + 8].mean()), 1)
            res[n + "_rgb"] = [round(float(c), 0) for c in a[i - 7:i + 8, j - 7:j + 8].reshape(-1, 3).mean(0)]
    return res


report = {"base": base, "points": points, "runs": []}
for hide in args.hide:
    for s in args.sun:
        for n, p in lights.items():
            if p:
                p.GetAttribute("inputs:intensity").Set(base[n] * s)
        tag = f"{hide}_sun{s:g}"
        views = {}
        hidden = dollhouse_camera(stage)
        walls = [h for h in hidden if not h.startswith("Objects/")]
        hide_for_camera(walls, hide == "camera")
        for view, cam in (("dollhouse", None),
                          ("outside", ((w / 2, -2.5, 1.6), (w / 2, d * 0.6, 1.0))),
                          ("inside", ((w / 2, d - 1.0, 1.6), (w / 2, -3.0, 0.8)))):
            if cam:
                hide_for_camera(walls, False)
                for n in walls:  # every wall and the ceiling back for the eye-level views
                    UsdGeom.Imageable(stage.GetPrimAtPath(f"/World/Room/{n}")).MakeVisible()
                set_camera(*cam)
            wait_frames(app, args.frames)
            path = out / f"{tag}_{view}.png"
            capture(str(path))
            for _ in range(600):  # the capture is written a few frames later
                app.update()
                if path.exists() and path.stat().st_size > 0:
                    break
            wait_frames(app, 5)
            views[view] = luma(path, {n: pixel(pt) for n, pt in points.items()})
        report["runs"].append({"hide": hide, "sun": s, "views": views})
        print(tag, json.dumps(views), flush=True)
(out / "probe.json").write_text(json.dumps(report, indent=1))
app.close()
