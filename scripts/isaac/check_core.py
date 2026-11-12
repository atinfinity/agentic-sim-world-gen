"""The scene check shared by check_scene.py (headless, own SimulationApp) and the live mode
(code sent to a running Isaac Sim's python_server). Import after Kit has started.

run() opens the USD (reloading layers already in memory, so a re-composed scene is read again),
waits for asset loading, records which objects resolved, runs physics for `steps` frames and judges:
unstable (moved >= move_tol), sunk (bbox below the floor after the settle), no_collider (robots pass
through it), static (collider but no rigid body). `step` is an async callable that advances Kit by one
frame: app.update() in the headless script, next_update_async() in a running app.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Awaitable, Callable

Step = Callable[[], Awaitable[None]]


async def frames(step: Step, n: int) -> None:
    for _ in range(n):
        await step()


def _reload_layers(usd: str) -> int:
    """Reload layers of this scene that are still in memory (a re-opened path is otherwise served
    from the layer cache, without the latest compose). Returns the number reloaded."""
    from pxr import Sdf

    folder = Path(usd).resolve().parent
    n = 0
    for layer in Sdf.Layer.GetLoadedLayers():
        p = layer.realPath
        if p and not layer.anonymous and Path(p).resolve().is_relative_to(folder):
            layer.Reload(force=True)
            n += 1
    return n


def enable_graph_nodes() -> bool:
    """Auto doors (agentic_sim_world_gen.usd.door) use PhysX scene-query graph nodes: in the full app they come
    with omni.physx.bundle, headless / pip apps have to enable them before the stage opens."""
    import omni.kit.app

    mgr = omni.kit.app.get_app().get_extension_manager()
    if mgr.is_extension_enabled("omni.physx.graph"):
        return False
    mgr.set_extension_enabled_immediate("omni.physx.graph", True)
    return True


async def open_stage(usd: str, step: Step, live: bool = False, max_frames: int = 600):
    """Open `usd` fresh and wait until remote references are loaded. Returns the stage or None.
    live: close the current stage first (reloading layers of the open stage crashes Kit 6.1) and give the
    viewport its default camera back (it points at a prim of the old stage)."""
    import omni.timeline
    import omni.usd

    omni.timeline.get_timeline_interface().stop()
    if enable_graph_nodes():
        await frames(step, 5)  # opening a stage right after enabling it crashed Kit
    ctx = omni.usd.get_context()
    if live:
        from omni.kit.viewport.utility import get_active_viewport

        vp = get_active_viewport()
        if vp:
            vp.camera_path = "/OmniverseKit_Persp"
        await frames(step, 2)
        await ctx.close_stage_async()
        await frames(step, 2)
        _reload_layers(usd)
        ok, _ = await ctx.open_stage_async(usd)
    else:
        _reload_layers(usd)
        ok = ctx.open_stage(usd)
    if not ok:
        return None
    for _ in range(max_frames):  # remote references can take a while on the first run
        await step()
        if ctx.get_stage_loading_status()[2] == 0:
            break
    await frames(step, 10)
    return ctx.get_stage()


def mute_extra_scenes(stage, keep: str = "/World/PhysicsScene") -> list[str]:
    """Deactivate physics scenes other than `keep` (assets that bring their own: about 25 PhysX scenes
    stop the first physics frame). compose already switches off the known ones; this catches the rest.
    Returns the paths switched off."""
    from pxr import Usd, UsdPhysics

    if not stage.GetPrimAtPath(keep):
        return []
    out = []
    for p in Usd.PrimRange(stage.GetPseudoRoot(), Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)):
        if p.IsA(UsdPhysics.Scene) and str(p.GetPath()) != keep:
            out.append(str(p.GetPath()))
    for path in out:
        p = stage.GetPrimAtPath(path)
        if p.IsInstanceProxy():  # inside an instance: not editable from the stage
            continue
        p.SetActive(False)
    return out


# SDF colliders kept before the busiest objects become hulls. Isaac Sim 6.1's GPU solver stops with CUDA error 700
# (~450 SDF bodies stop, ~400 run). A restaurant (561 SDF, 432 of them 36 hospital chairs) with the
# wide buffers: 400 stops, 200 runs, all hulls stops again, so it is not just the count. AGENTIC_SIM_WORLD_GEN_SDF_LIMIT overrides.
SDF_LIMIT = int(os.environ.get("AGENTIC_SIM_WORLD_GEN_SDF_LIMIT", "200"))


def limit_sdf(stage, limit: int = SDF_LIMIT) -> dict:
    """Over `limit` SDF mesh colliders: make the objects with the most of them convex hulls (in the session layer,
    instances holding one made editable) until the rest fit. Coarser contact for those objects, but physics runs.
    Returns {"sdf": total, "objects": [names changed], "colliders": n changed} ({} when under the limit)."""
    from pxr import Usd, UsdPhysics

    def is_sdf(p) -> bool:
        return p.HasAPI(UsdPhysics.MeshCollisionAPI) and UsdPhysics.MeshCollisionAPI(p).GetApproximationAttr().Get() == "sdf"

    pred = Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)
    per: dict[str, list] = {}
    for p in _asset_prims(stage):
        sdf = [q.GetPath() for q in Usd.PrimRange(p, pred) if is_sdf(q)]
        if sdf:
            per[_name(p)] = sdf
    total = sum(len(v) for v in per.values())
    if total <= limit:
        return {}
    changed, n, left = [], 0, total
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for name, paths in sorted(per.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            if left <= limit:
                break
            for path in paths:
                q = stage.GetPrimAtPath(path)
                while q and q.IsInstanceProxy():  # make the instance editable
                    q = q.GetParent()
                    if q.IsInstance():
                        q.SetInstanceable(False)
                        break
                UsdPhysics.MeshCollisionAPI(stage.GetPrimAtPath(path)).GetApproximationAttr().Set("convexHull")
            changed.append(name)
            n += len(paths)
            left -= len(paths)
    return {"sdf": total, "objects": changed, "colliders": n}


def _asset_prims(stage):
    """Objects = prims carrying agentic_sim_world_gen:uri (direct children, or array units one level deeper)."""
    from pxr import Usd

    root = stage.GetPrimAtPath("/World/Objects")
    out = []
    if root:
        it = iter(Usd.PrimRange(root))
        for p in it:
            if p.GetCustomDataByKey("agentic_sim_world_gen:uri"):
                out.append(p)
                it.PruneChildren()  # don't walk into the referenced asset
    return out


def _name(prim) -> str:
    return str(prim.GetPath()).removeprefix("/World/Objects/")


def survey(stage) -> tuple[dict, dict[str, list[str]]]:
    """Per object: resolved, variants, rigid bodies / colliders, bbox. Also the rigid body paths."""
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    objects, rigid = {}, {}
    for obj in _asset_prims(stage):
        ref = obj.GetChild("Asset")  # compose puts the reference + variants on this child
        if not ref.IsValid():
            ref = obj  # scenes composed before the Asset child existed
        # SimReady assets are instanceable: walk instance proxies or colliders read as 0
        children = list(Usd.PrimRange(obj, Usd.TraverseInstanceProxies()))
        # a body with rigidBodyEnabled = false is static (display stock, Fill.physics)
        bodies = [str(p.GetPath()) for p in children if p.HasAPI(UsdPhysics.RigidBodyAPI)
                  and UsdPhysics.RigidBodyAPI(p).GetRigidBodyEnabledAttr().Get() is not False]
        rng = cache.ComputeWorldBound(obj).ComputeAlignedRange()
        size = rng.GetSize() if not rng.IsEmpty() else Gf.Vec3d(0, 0, 0)
        objects[_name(obj)] = {
            # only the Xform (and our Collider) => reference failed
            "resolved": len(list(Usd.PrimRange(ref, Usd.TraverseInstanceProxies()))) > 1 if ref != obj else len(children) > 1,
            "uri": obj.GetCustomDataByKey("agentic_sim_world_gen:uri"),
            "variant_selections": dict(ref.GetVariantSets().GetAllVariantSelections()) if ref.IsValid() else {},
            "rigid_bodies": len(bodies),
            "colliders": sum(1 for p in children if p.HasAPI(UsdPhysics.CollisionAPI)),
            "world_bbox_size": [round(v, 3) for v in size],
            "world_bbox_min_z": round(rng.GetMin()[2], 3) if not rng.IsEmpty() else None,
        }
        rigid[_name(obj)] = bodies
    return objects, rigid


MAX_DEPENETRATION = 1.0  # [m/s]
MAX_SPEED = 10.0  # [m/s] a 5 m fall reaches 9.9 m/s


def cap_depenetration(stage, rigid: dict[str, list[str]], v: float = MAX_DEPENETRATION) -> None:
    """Objects that start interpenetrating are pushed apart at most this fast (session layer only, the
    file is untouched). Uncapped, a deep overlap blows the bodies up (NaN poses), and in a windowed
    Isaac Sim 6.1 that made the GPU narrowphase fail with CUDA error 700: PhysX stopped for the rest of
    the session and the app crashed. Capped, the overlap shows up as `unstable` instead."""
    from pxr import PhysxSchema, Usd

    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for bodies in rigid.values():
            for b in bodies:
                p = stage.GetPrimAtPath(b)
                if p and not p.IsInstanceProxy():
                    api = PhysxSchema.PhysxRigidBodyAPI.Apply(p)
                    api.CreateMaxDepenetrationVelocityAttr().Set(v)
                    api.CreateMaxLinearVelocityAttr().Set(MAX_SPEED)


def body_positions(rigid: dict[str, list[str]]) -> dict[str, tuple[float, float, float]]:
    """Rigid body positions from PhysX (USD xforms are not what the solver currently has)."""
    from omni.physx import get_physx_interface

    px = get_physx_interface()
    out = {}
    for bodies in rigid.values():
        for b in bodies:
            t = px.get_rigidbody_transformation(b)
            if t.get("ret_val"):
                out[b] = tuple(t["position"])
    return out


def _log_file() -> Path | None:
    import carb.settings

    f = carb.settings.get_settings().get("/log/file")
    return Path(f) if f else None


def log_size() -> int:
    """Where the Kit log ends now, so blown_up() reads only what this check wrote."""
    try:
        return _log_file().stat().st_size
    except Exception:  # noqa: BLE001 - no log file: blown_up() finds nothing
        return -1


def blown_up(objects: dict, since: int) -> list[str]:
    """Objects PhysX gave a NaN / infinite pose ("Invalid PhysX transform detected for <prim>") in the Kit log
    after `since`. A blow-up is what stops the GPU narrowphase with CUDA error 700 (office chairs, 13
    jointed bodies each, side by side with no gap), after which no pose can be read at all."""
    import re

    try:
        with open(_log_file(), "rb") as fh:
            fh.seek(max(since, 0))
            text = fh.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    names = sorted(objects, key=len, reverse=True)  # "chairs/chairs_r0c3" before "chairs"
    out: list[str] = []
    for m in re.finditer(r"Invalid PhysX transform detected for /World/Objects/(\S+?)\.?$", text, re.M):
        name = next((n for n in names if m[1] == n or m[1].startswith(n + "/")), None)
        if name and name not in out:
            out.append(name)
    return out


def judge(stage, objects: dict, rigid: dict, start: dict, end: dict, move_tol: float) -> dict:
    """Fill displacement / stability / sinking into `objects` and return the summary lists."""
    from pxr import Usd, UsdGeom

    for name, bodies in rigid.items():
        moved = [(math.dist(start[b], end[b]), b) for b in bodies if b in start and b in end]
        if not moved:
            continue
        o = objects[name]
        d, worst = max(moved)
        if math.isfinite(d) and d >= move_tol:  # which body moved, from where to where
            o["moved_body"] = {"path": worst, "from": [round(v, 3) for v in start[worst]],
                               "to": [round(v, 3) for v in end[worst]]}
        exploded = not math.isfinite(d)  # NaN/inf: PhysX blew up (usually interpenetration)
        o["max_displacement_m"] = None if exploded else round(d, 4)
        o["stable"] = (not exploded) and d < move_tol
        if exploded:
            o["exploded"] = True
    # sinking: bbox after the settle (PhysX writes the poses back to USD)
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    for obj in _asset_prims(stage):
        o = objects[_name(obj)]
        rng = cache.ComputeWorldBound(obj).ComputeAlignedRange()
        if not rng.IsEmpty() and o["rigid_bodies"] and math.isfinite(rng.GetMin()[2]):
            o["settled_min_z"] = round(rng.GetMin()[2], 3)
    return {
        "unstable": sorted(n for n, o in objects.items() if o.get("stable") is False),
        "sunk": sorted(n for n, o in objects.items() if o.get("settled_min_z", 0) < -0.01),
        "no_collider": sorted(n for n, o in objects.items() if o["resolved"] and not o["colliders"]),
        "static": sorted(n for n, o in objects.items() if o["resolved"] and o["colliders"] and not o["rigid_bodies"]),
    }


async def orbit(stage, step: Step, out_dir: str, n: int, settle: int = 8, width: int = 960) -> dict:
    """Shoot the settled room from `n` directions around it (frame 0 = the screenshot's view) into
    out_dir/NN.jpg (PNG when Pillow is missing), for the preview page's drag-to-rotate viewer."""
    from view import DOLLHOUSE_AZIMUTH, capture, dollhouse_camera

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for f in [*out.glob("*.png"), *out.glob("*.jpg")]:
        f.unlink()
    shots, az, cams = [], [], []
    for i in range(n):
        a = DOLLHOUSE_AZIMUTH + 360.0 * i / n
        dollhouse_camera(stage, azimuth_deg=a)
        cams.append(_camera(stage))
        await frames(step, settle)  # let RTX converge after the jump
        shots.append(out / f"{i:02d}.png")
        capture(shots[-1])
        await frames(step, 2)
        az.append(round(a % 360, 2))
    dollhouse_camera(stage)  # back to the screenshot's view
    for _ in range(120):  # the captures are written asynchronously
        if all(p.exists() for p in shots):
            break
        await frames(step, 1)
    names = []
    try:
        from PIL import Image
    except ImportError:
        Image = None
    for p in shots:
        if not p.exists():
            names.append(None)
            continue
        if Image is not None:
            im = Image.open(p).convert("RGB")
            if im.width > width:
                im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
            im.save(p.with_suffix(".jpg"), quality=82)
            p.unlink()
            p = p.with_suffix(".jpg")
        names.append(p.name)
    return {"dir": str(out), "frames": names, "azimuth_deg": az, "cameras": cams}


def _camera(stage, path: str = "/World/PreviewCamera") -> dict | None:
    """The view camera as the preview needs it to turn a click into a spot: eye, forward, up and the
    horizontal field of view (the aperture fits the image width)."""
    import math

    from pxr import Gf, Usd, UsdGeom

    prim = stage.GetPrimAtPath(path)
    if not prim:
        return None
    cam = UsdGeom.Camera(prim)
    m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    f = cam.GetFocalLengthAttr().Get() or 18.0
    ap = cam.GetHorizontalApertureAttr().Get() or 20.955
    vec = lambda v: [round(float(x), 5) for x in v]  # noqa: E731
    return {"eye": vec(m.ExtractTranslation()), "forward": vec(m.TransformDir(Gf.Vec3d(0, 0, -1)).GetNormalized()),
            "up": vec(m.TransformDir(Gf.Vec3d(0, 1, 0)).GetNormalized()),
            "hfov_deg": round(math.degrees(2 * math.atan(ap / 2 / f)), 3)}


async def run(usd: str, step: Step, steps: int = 240, move_tol: float = 0.02, screenshot: str | None = None,
              gif: str | None = None, report_path: str | None = None, live: bool = False,
              orbit_dir: str | None = None, orbit_frames: int = 0) -> dict:
    """The whole check. steps=0: no physics, only the pictures (a draft look, report["physics"] = False).
    live=True: always point the viewport at the room (the user is watching) and
    select the objects that failed, so they stand out in the Stage panel. orbit_frames > 0: also shoot the
    settled room from that many directions into orbit_dir (see orbit())."""
    import omni.timeline
    from pxr import UsdGeom
    from view import FrameRecorder, capture, dollhouse_camera

    stage = await open_stage(usd, step, live)
    if stage is None:
        return {"usd": usd, "error": f"failed to open {usd}"}
    report: dict = {"usd": usd, "objects": {}}
    muted = mute_extra_scenes(stage)
    if muted:
        report["extra_physics_scenes"] = muted
    if hull := limit_sdf(stage):
        report["sdf_to_hull"] = hull
    if live:
        report["mode"] = "live"
    objects, rigid = survey(stage)
    report["objects"] = objects

    # start = authored pose: the first PhysX read after play() is already a few steps in
    xf = UsdGeom.XformCache()
    start = {b: tuple(xf.GetLocalToWorldTransform(stage.GetPrimAtPath(b)).ExtractTranslation())
             for bodies in rigid.values() for b in bodies}

    if screenshot or gif or live or (orbit_dir and orbit_frames > 0):
        # set up the camera first so the recording sees the settle
        report["screenshot_hidden"] = dollhouse_camera(stage)
        await frames(step, 30 if (gif or live) else 1)  # let RTX converge before recording
    rec = FrameRecorder(str(Path(gif).with_suffix("")) + "_frames" if gif else None)

    if steps > 0:
        cap_depenetration(stage, rigid)
        log_at = log_size()
        timeline = omni.timeline.get_timeline_interface()
        timeline.play()
        for _ in range(steps):
            await step()
            rec.tick()
        end = body_positions(rigid)
        timeline.pause()  # keep the settled state on screen
        await frames(step, 2)
    else:  # a draft look: pictures of the room as authored, no physics
        end = dict(start)
        report["physics"] = False
    report.update(judge(stage, objects, rigid, start, end, move_tol))
    if steps > 0 and start and not end:  # e.g. the GPU narrowphase failed (CUDA error 700) and PhysX stopped
        blown = blown_up(objects, log_at)
        if blown:
            report["blew_up"] = blown
        report["error"] = (
            "physics did not run: PhysX returned no rigid body poses, the GPU physics stopped (CUDA error)"
            + (f" after {', '.join(blown[:8])} blew up" if blown else "")
            + ". It is the scene, not chance: objects that start inside or touching each other (jointed office "
            "chairs side by side with no gap) blow up, so running it again fails the same way. Move them a few cm "
            "apart and check again" + ("; restart Isaac Sim first (its GPU physics stays broken)" if live else ""))

    if screenshot:
        await frames(step, 30)  # let RTX converge a bit
        capture(screenshot)
        await frames(step, 10)
        report["screenshot"] = screenshot
    if gif:
        report["gif"] = rec.gif(gif)
    if orbit_dir and orbit_frames > 0 and "error" not in report:
        report["orbit"] = await orbit(stage, step, orbit_dir, orbit_frames)
    if live:
        bad = sorted(set(report["unstable"]) | set(report["sunk"]) | set(report["no_collider"]))
        if bad:
            import omni.usd

            omni.usd.get_context().get_selection().set_selected_prim_paths(
                [f"/World/Objects/{n}" for n in bad], True)
        report["selected"] = bad

    if report_path:
        Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report


def run_sync(coro):
    """Drive a coroutine whose awaits never suspend (headless: step() is a plain app.update())."""
    try:
        while True:
            coro.send(None)
    except StopIteration as e:
        return e.value
