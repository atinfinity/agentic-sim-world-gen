"""A headless Isaac Sim that stays up and runs the other scripts of this folder one after another.

Starting Kit takes most of a check (about 40 s for a draft) and of view_fronts / compare_photo. With
AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER=1 the service (agentic_sim_world_gen.isaac_worker) starts this once and hands it jobs:

    $ISAACSIM_PYTHON scripts/isaac/worker.py --dir <spool> [--idle 600] [--parent <pid>]

A job is a file <spool>/job_<n>.json {"script": "check_scene.py", "argv": [...], "reply": "<file>"}; the reply
file gets {"returncode", "stdout", "error"?, "seconds"}. The script runs unchanged, as __main__, with sys.argv
set: its `SimulationApp(...)` returns this process's app (close() does nothing) and the viewport takes the
size it asked for. Before each job the stage is closed and the layers of the job's folder are reloaded, so
nothing of the job before is left (what a script hides or adds is never saved).

It stops when <spool>/stop appears, when --parent is gone, or after --idle seconds without a job. A job that
raises leaves the worker up, but the service stops it and runs the job the one-shot way: after a PhysX
blow-up (CUDA error 700) the GPU physics of a Kit stays broken.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import runpy
import sys
import time
import traceback
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--dir", required=True, help="spool folder: job_<n>.json in, `ready` and `stop` flags")
ap.add_argument("--idle", type=float, default=600, help="stop after this many seconds without a job")
ap.add_argument("--parent", type=int, help="stop when this process is gone")
opts = ap.parse_args()
HERE = Path(__file__).resolve().parent
SPOOL = Path(opts.dir)
ALLOWED = {"check_scene.py", "front_views.py", "render_view.py"}  # what was tried in one Kit after another

import isaacsim  # noqa: E402
from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": 1280, "height": 720})


class _Shared:
    """What a script gets from SimulationApp(...): the worker's app, which it cannot close."""

    def __init__(self, config=None, *_, **__):
        from omni.kit.viewport.utility import get_active_viewport

        vp = get_active_viewport()
        if vp and config and config.get("width") and config.get("height"):
            vp.resolution = (int(config["width"]), int(config["height"]))

    def update(self):
        app.update()

    def close(self, *_, **__):
        pass

    def is_running(self):
        return app.is_running()

    def __getattr__(self, name):
        return getattr(app, name)


isaacsim.SimulationApp = _Shared
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def reset(folder: Path | None) -> None:
    """Close the stage of the job before and forget its layers (see check_core.open_stage, live)."""
    import omni.timeline
    import omni.usd
    from omni.kit.viewport.utility import get_active_viewport
    from pxr import Sdf

    omni.timeline.get_timeline_interface().stop()
    vp = get_active_viewport()
    if vp:
        vp.camera_path = "/OmniverseKit_Persp"
    for _ in range(2):
        app.update()
    omni.usd.get_context().close_stage()
    for _ in range(2):
        app.update()
    for layer in Sdf.Layer.GetLoadedLayers():
        p = layer.realPath
        if p and not layer.anonymous and (layer.dirty or (folder and Path(p).resolve().is_relative_to(folder))):
            try:
                layer.Reload(force=True)
            except Exception:  # noqa: BLE001  (a file that is gone, e.g. front_views_stage.usda)
                pass


def run(job: dict) -> dict:
    name = Path(job["script"]).name
    if name not in ALLOWED:
        return {"returncode": 2, "stdout": "", "error": f"worker does not run {name}"}
    argv = [str(a) for a in job.get("argv") or []]
    first = next((Path(a) for a in argv if a.endswith((".usd", ".usda", ".usdc"))), None)
    out, code, error = io.StringIO(), 0, None
    old_argv = sys.argv
    t0 = time.monotonic()
    try:
        reset(first.resolve().parent if first else None)
        sys.argv = [str(HERE / name), *argv]
        with contextlib.redirect_stdout(out):
            runpy.run_path(str(HERE / name), run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:  # noqa: BLE001
        code, error = 1, traceback.format_exc()[-4000:]
    finally:
        sys.argv = old_argv
    try:
        reset(None)  # drop the stage now: its assets are not held until the next job
    except Exception:  # noqa: BLE001
        code, error = code or 1, (error or "") + traceback.format_exc()[-2000:]
    r = {"returncode": code, "stdout": out.getvalue(), "seconds": round(time.monotonic() - t0, 1)}
    if error:
        r["error"] = error
    return r


def alive(pid: int | None) -> bool:
    if not pid:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def main() -> None:
    SPOOL.mkdir(parents=True, exist_ok=True)
    (SPOOL / "ready").write_text(str(os.getpid()))
    last = time.monotonic()
    jobs = 0
    while not (SPOOL / "stop").exists() and alive(opts.parent) and time.monotonic() - last < opts.idle:
        todo = sorted(SPOOL.glob("job_*.json"))
        if not todo:
            app.update()
            time.sleep(0.1)
            continue
        f = todo[0]
        try:
            job = json.loads(f.read_text())
        except ValueError:
            time.sleep(0.05)  # still being written
            continue
        f.unlink()
        reply = run(job)
        jobs += 1
        reply["jobs"] = jobs
        tmp = Path(str(job["reply"]) + ".part")
        tmp.write_text(json.dumps(reply, ensure_ascii=False))
        os.replace(tmp, job["reply"])
        last = time.monotonic()
    (SPOOL / "ready").unlink(missing_ok=True)


try:
    main()
finally:
    app.close()
