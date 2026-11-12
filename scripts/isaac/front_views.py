"""Render the objects of a scene from their front, as a labeled contact sheet (headless).

compose writes customData agentic_sim_world_gen:facing_deg on each object prim: the world direction the
asset's front points to (layout/frame.py: +X of the asset unless listed as an exception). Each object is shot alone (the other
objects, the walls and the ceiling are hidden) by a camera placed on that side, slightly above.
If a tile shows the back of a chair or the back panel of a cabinet, the convention is wrong for
that asset: set front_offset_deg (e.g. 180) on it. --targets limits the objects (agentic-sim-world-gen fronts / view_fronts
leave out fill items, i.e. products on shelves, unless asked). --around shoots 4 sides (front, +90, +180, +270
counter-clockwise) to find which side the front is on.

    $ISAACSIM_PYTHON scripts/isaac/front_views.py out/study/scene.usda --out out/study/front_views.png

view_fronts opens front_views_stage.usda, a layer over the scene with the objects not shot deactivated, so
their assets are not loaded (service.front_stage). Verified on Isaac Sim 6.1.0 (pip), 2026-10-25, again 2026-11-09.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

T0 = time.monotonic()
seconds = {}  # where the time goes


def lap(name):
    seconds[name] = round(time.monotonic() - T0 - sum(seconds.values()), 1)

ap = argparse.ArgumentParser()
ap.add_argument("usd")
ap.add_argument("--out", required=True, help="contact sheet PNG")
ap.add_argument("--tile", type=int, default=360, help="tile width [px]")
ap.add_argument("--cols", type=int, default=6)
ap.add_argument("--around", action="store_true",
                help="4 tiles per object: from the assumed front, then +90, 180, 270 deg (counter-clockwise)")
ap.add_argument("--targets", help="JSON list of object ids (paths under /World/Objects) to shoot; default: all")
args = ap.parse_args()

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": 960, "height": 720})
lap("start")

import omni.usd  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402  (bundled with Isaac Sim)
from pxr import Gf, Usd, UsdGeom  # noqa: E402
from view import capture, hide_guides, wait_frames  # noqa: E402

ctx = omni.usd.get_context()
assert ctx.open_stage(args.usd), f"failed to open {args.usd}"
for _ in range(300):
    app.update()
    if ctx.get_stage_loading_status()[2] == 0:
        break
stage = ctx.get_stage()
lap("open")

objs = []  # (prim, facing_deg)
root = stage.GetPrimAtPath("/World/Objects")
for p in Usd.PrimRange(root):
    f = p.GetCustomDataByKey("agentic_sim_world_gen:facing_deg")
    if f is not None:
        objs.append((p, float(f)))
if args.targets:  # chosen by service.front_targets (fill items left out by default)
    keep = {"/World/Objects/" + i for i in json.loads(Path(args.targets).read_text())}
    shoot = [(p, f) for p, f in objs if str(p.GetPath()) in keep]
else:
    shoot = objs
room = stage.GetPrimAtPath("/World/Room")
for p in room.GetChildren() if room else []:
    if p.GetName() != "Floor":
        UsdGeom.Imageable(p).MakeInvisible()

from omni.kit.viewport.utility import get_active_viewport  # noqa: E402

cam = UsdGeom.Camera.Define(stage, "/World/FrontCamera")
cam.CreateFocalLengthAttr(24.0)
cam_op = UsdGeom.Xformable(cam).AddTransformOp()
get_active_viewport().camera_path = "/World/FrontCamera"
hide_guides()  # the grid showed in the tiles shot after its extension loaded

out = Path(args.out)
tiles_dir = out.with_name(out.stem + "_tiles")
tiles_dir.mkdir(parents=True, exist_ok=True)
for old in tiles_dir.glob("*.png"):
    old.unlink()
report = []
for prim, facing in shoot:
    for q, _ in objs:
        img = UsdGeom.Imageable(q)
        img.MakeVisible() if q == prim else img.MakeInvisible()
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    rng = bc.ComputeWorldBound(prim).ComputeAlignedRange()
    c, sz = rng.GetMidpoint(), rng.GetSize()
    r = max(sz[0], sz[1], sz[2], 0.3)
    dist = 1.3 * r / math.tan(math.radians(18)) * 0.5 + 0.3  # fits ~36 deg vertical FOV
    # from the front (20 deg to its right so the depth reads; straight on with --around), a bit above
    for k, off in enumerate((0, 90, 180, 270) if args.around else (20,)):
        side = math.radians(facing + off)
        eye = Gf.Vec3d(c[0] + dist * math.cos(side), c[1] + dist * math.sin(side), c[2] + 0.45 * dist)
        cam_op.Set(Gf.Matrix4d().SetLookAt(eye, Gf.Vec3d(*c), Gf.Vec3d(0, 0, 1)).GetInverse())
        wait_frames(app, 120 if not report else 40)  # the first: let textures stream in (render_view.py)
        f = tiles_dir / f"{prim.GetName()}{'_' + str(off) if args.around else ''}.png"
        capture(f)
        wait_frames(app, 10)
        report.append({"id": str(prim.GetPath()).removeprefix("/World/Objects/"),
                       "label": prim.GetCustomDataByKey("agentic_sim_world_gen:label") or "", "facing_deg": facing,
                       "view": "front" if not args.around else (f"+{off}" if off else "front"),
                       "uri": prim.GetCustomDataByKey("agentic_sim_world_gen:uri"), "tile": str(f)})

lap("shoot")
# captures are written a few frames later: wait for the last ones
for _ in range(300):
    if all(Path(rec["tile"]).exists() for rec in report):
        break
    app.update()
wait_frames(app, 10)

# contact sheet
w = args.tile
h = w * 3 // 4
cols = 4 if args.around else min(args.cols, max(1, len(report)))
rows = math.ceil(len(report) / cols)
sheet = Image.new("RGB", (cols * w, rows * (h + 22)), "white")
draw = ImageDraw.Draw(sheet)
for i, rec in enumerate(report):
    x, y = (i % cols) * w, (i // cols) * (h + 22)
    try:
        sheet.paste(Image.open(rec["tile"]).convert("RGB").resize((w, h)), (x, y + 22))
    except OSError:
        pass
    name = rec["uri"].rsplit("/", 1)[-1] if rec["uri"] else ""
    draw.text((x + 4, y + 4), f"{rec['id']}  {rec['view']}  {name}", fill="black")
sheet.save(out)
out.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
lap("save")
print(json.dumps({"sheet": str(out), "objects": len(report), "seconds": seconds}))
app.close()
