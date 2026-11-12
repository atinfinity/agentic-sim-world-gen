"""Synthetic data with Replicator from a agentic-sim-world-gen scene (Isaac Sim python, headless).

The frames (camera poses, light scale / color temperature, floor/wall colors, floor/wall/ceiling materials)
come from plan.json,
written by agentic-sim-world-gen (agentic_sim_world_gen.sdg.plan), so this script only applies them and runs the writers:

    $ISAACSIM_PYTHON scripts/isaac/sdg.py out/study/scene.usda --plan out/study/sdg/plan.json \
        --out out/study/sdg --sheet out/study/sdg/sheet.jpg

Output: <out>/basic (BasicWriter: rgb, bbox, segmentation, depth ...), <out>/coco, <out>/kitti when asked,
<out>/summary.json (per-frame checks) and the contact sheet (RGB + 2D boxes, semantic segmentation).
Labels: compose writes UsdSemantics labels of taxonomy "class" on every object, the floor, walls, ceiling.

Verified on Isaac Sim 6.1.0 (pip), 2026-10-25.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import math
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("usd")
ap.add_argument("--plan", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--sheet", help="contact sheet image")
ap.add_argument("--sheet-frames", type=int, default=8)
ap.add_argument("--keep-part-labels", action="store_true", help="keep class labels that assets put on their parts")
args = ap.parse_args()

plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
W, H = plan["resolution"]

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": W, "height": H})

import carb.settings  # noqa: E402
import numpy as np  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade, Vt  # noqa: E402

SENSOR_W_MM = 36.0
CAM = "/World/SDG/Camera"

ctx = omni.usd.get_context()
assert ctx.open_stage(args.usd), f"failed to open {args.usd}"
for _ in range(600):
    app.update()
    if ctx.get_stage_loading_status()[2] == 0:
        break
stage = ctx.get_stage()
warnings = list(plan.get("warnings", []))

# --- one label per object -----------------------------------------------------------------------
# Some assets label their own parts with taxonomy "class" too (a monitor's Links/monitor and Links/base,
# a lamp's "lamp"), which splits one object into several boxes and makes "lamp,table lamp" classes.
# Blank those inner labels on this stage only (the scene file is not touched); other taxonomies stay.
def strip_part_labels(root: Usd.Prim) -> tuple[int, int]:
    removed = locked = 0
    for p in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
        if p == root:
            continue
        legacy = [s for s in p.GetAppliedSchemas() if s.startswith("SemanticsAPI:")
                  and p.GetAttribute(f"semantic:{s.split(':', 1)[1]}:params:semanticType").Get() == "class"]
        hits = (["SemanticsLabelsAPI:class"] if "SemanticsLabelsAPI:class" in p.GetAppliedSchemas() else []) + legacy
        if not hits:
            continue
        if p.IsInstanceProxy():
            locked += 1
            continue
        # change values, not schemas: Replicator reads labels from Fabric, which follows attribute edits only
        for s in hits:
            if s == "SemanticsLabelsAPI:class":
                p.GetAttribute("semantics:labels:class").Set(Vt.TokenArray([]))
            else:
                p.GetAttribute(f"semantic:{s.split(':', 1)[1]}:params:semanticType").Set("part_class")
        removed += 1
    return removed, locked


part_labels = [0, 0]
for path, cls in plan.get("classes", {}).items():
    prim = stage.GetPrimAtPath(path)
    if not prim:
        warnings.append(f"object prim missing: {path}")
        continue
    attr = prim.GetAttribute("semantics:labels:class")
    want = [cls] if cls else []  # "" = background (SdgSpec.classes "" / others="background")
    if attr and list(attr.Get() or []) != want:  # plan.json is authoritative (SdgSpec.classes)
        attr.Set(Vt.TokenArray(want))
    if not args.keep_part_labels:
        r, lk = strip_part_labels(prim)
        part_labels[0] += r
        part_labels[1] += lk
    if part_labels[1]:
        warnings.append(f"{part_labels[1]} part label(s) inside instanceable assets could not be removed")

# --- what the frames change -------------------------------------------------------------------
lights = []
for p in Usd.PrimRange(stage.GetPrimAtPath("/World/Lights")):
    if p.HasAPI(UsdLux.LightAPI) or p.IsA(UsdLux.BoundableLightBase) or p.IsA(UsdLux.NonboundableLightBase):
        lapi = UsdLux.LightAPI(p)
        lights.append((lapi, lapi.GetIntensityAttr().Get() or 0.0))
if not lights:
    warnings.append("no lights under /World/Lights")


def surface_color(name: str):
    """(input, base color) that sets the room material's color: diffuseColor, or for a texture its `scale`
    (a multiplier; base = the preset's average color written on diffuseColor). None if there is none."""
    sh = UsdShade.Shader.Get(stage, f"/World/Room/Looks/{name}/PreviewSurface")
    if not sh:
        return None
    inp = sh.GetInput("diffuseColor")
    if not inp:
        return None
    if stage.GetPrimAtPath(f"/World/Room/Looks/{name}/MDL"):
        warnings.append(f"{name} uses MDL: color randomization has no effect in RTX")
    if not inp.HasConnectedSource():
        return inp, None
    tex = UsdShade.Shader.Get(stage, f"/World/Room/Looks/{name}/DiffuseTexture")
    base = inp.Get()
    if not tex or base is None:
        return None
    return tex.CreateInput("scale", Sdf.ValueTypeNames.Float4), base


STUFF = plan.get("stuff", ["floor", "wall", "ceiling"])  # room labels, not objects ([] = left unlabelled)
colors = {"floor_color": surface_color("Floor"), "wall_color": surface_color("Wall")}
if any(k in f and colors[k] is None for f in plan["frames"] for k in colors):
    warnings.append("floor/wall without a PreviewSurface color: their colors are not randomized")

# floor / walls / ceiling material swaps: compose built the candidates under /World/Room/Looks/SDG; a frame
# points every prim of that surface (found by its label) at one of them, or back at its own material.
ROOM_LABELS = {"floor": {"floor", "ground"}, "walls": {"wall"}, "ceiling": {"ceiling"}}
SDG_LOOKS = "/World/Room/Looks/SDG"


def room_label(p: Usd.Prim) -> str | None:
    while p and str(p.GetPath()).startswith("/World/Room"):
        a = p.GetAttribute("semantics:labels:class")
        if a and a.Get():
            return list(a.Get())[0]
        p = p.GetParent()
    return None


mat_cands = plan.get("materials") or {}
bound: dict[str, list] = {t: [] for t in mat_cands}
if mat_cands:
    for p in Usd.PrimRange(stage.GetPrimAtPath("/World/Room")):
        rel = UsdShade.MaterialBindingAPI(p).GetDirectBindingRel()
        tg = rel.GetTargets() if rel else []
        if not tg or str(tg[0]).startswith(SDG_LOOKS):
            continue
        lab = room_label(p)
        for t in bound:
            if lab in ROOM_LABELS[t]:
                bound[t].append((rel, tg[0]))
    for t, cands in mat_cands.items():
        if not bound[t]:
            warnings.append(f"no {t} prims bound to a material: {t} materials are not randomized")
        for c in cands:
            if c["path"] and not stage.GetPrimAtPath(c["path"]):
                warnings.append(f"material candidate missing (recompose the scene): {c['path']}")


def set_materials(sel: dict) -> None:
    for t, v in sel.items():
        c = mat_cands[t][v["index"]]
        for rel, own in bound.get(t, []):
            rel.SetTargets([Sdf.Path(c["path"]) if c["path"] else own])


UsdGeom.Scope.Define(stage, "/World/SDG")
cam = UsdGeom.Camera.Define(stage, CAM)
cam.CreateHorizontalApertureAttr(SENSOR_W_MM)
cam.CreateVerticalApertureAttr(SENSOR_W_MM * H / W)
cam.CreateClippingRangeAttr(Gf.Vec2f(0.05, 1000.0))
cam_xf = UsdGeom.Xformable(cam)
cam_xf.ClearXformOpOrder()
cam_op = cam_xf.AddTransformOp()


def apply(f: dict) -> None:
    c = f["camera"]
    eye, at = Gf.Vec3d(*c["position"]), Gf.Vec3d(*c["look_at"])
    cam_op.Set(Gf.Matrix4d().SetLookAt(eye, at, Gf.Vec3d(0, 0, 1)).GetInverse())
    cam.GetFocalLengthAttr().Set(SENSOR_W_MM / 2 / math.tan(math.radians(c["hfov_deg"]) / 2))
    for lapi, base in lights:
        if "light_scale" in f:
            lapi.GetIntensityAttr().Set(base * f["light_scale"])
        if "color_temp_k" in f:
            lapi.CreateEnableColorTemperatureAttr().Set(True)
            lapi.CreateColorTemperatureAttr().Set(float(f["color_temp_k"]))
    set_materials(f.get("materials", {}))
    for k, c in colors.items():
        if c is None or k not in f:
            continue
        inp, base = c
        if base is None:
            inp.Set(Gf.Vec3f(*f[k]))
        else:  # tint the texture so its average becomes the drawn color
            inp.Set(Gf.Vec4f(*(min(4.0, v / max(b, 1e-3)) for v, b in zip(f[k], base)), 1.0))


# --- Replicator ---------------------------------------------------------------------------------
carb.settings.get_settings().set("rtx/post/dlss/execMode", 2)  # DLSS Quality: fewer edge artifacts
rep.orchestrator.set_capture_on_play(False)
rep.set_global_seed(int(plan.get("seed", 0)))
rp = rep.create.render_product(CAM, (W, H), name="sdg")
out = Path(args.out).resolve()  # Replicator puts relative dirs under ~/omni.replicator_out
writers = []
basic = rep.WriterRegistry.get("BasicWriter")
basic.initialize(output_dir=str(out / "basic"), semantic_types=["class"], colorize_semantic_segmentation=True,
                 colorize_instance_segmentation=True, **{a: True for a in plan["annotators"]})
basic.attach(rp)
writers.append(basic)
# SdgSpec.structure false: floor / walls / ceiling are background. After the material lookup above,
# which finds the room's prims by these labels.
if plan.get("blank_structure"):
    blanked = 0
    for p in Usd.PrimRange(stage.GetPrimAtPath("/World/Room")):
        a = p.GetAttribute("semantics:labels:class")
        if a and a.Get():
            a.Set(Vt.TokenArray([]))
            blanked += 1
    if not blanked:
        warnings.append("blank_structure: no labelled prims under /World/Room")

# Writers sharing a render product share its annotators and break each other: BasicWriter colorizes the
# segmentation and COCO then crashes (the orchestrator hangs); COCO + KITTI on one product made COCO append
# thousands of image entries. -> each dataset writer gets its own render product of the same camera.
if "coco" in plan["writers"]:
    # our classes, not the fixed COCO list (anything not in it would become "other")
    names = sorted(set(plan["classes"].values()) - set(STUFF) - {""}) + STUFF
    cats = {}
    for i, n in enumerate(dict.fromkeys(names)):
        rgb = [int(c * 255) for c in colorsys.hsv_to_rgb((i * 0.618) % 1.0, 0.65, 0.95)]
        cats[n] = {"name": n, "id": i + 1, "supercategory": "structure" if n in STUFF
                   else "object", "color": tuple(rgb), "isthing": int(n not in STUFF)}
    w = rep.WriterRegistry.get("CocoWriter")
    w.initialize(output_dir=str(out / "coco"), semantic_types=["class"], coco_categories=cats,
                 dataset_id=plan.get("scene", "agentic-sim-world-gen"))
    w.attach(rep.create.render_product(CAM, (W, H), name="sdg_coco"))
    writers.append(w)
if "kitti" in plan["writers"]:
    w = rep.WriterRegistry.get("KittiWriter")
    w.initialize(output_dir=str(out / "kitti"), semantic_types=["class"], omit_semantic_type=True)
    w.attach(rep.create.render_product(CAM, (W, H), name="sdg_kitti"))
    writers.append(w)

# warm up: textures stream in and shaders compile before the first captured frame (every material candidate)
for k in range(max((len(c) for c in mat_cands.values()), default=0)):
    set_materials({t: {"index": k % len(c)} for t, c in mat_cands.items()})
    for _ in range(40):
        app.update()
apply(plan["frames"][0])
for _ in range(60):
    app.update()

t0 = time.time()
for f in plan["frames"]:
    apply(f)
    rep.orchestrator.step(delta_time=0.0, rt_subframes=int(plan["rt_subframes"]))
rep.orchestrator.wait_until_complete()
elapsed = time.time() - t0
for w in writers:
    w.detach()
rp.destroy()

# KITTI labels are space separated: "rack with shelves" -> "rack_with_shelves" (the other 14 fields are numbers)
for txt in sorted((out / "kitti").glob("**/object_detection/*.txt")):
    rows = []
    for line in txt.read_text().splitlines():
        t = line.split()
        if not t:
            continue
        if len(t) > 15:
            t = ["_".join(t[:-14])] + t[-14:]
        t[0] = t[0].strip("\"'")
        rows.append(" ".join(t))
    txt.write_text("\n".join(rows) + ("\n" if rows else ""))

# --- checks and contact sheet -------------------------------------------------------------------
from PIL import Image, ImageDraw, ImageFont  # noqa: E402  (bundled with Isaac Sim)

bdir = out / "basic"


def font(size: int):
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def find(stem: str, i: int, ext: str) -> Path | None:
    for pad in (4, 1):
        p = bdir / f"{stem}_{i:0{pad}d}.{ext}"
        if p.exists():
            return p
    return None


def labels_of(raw: dict) -> dict:
    """{id: class} from a *_labels json ({"0": {"class": "chair"}} or {"(r,g,b,a)": {"class": ...}})."""
    return {k: (v.get("class") if isinstance(v, dict) else v) for k, v in raw.items()}


frames, classes_seen = [], {}
for i, f in enumerate(plan["frames"]):
    rec = {"frame": i, "target": f["camera"]["target"]}
    if "materials" in f:
        rec["materials"] = {t: v["name"] for t, v in f["materials"].items()}
    rgb = find("rgb", i, "png")
    if rgb is None:
        rec["error"] = "no rgb"
        frames.append(rec)
        continue
    a = np.asarray(Image.open(rgb).convert("RGB"), dtype=np.float32)
    rec["mean_rgb"] = round(float(a.mean()), 1)
    bb = find("bounding_box_2d_tight", i, "npy")
    if bb is not None:
        boxes = np.load(bb)
        lab = labels_of(json.loads(find("bounding_box_2d_tight_labels", i, "json").read_text()))
        names = [lab.get(str(int(b["semanticId"])), "?") for b in boxes]
        rec["boxes"] = len(boxes)
        rec["box_classes"] = sorted(set(names))
        for n in names:
            classes_seen[n] = classes_seen.get(n, 0) + 1
    sem = find("semantic_segmentation", i, "png")
    if sem is not None:
        s = np.asarray(Image.open(sem).convert("RGBA"))
        lab = labels_of(json.loads(find("semantic_segmentation_labels", i, "json").read_text()))
        rec["labeled_px"] = round(float(((s[..., 3] > 0) & (s[..., :3].sum(-1) > 0)).mean()), 3)
        rec["sem_classes"] = sorted({v for v in lab.values() if v and v not in ("BACKGROUND", "UNLABELLED")})
    d = find("distance_to_image_plane", i, "npy")
    if d is not None:
        dep = np.load(d)
        fin = dep[np.isfinite(dep)]
        rec["depth_m"] = [round(float(fin.min()), 2), round(float(np.median(fin)), 2)] if fin.size else None
    rec["ok"] = rec["mean_rgb"] > 15 and rec.get("boxes", 1) > 0
    frames.append(rec)

mat_use: dict[str, dict[str, int]] = {}
for f in plan["frames"]:
    for t, v in f.get("materials", {}).items():
        mat_use.setdefault(t, {})[v["name"]] = mat_use.get(t, {}).get(v["name"], 0) + 1
# per-frame metadata next to the datasets: what the frame changed (camera, lights, room colors, materials)
(out / "frames.json").write_text(json.dumps([{"frame": i, **f} for i, f in enumerate(plan["frames"])], indent=1,
                                            ensure_ascii=False), encoding="utf-8")

summary = {
    "frames": len(plan["frames"]),
    "written": sum("mean_rgb" in r for r in frames),
    "ok": sum(bool(r.get("ok")) for r in frames),
    "seconds": round(elapsed, 1),
    "classes": dict(sorted(classes_seen.items(), key=lambda kv: -kv[1])),
    "part_labels_removed": part_labels[0],
    **({"materials": mat_use} if mat_use else {}),
    "writers": {w: sorted(p.name for p in (out / w).rglob("*") if p.is_file())[:3] + ["..."] for w in plan["writers"]
                if (out / w).exists()},
    "warnings": warnings,
    "per_frame": frames,
}
(out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

if args.sheet:
    n = min(args.sheet_frames, len(plan["frames"]))
    pick = [round(k * (len(plan["frames"]) - 1) / max(n - 1, 1)) for k in range(n)] if n > 1 else [0]
    tw = 400
    th = round(tw * H / W)
    cols = 2  # RGB+boxes | semantic segmentation, one frame per row pair; 2 frames per row
    rows = math.ceil(len(pick) / 2)
    sheet = Image.new("RGB", (tw * 2 * cols + 8 * (cols - 1), (th + 20) * rows), "white")
    draw = ImageDraw.Draw(sheet)
    fs = font(12)
    for k, i in enumerate(pick):
        x0, y0 = (k % cols) * (tw * 2 + 8), (k // cols) * (th + 20)
        rgb = find("rgb", i, "png")
        if rgb is None:
            continue
        im = Image.open(rgb).convert("RGB")
        sx, sy = tw / im.width, th / im.height
        im = im.resize((tw, th))
        dr = ImageDraw.Draw(im)
        bb = find("bounding_box_2d_tight", i, "npy")
        if bb is not None:
            lab = labels_of(json.loads(find("bounding_box_2d_tight_labels", i, "json").read_text()))
            for b in np.load(bb):
                name = lab.get(str(int(b["semanticId"])), "?")
                if name in STUFF:
                    continue
                box = (b["x_min"] * sx, b["y_min"] * sy, b["x_max"] * sx, b["y_max"] * sy)
                dr.rectangle(box, outline=(255, 220, 0), width=2)
                dr.text((box[0] + 2, box[1] + 1), name, fill=(255, 220, 0), font=fs)
        sheet.paste(im, (x0, y0 + 20))
        sem = find("semantic_segmentation", i, "png")
        if sem is not None:
            sheet.paste(Image.open(sem).convert("RGB").resize((tw, th), Image.NEAREST), (x0 + tw, y0 + 20))
        r = frames[i]
        mats = "  ".join(f"{t}: {n}" for t, n in r.get("materials", {}).items())
        draw.text((x0 + 4, y0 + 3), f"frame {i}  target {r['target']}  boxes {r.get('boxes', '-')}  {mats}"[:110],
                  fill="black", font=fs)
    sheet.save(args.sheet, quality=88)
    summary["sheet"] = args.sheet

print("SDG_SUMMARY " + json.dumps({k: v for k, v in summary.items() if k != "per_frame"}, ensure_ascii=False))
app.close()
