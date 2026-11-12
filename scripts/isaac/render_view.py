"""Render a scene from camera prims (headless) and, with --photo, put each photo and its render side by side.

compose writes /World/Cameras/<id> for every input photo with a camera (same field of view and aspect).
The viewport is sized to the photo's aspect ratio so the framing matches. The comparison image carries
the photo's credit line (customData agentic_sim_world_gen:inputs on /World).

    $ISAACSIM_PYTHON scripts/isaac/render_view.py out/living/scene.usda --camera /World/Cameras/photo \
        --out out/living/render_photo.png --photo examples/photos/living_room.jpg \
        --compare out/living/compare_photo.png --size 1600x1068

Several photos in one run (Isaac Sim starts once): --jobs jobs.json with
[{"camera": "/World/Cameras/<id>", "out": "render_<id>.png", "photo": "<file>", "size": [w, h]}, ...];
--compare then gets one row per photo.

Verified on Isaac Sim 6.1.0 (pip), 2026-10-25.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("usd")
ap.add_argument("--camera", help="camera prim path")
ap.add_argument("--out", help="render PNG")
ap.add_argument("--size", default="1600x1200", help="photo size WxH (only the aspect ratio is used)")
ap.add_argument("--width", type=int, default=1280, help="render width [px]")
ap.add_argument("--photo", help="photo to compare with")
ap.add_argument("--jobs", help="JSON list of {camera, out, photo, size}: several views in one run")
ap.add_argument("--compare", help="side-by-side PNG (needs photos)")
args = ap.parse_args()

if args.jobs:
    jobs = json.loads(Path(args.jobs).read_text())
else:
    assert args.camera and args.out, "--camera and --out (or --jobs) are required"
    jobs = [{"camera": args.camera, "out": args.out, "photo": args.photo,
             "size": [int(v) for v in args.size.lower().split("x")]}]
W = args.width
for j in jobs:
    pw, ph = j.get("size") or (1600, 1200)
    j["H"] = round(W * ph / pw / 2) * 2

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": W, "height": jobs[0]["H"]})

import omni.usd  # noqa: E402
from omni.kit.viewport.utility import get_active_viewport  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402  (bundled with Isaac Sim)
from view import capture, hide_guides, wait_frames  # noqa: E402

ctx = omni.usd.get_context()
assert ctx.open_stage(args.usd), f"failed to open {args.usd}"
for _ in range(300):
    app.update()
    if ctx.get_stage_loading_status()[2] == 0:
        break
stage = ctx.get_stage()
for j in jobs:
    assert stage.GetPrimAtPath(j["camera"]).IsValid(), f"no camera {j['camera']}"

vp = get_active_viewport()
hide_guides()
for k, j in enumerate(jobs):
    vp.resolution = (W, j["H"])
    vp.camera_path = j["camera"]
    wait_frames(app, 120 if k == 0 else 60)  # let textures stream in and the renderer converge
    out = Path(j["out"])
    if out.exists():
        out.unlink()
    capture(out)
    for _ in range(300):
        if out.exists():
            break
        app.update()
    wait_frames(app, 10)

result = {"renders": [{"camera": j["camera"], "render": j["out"], "size": [W, j["H"]]} for j in jobs]}
if len(jobs) == 1:
    result.update(render=jobs[0]["out"], camera=jobs[0]["camera"], size=[W, jobs[0]["H"]])


def font(size: int):
    """A TrueType font with Latin-extended/Hebrew/... glyphs for credit lines; PIL's default otherwise."""
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "NotoSans-Regular.ttf",
                 "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", "Arial Unicode.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


rows = [j for j in jobs if j.get("photo")]
if rows and args.compare:
    h = 540 if len(rows) == 1 else 360
    inputs = stage.GetPrimAtPath("/World").GetCustomDataByKey("agentic_sim_world_gen:inputs") or {}
    f_label, f_credit = font(15), font(13)
    blocks = []
    for j in rows:
        render = Image.open(j["out"]).convert("RGB")
        photo = Image.open(j["photo"]).convert("RGB")
        tiles = [photo.resize((round(h * photo.width / photo.height), h)),
                 render.resize((round(h * render.width / render.height), h))]
        cid = j["camera"].rsplit("/", 1)[-1]
        src = inputs.get(cid, {})
        credit = " / ".join(str(src[k]) for k in ("title", "author", "license") if src.get(k))
        lines = [f"photo: {credit}" if credit else "photo: source not recorded"] + ([str(src["url"])] if src.get("url") else [])
        top, bottom = 26, 8 + 18 * len(lines)
        b = Image.new("RGB", (sum(t.width for t in tiles) + 8, top + h + bottom), "white")
        draw = ImageDraw.Draw(b)
        x = 0
        for t, label in zip(tiles, (f"photo {cid}" if len(rows) > 1 else "photo", "agentic-sim-world-gen render (Isaac Sim)")):
            b.paste(t, (x, top))
            draw.text((x + 6, 5), label, fill="black", font=f_label)
            x += t.width + 8
        for k, line in enumerate(lines):
            draw.text((6, top + h + 5 + 18 * k), line, fill=(80, 80, 80), font=f_credit)
        blocks.append(b)
    sheet = Image.new("RGB", (max(b.width for b in blocks), sum(b.height for b in blocks) + 10 * (len(blocks) - 1)), "white")
    y = 0
    for b in blocks:
        sheet.paste(b, (0, y))
        y += b.height + 10
    sheet.save(args.compare)
    result["compare"] = args.compare
print(json.dumps(result))
app.close()
