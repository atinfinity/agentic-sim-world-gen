#!/usr/bin/env python
"""Draw the top views of examples/*.json for the gallery: src/agentic_sim_world_gen/data/gallery/<name>.svg.

    PYTHONPATH=src python scripts/make_gallery.py [name ...]

Each example is resolved with the real search (network; a few minutes for all) and its layout.svg is kept.
Run again after an example or the top view's drawing changes."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from agentic_sim_world_gen import gallery, service


def main(names: list[str]) -> int:
    ex = gallery.examples_dir()
    files = [ex / f"{n}.json" for n in names] or sorted(ex.glob("*.json"))
    gallery.THUMBS.mkdir(parents=True, exist_ok=True)
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for f in files:
            t = time.time()
            try:
                st = service.create_scene(json.loads(f.read_text(encoding="utf-8")), Path(tmp) / f.stem, preferences=False)
                svg = Path(st["scene_dir"]) / "layout.svg"
                (gallery.THUMBS / f"{f.stem}.svg").write_text(svg.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"{f.stem}: {svg.stat().st_size / 1024:.0f} KB, {time.time() - t:.0f} s", flush=True)
            except Exception as e:  # noqa: BLE001 — the others are still drawn
                failed += 1
                print(f"{f.stem}: FAILED {type(e).__name__}: {e}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
