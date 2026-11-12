"""Build the index of the MDL materials bundled with Isaac Sim (src/agentic_sim_world_gen/data/mdl_<version>.jsonl.gz).

    python scripts/build_mdl_index.py --version 6.1

Steps
1. List the public S3 bucket under NVIDIA/Materials/Base and NVIDIA/Materials/vMaterials_2.
2. Download every .mdl (small text files) and read the exported materials and their annotations
   (display_name, description, key_words). A vMaterials file holds several materials (variants of one
   look); Base files hold one OmniPBR / OmniGlass material named like the file.
3. A material is indexed when it has a 256x256 thumbnail: Base <file>.mdl.png, vMaterials
   <file>.mdl@<material>.png. The representative color is the mean of the thumbnail's center
   (the thumbnails are a sphere or a close-up on white, the center is the material), averaged in
   linear RGB and stored as sRGB hex.

Records hold paths below the assets root (/NVIDIA/Materials/...), so ISAAC_ASSETS_ROOT can point at a
local copy (see agentic_sim_world_gen.materials). Categories that are no use for floors / walls / ground (gems,
liquids, car paint, food, emissives, templates) are left out.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from build_isaac_index import BUCKET, list_keys, make_session  # noqa: E402

LIBRARIES = {"Base": "NVIDIA-Omniverse-Materials", "vMaterials_2": "NVIDIA-vMaterials"}  # see agentic_sim_world_gen.license.KNOWN
SKIP_DIRS = {"Templates", "Styles", "Emissives", "Gems", "Liquids", "Food", "Carpaint", "CSV_Mapping_Files"}
CROP = 0.25  # mean over the center half of the thumbnail (25 % margin each side)


def _get(session: requests.Session, url: str, cache: Path, binary: bool = False):
    p = cache / url.removeprefix(BUCKET)
    if not p.exists():
        r = session.get(url, timeout=120)
        r.raise_for_status()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(r.content)
        tmp.replace(p)
    return p.read_bytes() if binary else p.read_text(encoding="utf-8", errors="replace")


_EXPORT = re.compile(r"export\s+material\s+(\w+)\s*\(")
_BLOCK = re.compile(r"\[\[((?:(?!\]\]).)*)\]\]\s*=", re.S)  # the material's own annotations end with "]] ="


def _anno(block: str, name: str) -> str | None:
    m = re.search(rf'::anno::{name}\(\s*"((?:[^"\\]|\\.)*)"', block)
    return m.group(1).strip() if m else None


def parse_mdl(text: str) -> list[dict]:
    """Exported materials with display_name, description and key_words."""
    starts = [(m.start(), m.group(1)) for m in _EXPORT.finditer(text)]
    out = []
    for i, (pos, name) in enumerate(starts):
        chunk = text[pos : starts[i + 1][0] if i + 1 < len(starts) else len(text)]
        m = _BLOCK.search(chunk)
        block = m.group(1) if m else ""
        kw = re.search(r"::anno::key_words\(\s*string\[\]\(([^)]*)\)", block)
        keywords = re.findall(r'"([^"]*)"', kw.group(1)) if kw else []
        title, desc = _anno(block, "display_name"), _anno(block, "description")
        if {"omni", "generic"} & {k.lower() for k in keywords} or (title or "").lower().startswith(("omni pbr", "omni glass")):
            title, desc, keywords = None, None, []  # the generic OmniPBR / OmniGlass annotations say nothing
        out.append({"name": name, "title": title, "description": desc, "keywords": keywords})
    return out


def thumb_color(png: bytes) -> str:
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = im.size
    im = im.crop((int(w * CROP), int(h * CROP), int(w * (1 - CROP)), int(h * (1 - CROP))))
    raw = im.tobytes()
    px = [raw[i : i + 3] for i in range(0, len(raw), 3)]

    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def enc(c: float) -> int:
        c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        return round(min(max(c, 0.0), 1.0) * 255)

    mean = [sum(lin(p[i]) for p in px) / len(px) for i in range(3)]
    return "#" + "".join(f"{enc(c):02x}" for c in mean)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="6.1")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cache", default=str(Path.home() / ".cache" / "agentic-sim-world-gen" / "mdl"))
    a = ap.parse_args()

    session = make_session()
    cache = Path(a.cache)
    prefix = f"Assets/Isaac/{a.version}"
    jobs = []  # (record, thumbnail url)
    keys = []
    for lib in LIBRARIES:
        keys += [k for k, _ in list_keys(f"{prefix}/NVIDIA/Materials/{lib}/", session)]
    keyset = set(keys)
    mdls = [k for k in keys if k.endswith(".mdl") and "/.thumbs/" not in k
            and not SKIP_DIRS & set(k.split("/"))]
    print(f"{len(keys)} keys, {len(mdls)} mdl files", flush=True)

    with ThreadPoolExecutor(16) as ex:
        texts = list(ex.map(lambda k: _get(session, BUCKET + k, cache), mdls))
    for k, text in zip(mdls, texts):
        rel = k.removeprefix(prefix)  # /NVIDIA/Materials/<lib>/<category>/[<sub>/]<file>.mdl
        parts = rel.strip("/").split("/")
        lib, category, sub = parts[2], parts[3], "/".join(parts[4:-1])
        d, f = os.path.split(k)
        mats = parse_mdl(text)
        for m in mats:
            thumb = next((t for t in (f"{d}/.thumbs/256x256/{f}@{m['name']}.png",
                                      f"{d}/.thumbs/256x256/{f}.png" if len(mats) == 1 or m["name"] == f[:-4] else None)
                          if t and t in keyset), None)
            if not thumb or "template" in (m["title"] or "").lower():  # templates are bare starting points
                continue
            rec = {
                # <library>/<category>/[<sub>/]<file stem>[:<material>] (the material only when it differs)
                "id": "/".join(parts[2:-1] + [f[:-4]]) + ("" if m["name"] == f[:-4] else f":{m['name']}"),
                "library": lib,
                "category": category,
                "sub": sub or None,
                "mdl": rel,
                "name": m["name"],
                "title": m["title"],
                "description": m["description"],
                "keywords": m["keywords"],
                "thumb": thumb.removeprefix(prefix),
                "license": LIBRARIES[lib],
            }
            jobs.append((rec, BUCKET + thumb))
    print(f"{len(jobs)} materials with a thumbnail", flush=True)

    def color(job):
        rec, url = job
        try:
            rec["color"] = thumb_color(_get(session, url, cache, binary=True))
        except (requests.RequestException, OSError) as e:
            print(f"  no color for {rec['id']}: {e}", flush=True)
            rec["color"] = None
        return rec

    with ThreadPoolExecutor(16) as ex:
        records = list(ex.map(color, jobs))
    records.sort(key=lambda r: r["id"])
    out = Path(a.out or ROOT / "src" / "agentic_sim_world_gen" / "data" / f"mdl_{a.version}.jsonl.gz")
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps({k: v for k, v in r.items() if v not in (None, [], "")}, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} materials -> {out} ({out.stat().st_size / 1e3:.0f} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
