"""Find what stops Isaac Sim's first physics frame: open a scene with only some of its prims active, play and
print a line per frame. Run each half in its own process with a timeout (a stuck frame never returns).

    $ISAACSIM_PYTHON scripts/isaac/hang_probe.py out/restaurant/scene.usda --keep table1,booth1_s --steps 20
    --keep / --drop: names under /World/Objects (comma separated, or @file with one per line)
    --drop-room: names under /World/Room (Wall_K, Doors, ...); --no-lights: deactivate /World/Lights
    --no-play: stop after opening (is it the open or the play?)
    --drop-asset-scenes: deactivate the PhysicsScene prims that come with the assets

Prints OPENED, PLAY, FRAME n <s> and DONE; the caller's timeout tells a stuck frame.
"""
from __future__ import annotations

import argparse
import faulthandler
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("usd")
ap.add_argument("--keep")
ap.add_argument("--drop")
ap.add_argument("--drop-room", default="")
ap.add_argument("--no-lights", action="store_true")
ap.add_argument("--no-play", action="store_true")
ap.add_argument("--steps", type=int, default=20)
ap.add_argument("--drop-asset-scenes", action="store_true", help="deactivate the PhysicsScene prims inside the assets")
args = ap.parse_args()


def names(v: str | None) -> set[str] | None:
    if v is None:
        return None
    if v.startswith("@"):
        return {ln.strip() for ln in open(v[1:]) if ln.strip()}
    return {x for x in v.split(",") if x}


from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": 640, "height": 360})

import check_core  # noqa: E402
import omni.timeline  # noqa: E402

faulthandler.dump_traceback_later(120, repeat=True, file=sys.stderr)  # Python stacks while stuck


async def step() -> None:
    app.update()


t0 = time.time()
stage = check_core.run_sync(check_core.open_stage(args.usd, step))
if stage is None:
    sys.exit(f"failed to open {args.usd}")
keep, drop = names(args.keep), names(args.drop) or set()
root = stage.GetPrimAtPath("/World/Objects")
active = []
for c in root.GetChildren():
    if (keep is not None and c.GetName() not in keep) or c.GetName() in drop:
        c.SetActive(False)
    else:
        active.append(c.GetName())
for n in names(args.drop_room) or set():
    p = stage.GetPrimAtPath(f"/World/Room/{n}")
    if p:
        p.SetActive(False)
if args.no_lights and stage.GetPrimAtPath("/World/Lights"):
    stage.GetPrimAtPath("/World/Lights").SetActive(False)
from pxr import Usd, UsdPhysics  # noqa: E402

scenes = [p for p in Usd.PrimRange(stage.GetPseudoRoot()) if p.IsA(UsdPhysics.Scene)]
inner = [p for p in scenes if str(p.GetPath()).startswith("/World/Objects/")]
print(f"SCENES {len(scenes)} ({len(inner)} inside assets): {[str(p.GetPath()) for p in scenes if p not in inner]}", flush=True)
if args.drop_asset_scenes:
    for p in inner:
        p.SetActive(False)
for _ in range(5):
    app.update()
print(f"OPENED {time.time() - t0:.1f} s, {len(active)} objects active", flush=True)
if not args.no_play:
    tl = omni.timeline.get_timeline_interface()
    tl.play()
    print("PLAY", flush=True)
    t1 = time.time()
    for i in range(args.steps):
        app.update()
        print(f"FRAME {i} {time.time() - t1:.1f}", flush=True)
    tl.stop()
print("DONE", flush=True)
faulthandler.cancel_dump_traceback_later()
app.close()
