"""Render a scene along a known camera path (headless) into frames, an mp4 and the true pose of every frame.

Ground truth for the video tools: video_pose measures how the camera turned between frames, fit_photos
fits the cameras to landmarks; on a rendered walkthrough both can be compared with the path that made it.

    $ISAACSIM_PYTHON scripts/isaac/render_path.py out/sdg_living/scene.usda --path path.json --out out/synth

path.json: {"fps": 15, "hfov_deg": 70, "size": [1280, 720],
            "keys": [{"t": 0, "position": [x, y, z], "yaw_deg": 90, "pitch_deg": 10}, ...]}
Room axes (x east, y north, z up); yaw 0 = east, 90 = north; pitch > 0 looks down; no roll. Between keys the
position, yaw and pitch change linearly. Writes <out>/frames/NNNN.png, <out>/walk.mp4 (ffmpeg) and
<out>/truth.json {fps, hfov_deg, size, frames [{i, t, position, yaw_deg, pitch_deg}]}.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("usd")
ap.add_argument("--path", required=True, help="camera path JSON (see the docstring)")
ap.add_argument("--out", required=True, help="output folder")
ap.add_argument("--settle", type=int, default=12, help="renderer updates per frame before the capture")
args = ap.parse_args()

spec = json.loads(Path(args.path).read_text())
fps, hfov = float(spec.get("fps", 15)), float(spec.get("hfov_deg", 70))
W, H = spec.get("size", [1280, 720])
keys = sorted(spec["keys"], key=lambda k: k["t"])
for a, b in zip(keys, keys[1:]):  # unwrap the yaw: the camera turns the short way between keys
    b["yaw_deg"] = a["yaw_deg"] + (b["yaw_deg"] - a["yaw_deg"] + 180) % 360 - 180


def pose(t: float) -> dict:
    for a, b in zip(keys, keys[1:]):
        if a["t"] <= t <= b["t"]:
            s = (t - a["t"]) / (b["t"] - a["t"])
            break
    else:
        a = b = keys[-1]
        s = 0.0
    lerp = lambda u, v: u + s * (v - u)  # noqa: E731
    return {"position": [lerp(u, v) for u, v in zip(a["position"], b["position"])],
            "yaw_deg": lerp(a["yaw_deg"], b["yaw_deg"]), "pitch_deg": lerp(a["pitch_deg"], b["pitch_deg"])}


n = int(round((keys[-1]["t"] - keys[0]["t"]) * fps)) + 1
frames = [{"i": i, "t": round(keys[0]["t"] + i / fps, 4), **pose(keys[0]["t"] + i / fps)} for i in range(n)]

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": W, "height": H})

import omni.usd  # noqa: E402
from omni.kit.viewport.utility import get_active_viewport  # noqa: E402
from pxr import Gf, UsdGeom  # noqa: E402
from view import capture, hide_guides, wait_frames  # noqa: E402

ctx = omni.usd.get_context()
assert ctx.open_stage(args.usd), f"failed to open {args.usd}"
for _ in range(300):
    app.update()
    if ctx.get_stage_loading_status()[2] == 0:
        break
stage = ctx.get_stage()
ceiling = stage.GetPrimAtPath("/World/Room/Ceiling")
if ceiling and ceiling.IsValid():  # keep the ceiling (check_scene hides it; a walkthrough should see it)
    UsdGeom.Imageable(ceiling).MakeVisible()

SENSOR_W_MM = 36.0
cam = UsdGeom.Camera.Define(stage, "/World/Cameras/path")
cam.CreateHorizontalApertureAttr(SENSOR_W_MM)
cam.CreateVerticalApertureAttr(SENSOR_W_MM * H / W)
cam.CreateFocalLengthAttr(SENSOR_W_MM / 2 / math.tan(math.radians(hfov) / 2))
cam.CreateClippingRangeAttr(Gf.Vec2f(0.05, 1000.0))
xf = UsdGeom.Xformable(cam)
xf.ClearXformOpOrder()
op = xf.AddTransformOp()


def place(f: dict) -> None:
    y, p = math.radians(f["yaw_deg"]), math.radians(f["pitch_deg"])
    eye = Gf.Vec3d(*f["position"])
    fw = Gf.Vec3d(math.cos(y) * math.cos(p), math.sin(y) * math.cos(p), -math.sin(p))
    op.Set(Gf.Matrix4d().SetLookAt(eye, eye + fw, Gf.Vec3d(0, 0, 1)).GetInverse())


vp = get_active_viewport()
hide_guides()
vp.resolution = (W, H)
vp.camera_path = "/World/Cameras/path"
out = Path(args.out)
fdir = out / "frames"
if fdir.exists():
    shutil.rmtree(fdir)
fdir.mkdir(parents=True)
place(frames[0])
wait_frames(app, 120)  # textures stream in
for f in frames:
    place(f)
    wait_frames(app, args.settle)
    png = fdir / f"{f['i']:04d}.png"
    capture(png)
    for _ in range(300):
        if png.exists():
            break
        app.update()
    wait_frames(app, 2)

truth = {"usd": str(Path(args.usd).resolve()), "fps": fps, "hfov_deg": hfov, "size": [W, H],
         "frames": [{**f, "position": [round(v, 4) for v in f["position"]], "yaw_deg": round(f["yaw_deg"], 3),
                     "pitch_deg": round(f["pitch_deg"], 3)} for f in frames]}
(out / "truth.json").write_text(json.dumps(truth, indent=1))
mp4 = out / "walk.mp4"
if shutil.which("ffmpeg"):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(fdir / "%04d.png"),
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(mp4)], check=True)
print(json.dumps({"frames": len(frames), "truth": str(out / "truth.json"), "video": str(mp4)}))
app.close()
