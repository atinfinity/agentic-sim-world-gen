"""Inputs a scene was made from (photos, reference images, drawings): provenance and photo cameras.

The source of every input is kept with the scene (scene.resolved.json, customData on /World, LICENSES.md).
Fields the caller leaves out are read from a sidecar `<stem>.source.json` next to the file, e.g.

    {"url": "https://commons.wikimedia.org/wiki/File:...", "title": "...", "author": "...", "license": "CC0-1.0"}
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

from agentic_sim_world_gen.scene.spec import InputRef, PhotoCamera, Screen, Taste

SIDECAR_FIELDS = ("url", "title", "author", "license")
FRAME_FIELDS = ("video", "time_s")  # a frame's sidecar also says which video and when (agentic_sim_world_gen.video)
SENSOR_W_MM = 36.0  # horizontal aperture of the USD camera; focal length follows from the field of view


def sidecar_path(path: Path) -> Path:
    return path.with_name(path.stem + ".source.json")


def image_size(path: Path) -> tuple[int, int] | None:
    """(width, height) of a PNG or JPEG, read from the header (no PIL needed). None if unknown."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", head[16:24])
            if head[:2] != b"\xff\xd8":
                return None
            f.seek(2)
            while True:
                marker = f.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None
                if marker[1] in (0xD8, 0x01) or 0xD0 <= marker[1] <= 0xD7:
                    continue
                (length,) = struct.unpack(">H", f.read(2))
                if marker[1] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">xHH", f.read(5))
                    return w, h
                f.seek(length - 2, 1)
    except OSError:
        return None


def resolve(ref: InputRef, base: Path | None = None) -> InputRef:
    """Absolute path, size_px from the file, and missing url/title/author/license from the sidecar."""
    ref = ref.model_copy(deep=True)
    if not ref.path:
        return ref
    p = Path(ref.path).expanduser()
    if not p.is_absolute() and base is not None and (base / p).exists():
        p = base / p
    p = p.resolve()
    ref.path = str(p)
    if ref.size_px is None:
        ref.size_px = image_size(p)
    side = sidecar_path(p)
    if side.exists():
        data = json.loads(side.read_text(encoding="utf-8"))
        for k in SIDECAR_FIELDS + FRAME_FIELDS:
            if getattr(ref, k) is None and data.get(k) not in (None, ""):
                setattr(ref, k, data[k])
    return ref


def screen_source(sc: Screen, image: str) -> dict[str, str | None]:
    """url/title/author/license of one screen image: the fields set on the screen, else its sidecar."""
    out = {k: getattr(sc, k) for k in SIDECAR_FIELDS}
    side = sidecar_path(Path(image))
    if side.exists() and any(v is None for v in out.values()):
        data = json.loads(side.read_text(encoding="utf-8"))
        out = {k: v if v is not None else data.get(k) for k, v in out.items()}
    return out


def focal_mm(cam: PhotoCamera) -> float:
    return SENSOR_W_MM / 2 / math.tan(math.radians(cam.hfov_deg) / 2)


def frame_note(ref: InputRef) -> str | None:
    """'frame at 12.3 s of walk.mp4' for an input taken from a video, else None."""
    if not ref.video:
        return None
    at = f" at {ref.time_s:.1f} s" if ref.time_s is not None else ""
    return f"frame{at} of {Path(ref.video).name}"


def provenance(ref: InputRef) -> dict[str, str | float]:
    """Source fields for USD customData / reports (no camera)."""
    d = {"id": ref.id, "kind": ref.kind}
    for k in ("path", "url", "title", "author", "license", "video", "time_s"):
        if (v := getattr(ref, k)) is not None:
            d[k] = v
    if ref.taste is not None and (t := taste_summary(ref.taste)):
        d["taste"] = t
    return d


# images taken from somewhere else: their page URL and license must be recorded
SOURCED_KINDS = ("photo", "reference")


def missing_source(ref: InputRef) -> list[str]:
    return [k for k in ("url", "license") if not getattr(ref, k)] if ref.kind in SOURCED_KINDS else []


def taste_rows(t: Taste) -> list[tuple[str, str]]:
    """(Japanese item, value) rows of a reference image's taste, for the preview and reports."""
    rows = [(ja, v) for ja, v in (("床", t.floor), ("壁", t.walls), ("天井", t.ceiling), ("照明", t.lighting)) if v]
    if t.light_kelvin:
        rows.append(("色温度", f"{t.light_kelvin:g} K"))
    if t.lighting_scale:
        rows.append(("明るさ", f"×{t.lighting_scale:g}"))
    rows += [(f"家具：{k}", v) for k, v in t.furniture.items()]
    if t.style:
        rows.append(("様式", ", ".join(t.style)))
    if t.density:
        rows.append(("物の量", t.density))
    return rows


def taste_summary(t: Taste) -> str:
    """One line: 'note / 床 light oak parquet / 壁 white paint / ...'."""
    return " / ".join([*([t.note] if t.note else []), *(f"{k} {v}" for k, v in taste_rows(t))])
