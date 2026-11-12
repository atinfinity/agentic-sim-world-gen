"""P0 check: open a generated scene in Isaac Sim 6.1 (headless), verify that assets
resolved, run physics for a while, report how far objects moved, and save a screenshot.

Run with Isaac Sim's Python (NOT the project venv): python.sh of a standalone install,
or the python of a venv with the pip package `isaacsim[all,extscache]==6.1.0.0`:
    ~/isaacsim/python.sh scripts/isaac/check_scene.py out/study/scene.usda \
        --steps 240 --screenshot out/study/shot.png --report out/study/check.json [--gif out/study/check.gif]

Report per object: resolved, variant selections, rigid bodies / colliders, displacement during the
settle (from the authored pose), bbox min z after it (sinking). Top-level lists: unstable, sunk,
no_collider (nothing to collide with: robots pass through it), static (no rigid body: never moves).

The check itself is check_core.py, shared with the live mode (`agentic-sim-world-gen check --live`: the same code
sent to a running Isaac Sim with python_server). Verified on Isaac Sim 6.1.0 (pip, RTX 2080 Ti).
"""

from __future__ import annotations

import argparse
import json
import sys

ap = argparse.ArgumentParser()
ap.add_argument("usd")
ap.add_argument("--steps", type=int, default=240, help="physics steps at 60 Hz")
ap.add_argument("--screenshot")
ap.add_argument("--report")
ap.add_argument("--move-tol", type=float, default=0.02, help="[m] displacement considered 'moved'")
ap.add_argument("--gif", help="record the settle as a GIF (needs ffmpeg); frames go next to it")
ap.add_argument("--orbit-dir", help="also shoot the settled room from all around into this folder (NN.jpg)")
ap.add_argument("--orbit-frames", type=int, default=24, help="number of directions for --orbit-dir")
args = ap.parse_args()

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": 1280, "height": 720})

import check_core  # noqa: E402  (scripts/isaac/check_core.py)


async def step() -> None:
    app.update()


report = check_core.run_sync(check_core.run(
    args.usd, step, steps=args.steps, move_tol=args.move_tol, screenshot=args.screenshot, gif=args.gif,
    report_path=args.report, orbit_dir=args.orbit_dir, orbit_frames=args.orbit_frames))
print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
app.close()
if "error" in report:
    sys.exit(1)
