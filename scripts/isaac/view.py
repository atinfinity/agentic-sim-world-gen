"""Shared viewport helpers for the Isaac Sim scripts (import after SimulationApp starts).

dollhouse_camera(): camera above the south-west corner looking at the room (or outdoor site) center,
with the ceiling and the camera-side walls hidden (from the camera only for a room with its site: they still
shade it; show() undoes either); `azimuth_deg` moves it around the room on the same
ellipse (DOLLHOUSE_AZIMUTH is the default view) for the orbit frames of the check. FrameRecorder: PNG frames while physics runs, turned
into a GIF with ffmpeg (if installed).
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

from pxr import Gf, Usd, UsdGeom


def _camera_only(p, on: bool) -> None:
    """Hide a prim from the camera only (RTX primvars:hideForCamera on its meshes): it still casts shadows and
    blocks the sky. A room with its site: MakeInvisible let the sun flood the room past the hidden ceiling
    and walls, so the 3000 K fixtures did not show."""
    from pxr import Sdf

    for q in Usd.PrimRange(p):
        if q.IsA(UsdGeom.Gprim):
            pv = UsdGeom.PrimvarsAPI(q)
            if on:
                pv.CreatePrimvar("hideForCamera", Sdf.ValueTypeNames.Bool).Set(True)
            elif pv.HasPrimvar("hideForCamera"):
                pv.GetPrimvar("hideForCamera").Set(False)


def hide(p, camera_only: bool = False) -> None:
    if camera_only:
        UsdGeom.Imageable(p).MakeVisible()
        _camera_only(p, True)
    else:
        UsdGeom.Imageable(p).MakeInvisible()


def show(p) -> None:
    """Undo hide() either way."""
    UsdGeom.Imageable(p).MakeVisible()
    _camera_only(p, False)


def wait_frames(app, n: int) -> None:
    for _ in range(n):
        app.update()


def hide_guides() -> None:
    """Hide the viewport grid and axis overlays (they end up in captured images)."""
    import carb.settings
    from omni.kit.viewport.utility import get_active_viewport

    st = carb.settings.get_settings()
    st.set("/app/viewport/grid/enabled", False)
    vid = get_active_viewport().id
    for guide in ("grid", "axis"):
        st.set(f"/persistent/app/viewport/{vid}/guide/{guide}/visible", False)
        st.set(f"/persistent/app/viewport/Viewport/Viewport0/guide/{guide}/visible", False)


# the default eye is center + (-0.6 w, -0.9 d); on the ellipse center + k (w cos a, d sin a) that is:
DOLLHOUSE_AZIMUTH = math.degrees(math.atan2(-0.9, -0.6))
_K = math.hypot(0.6, 0.9)


def dollhouse_camera(stage, room_root: str = "/World/Room", azimuth_deg: float | None = None) -> list[str]:
    """Point the active viewport at the room from above (the south-west corner, or `azimuth_deg` around
    the room, counter-clockwise from +X). Returns the hidden prims (ceiling, walls on the camera side)."""
    from omni.kit.viewport.utility import get_active_viewport

    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    site = stage.GetPrimAtPath(room_root).GetCustomDataByKey("agentic_sim_world_gen:site") if stage.GetPrimAtPath(room_root) else None
    floor = stage.GetPrimAtPath(f"{room_root}/Floor")
    if site is not None and floor:  # a room with its surroundings: the room and the things outside it
        rng = cache.ComputeWorldBound(floor).ComputeAlignedRange()
        objects = stage.GetPrimAtPath("/World/Objects")
        if objects:
            rng.UnionWith(cache.ComputeWorldBound(objects).ComputeAlignedRange())
        x0, y0, x1, y1 = list(site)
        lo, hi = rng.GetMin(), rng.GetMax()
        lo = Gf.Vec3d(max(lo[0], x0), max(lo[1], y0), 0)
        hi = Gf.Vec3d(min(hi[0], x1), min(hi[1], y1), 0)
        c, sz = (lo + hi) / 2, hi - lo
    elif site is not None:  # outdoor: the site, not the ground slab (that runs on past it)
        x0, y0, x1, y1 = list(site)
        c, sz = Gf.Vec3d((x0 + x1) / 2, (y0 + y1) / 2, 0), Gf.Vec3d(x1 - x0, y1 - y0, 0)
    else:
        rng = cache.ComputeWorldBound(floor).ComputeAlignedRange()
        c, sz = rng.GetMidpoint(), rng.GetSize()
    a = math.radians(DOLLHOUSE_AZIMUTH if azimuth_deg is None else azimuth_deg)
    eye = Gf.Vec3d(c[0] + _K * sz[0] * math.cos(a), c[1] + _K * sz[1] * math.sin(a), max(sz[0], sz[1]) * 1.1)
    hidden = []
    shade = site is not None and bool(floor)  # a room with its site: hidden from the camera only
    root = stage.GetPrimAtPath(room_root)
    for p in root.GetChildren() if root else []:
        name = p.GetName()
        hide_it = name in ("Ceiling", "CeilingFeatures")
        if name.startswith("Wall_"):
            wc = cache.ComputeWorldBound(p).ComputeAlignedRange().GetMidpoint()
            hide_it = (wc[0] - c[0]) * (eye[0] - c[0]) + (wc[1] - c[1]) * (eye[1] - c[1]) > 0
        if hide_it:
            hide(p, shade)
            hidden.append(name)
        elif name.startswith("Wall_"):  # hidden for an earlier camera position
            show(p)
    # fixtures flush with the ceiling go with it (pendants and suspended lines stay)
    fixtures = stage.GetPrimAtPath(f"{room_root}/Fixtures")
    for p in fixtures.GetChildren() if fixtures else []:
        if p.GetCustomDataByKey("agentic_sim_world_gen:flush"):
            hide(p, shade)
    # windows, curtains and doors in an opening of a hidden wall go with it
    gone = set()
    for name in hidden:
        ids = root.GetChild(name).GetCustomDataByKey("agentic_sim_world_gen:openings")
        gone.update(ids or ())
    objects = stage.GetPrimAtPath("/World/Objects")
    for p in objects.GetChildren() if objects else []:
        op = p.GetCustomDataByKey("agentic_sim_world_gen:opening")
        if op is None:
            continue
        if op in gone:
            UsdGeom.Imageable(p).MakeInvisible()
            hidden.append(f"Objects/{p.GetName()}")
        else:
            UsdGeom.Imageable(p).MakeVisible()
    cam = UsdGeom.Camera.Define(stage, "/World/PreviewCamera")
    m = Gf.Matrix4d().SetLookAt(eye, Gf.Vec3d(c[0], c[1], 0.4), Gf.Vec3d(0, 0, 1)).GetInverse()
    xf = UsdGeom.Xformable(cam)
    xf.ClearXformOpOrder()
    xf.AddTransformOp().Set(m)
    cam.CreateFocalLengthAttr(18.0)
    get_active_viewport().camera_path = "/World/PreviewCamera"
    hide_guides()
    return hidden


def capture(path: str) -> None:
    from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport

    capture_viewport_to_file(get_active_viewport(), str(path))


class FrameRecorder:
    """Call tick() once per app.update() while physics runs; a frame is saved every `every` ticks."""

    def __init__(self, out_dir: str | None, every: int = 4):
        self.dir = Path(out_dir) if out_dir else None
        self.every, self.i, self.n = every, 0, 0
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
            for f in self.dir.glob("frame_*.png"):
                f.unlink()

    def tick(self) -> None:
        if self.dir and self.i % self.every == 0:
            capture(self.dir / f"frame_{self.n:04d}.png")
            self.n += 1
        self.i += 1

    def gif(self, path: str | None, fps: int = 15, width: int = 640) -> str | None:
        """Assemble the frames into a GIF (palette per clip). Needs ffmpeg; returns the path or None."""
        if not (self.dir and path and self.n and shutil.which("ffmpeg")):
            return None
        vf = f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(60 // self.every),
               "-i", str(self.dir / "frame_%04d.png"), "-vf", vf, str(path)]
        return str(path) if subprocess.run(cmd).returncode == 0 else None
