"""Monocular depth for fit_photos: fill the `depth` of landmarks and surface pixels from a depth map of the
photo, `<photo stem>.depth.npz` next to the photo (written by scripts/depth/moge_depth.py with MoGe-2).

The depth model runs in its own Python (AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON, a venv with MoGe and torch): MoGe's package pulls
gradio and a CUDA build, so it is not an extra of agentic-sim-world-gen. Reading the maps needs numpy only.

Measured on a handheld recording (ARKitScenes): MoGe-2 ViT-S is within 2-6 %
of the true depth inside a frame, but each frame's scale is off by -15 .. +24 % - so fit_photos fits one scale per
photo. Depth Anything V2 Metric Indoor was 1.3-2x too far and is not used.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

MODEL = "Ruicheng/moge-2-vits-normal"  # MIT, 141 MB
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "depth" / "moge_depth.py"
WINDOW = 2  # median over (2 * WINDOW + 1)^2 pixels of the map around the pixel


def map_path(image: str | Path) -> Path:
    p = Path(image)
    return p.with_name(p.stem + ".depth.npz")


def load(image: str | Path):
    """(depth map (h, w) float32 [m], meta {hfov_deg, model, size}) or None when there is no map."""
    import numpy as np

    f = map_path(image)
    if not f.exists():
        return None
    with np.load(f, allow_pickle=False) as z:
        d = z["depth"].astype("float32")
        meta = json.loads(str(z["meta"]))
    return d, meta


def sample(depth, size: tuple[int, int], px: tuple[float, float]) -> float | None:
    """Median depth around pixel px of an image of `size` (the map may be smaller)."""
    import numpy as np

    h, w = depth.shape
    iu, iv = int(px[0] / size[0] * w), int(px[1] / size[1] * h)
    if not (0 <= iu < w and 0 <= iv < h):
        return None
    win = depth[max(0, iv - WINDOW):iv + WINDOW + 1, max(0, iu - WINDOW):iu + WINDOW + 1]
    win = win[np.isfinite(win) & (win > 0)]
    return float(np.median(win)) if win.size else None


def fill(request: dict[str, Any]) -> dict[str, Any]:
    """Fill `depth` of every point, line and surface pixel of the photos that have `image` and a depth map.
    Returns {photos: {id: {filled, hfov_deg, model}}, missing: [ids], skipped: [...]}. Given depths are kept."""
    out: dict[str, Any] = {"photos": {}, "missing": [], "skipped": []}
    for cid, c in (request.get("photos") or {}).items():
        img = c.get("image")
        if not img:
            continue
        got = load(img)
        if got is None:
            out["missing"].append(cid)
            continue
        d, meta = got
        size = tuple(c.get("size") or meta["size"])
        n = 0
        for kind in ("points", "lines", "surfaces"):
            for o in c.get(kind) or []:
                if o.get("depth"):
                    continue
                v = sample(d, size, o["px"])
                if v is None:
                    out["skipped"].append(f"{cid} {o.get('at') or o.get('along') or o.get('on')}")
                    continue
                o["depth"] = round(v, 4)
                n += 1
        out["photos"][cid] = {"filled": n, "hfov_deg": meta.get("hfov_deg"), "model": meta.get("model")}
    return out


def python() -> str | None:
    return os.environ.get("AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON")


def run(images: list[str | Path], force: bool = False, timeout: float = 1800) -> dict[str, Any]:
    """Write `<stem>.depth.npz` for each image with scripts/depth/moge_depth.py in AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON.
    The model is downloaded only with AGENTIC_SIM_WORLD_GEN_DEPTH_DOWNLOAD=1."""
    todo = [str(Path(i)) for i in images if force or not map_path(i).exists()]
    done = [str(map_path(i)) for i in images]
    if not todo:
        return {"ok": True, "maps": done, "ran": 0}
    py = python()
    if not py:
        return {"ok": False, "error": "AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON is not set: make a venv with MoGe "
                "(docs/getting-started.md「単眼の深度」) and point AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON at its python"}
    env = dict(os.environ)
    if env.get("AGENTIC_SIM_WORLD_GEN_DEPTH_DOWNLOAD") != "1":
        env["HF_HUB_OFFLINE"] = "1"
    cmd = [py, "-I", str(SCRIPT), "--model", os.environ.get("AGENTIC_SIM_WORLD_GEN_DEPTH_MODEL", MODEL), *todo]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        tail = (p.stderr or p.stdout).strip().splitlines()[-5:]
        hint = " (set AGENTIC_SIM_WORLD_GEN_DEPTH_DOWNLOAD=1 to download the model, 141 MB)" if "offline" in p.stderr.lower() \
            or "LocalEntryNotFound" in p.stderr else ""
        return {"ok": False, "error": "depth model failed" + hint + ": " + " | ".join(tail)}
    return {"ok": True, "maps": done, "ran": len(todo)}


def fov_hint(filled: dict[str, Any], given: float | None) -> str | None:
    """The depth model's own field of view (median over photos) when it differs from the one given."""
    v = sorted(x["hfov_deg"] for x in filled.get("photos", {}).values() if x.get("hfov_deg"))
    if not v:
        return None
    med = v[len(v) // 2]
    if given is None or abs(med - given) > 5:
        return f"the depth model sees a field of view of {med:.0f} deg (median of {len(v)} photos)"
    return None
