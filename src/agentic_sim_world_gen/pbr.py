"""CC0 PBR materials from ambientCG and Poly Haven for floors and walls.

The catalog is an offline index (data/pbr_cc0.jsonl.gz, built by scripts/build_pbr_index.py from the two sites'
public APIs) searched together with the bundled MDL materials (agentic_sim_world_gen.materials). Each record:
id ("ambientCG/Tatami001", "PolyHaven/aerial_asphalt_01"), library, category (a agentic_sim_world_gen.materials.CATEGORY
key), sub (the site's own category), name, title, description, keywords, thumb (URL), color (None: only the
tags name colors), license (CC0-1.0), url (the material's page), version, size_m (the texture's real width, or
None), uses (floor / wall from the site's tags), author, and for ambientCG the 1K-JPG zip (download url, size).

Textures are fetched only when a material is applied (fetch): the 1K JPG color, roughness and OpenGL normal
maps go to AGENTIC_SIM_WORLD_GEN_CACHE/pbr/<library>/<name>/ with a manifest.json (where they came from, version, sha256 of
each file, mean color). After that the material works offline. Only the two sites' fixed download URLs are
used (DOWNLOAD_PREFIXES), never a URL from elsewhere. Surfaces refer to the maps as "pbr:<id>/<map>.jpg";
agentic_sim_world_gen.usd.look copies them into the scene's textures/ (fetching them again if the cache was cleared).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import time
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from agentic_sim_world_gen import atomic

DATA = Path(__file__).resolve().parent / "data"
INDEX_NAME = "pbr_cc0.jsonl.gz"
LICENSE = "CC0-1.0"
REF = "pbr:"
LIBRARIES = {"ambientCG": "https://ambientcg.com/view?id={name}", "PolyHaven": "https://polyhaven.com/a/{name}"}
# the only URLs textures are fetched from
DOWNLOAD_PREFIXES = {
    "ambientCG": ("https://ambientcg.com/get?file=",),
    "PolyHaven": ("https://api.polyhaven.com/files/", "https://dl.polyhaven.org/file/ph-assets/Textures/"),
}
MAPS = ("color", "roughness", "normal")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,99}$")
MAX_DOWNLOAD = 200 * 1024 * 1024  # a 1K-JPG zip is ~10 MB
DEFAULT_SIZE_M = 2.0  # ambientCG gives no real size for most materials; theirs read about 2 m across
from agentic_sim_world_gen import UA


class FetchError(RuntimeError):
    pass


# ---------------------------------------------------------------- catalog records (built from the site APIs)

# ambientCG displayCategory -> agentic_sim_world_gen.materials.CATEGORY key; None = not a floor / wall material
_ACG_CATEGORY = {
    "Wood": "Wood", "Wood Floor": "Wood", "Planks": "Wood", "Bamboo": "Wood", "Painted Wood": "Wood",
    "Wood Siding": "Wood", "Wicker": "Wood", "Chipboard": "Composite", "Cork": "Composite",
    "Bricks": "Masonry", "Painted Bricks": "Masonry", "Facade": "Masonry", "Roofing Tiles": "Masonry",
    "Clay": "Masonry", "Glazed Terracotta": "Ceramic", "Tiles": "Ceramic", "Porcelain": "Ceramic",
    "Marble": "Stone", "Granite": "Stone", "Onyx": "Stone", "Travertine": "Stone", "Terrazzo": "Stone",
    "Rock": "Stone", "Rocks": "Stone", "Paving Stones": "Stone", "Tactile Paving": "Stone",
    "Concrete": "Concrete", "Asphalt": "Concrete", "Road": "Concrete", "Pathway": "Ground",
    "Ground": "Ground", "Gravel": "Ground", "Grass": "Natural", "Moss": "Natural", "Snow": "Ground",
    "Ice": "Ground", "Wood Chips": "Natural", "Bark": "Natural", "Tatami": "Natural", "Thatched Roof": "Natural",
    "Plaster": "Plaster", "Painted Plaster": "Plaster", "Paint": "Paint", "Wallpaper": "Paper", "Paper": "Paper",
    "Cardboard": "Paper", "Office Ceiling": "Wall_Board", "Acoustic Foam": "Wall_Board",
    "Styrofoam": "Wall_Board", "Foam": "Wall_Board", "Carpet": "Carpet", "Fabric": "Fabric", "Leather": "Leather",
    "Metal": "Metal", "Metal Plates": "Metal", "Metal Walkway": "Metal", "Diamond Plate": "Metal",
    "Corrugated Steel": "Metal", "Sheet Metal": "Metal", "Painted Metal": "Metal", "Rust": "Metal",
    "Plastic": "Plastic", "Rubber": "Plastic",
}
# Poly Haven categories, first hit wins
_PH_CATEGORY = [
    ("carpet", "Carpet"), ("tiles", "Ceramic"), ("brick", "Masonry"), ("roofing", "Masonry"),
    ("plaster", "Plaster"), ("wood", "Wood"), ("raw wood", "Wood"), ("concrete", "Concrete"),
    ("plaster-concrete", "Concrete"), ("asphalt", "Concrete"), ("road", "Concrete"), ("cobblestone", "Stone"),
    ("sandstone", "Stone"), ("rock", "Stone"), ("metal", "Metal"), ("leather", "Leather"), ("fabric", "Fabric"),
    ("bark", "Natural"), ("snow", "Ground"), ("sand", "Ground"), ("gravel", "Ground"), ("terrain", "Ground"),
]
_PH_SKIP = {"food"}
_USE_TAGS = {"floor": "floor", "flooring": "floor", "ground": "floor", "road": "floor", "wall": "wall",
             "walls": "wall", "ceiling": "wall", "facade": "wall"}


def _uses(category: str, tags: list[str]) -> list[str]:
    from agentic_sim_world_gen.materials import CATEGORY

    uses = set(CATEGORY.get(category, {}).get("uses", ()))
    uses |= {_USE_TAGS[t] for t in tags if t in _USE_TAGS}
    return sorted(uses)


def from_ambientcg(raw: dict[str, Any]) -> dict[str, Any] | None:
    """An index record for one asset of the ambientCG full_json API, or None (not a floor / wall material)."""
    name, cat = raw.get("assetId") or "", _ACG_CATEGORY.get(raw.get("displayCategory") or "")
    maps = set(raw.get("maps") or [])
    if not NAME_RE.match(name) or cat is None or "opacity" in maps or "color" not in maps:
        return None
    cats = (raw.get("downloadFolders") or {}).get("default", {}).get("downloadFiletypeCategories")
    downloads = (cats.get("zip") or {}).get("downloads") or [] if isinstance(cats, dict) else []
    dl = next((d for d in downloads if d.get("attribute") == "1K-JPG"), None)
    if not dl or not str(dl.get("downloadLink", "")).startswith(DOWNLOAD_PREFIXES["ambientCG"]):
        return None
    tags = [t for t in raw.get("tags") or [] if not t.isdigit()]
    size = raw.get("dimensionX")
    return {
        "id": f"ambientCG/{name}", "library": "ambientCG", "category": cat, "sub": raw.get("displayCategory"),
        "name": name, "title": raw.get("displayName") or name, "description": raw.get("description") or None,
        "keywords": tags, "thumb": (raw.get("previewImage") or {}).get("256-JPG-FFFFFF"), "color": None,
        "license": LICENSE, "url": LIBRARIES["ambientCG"].format(name=name),
        "version": (raw.get("releaseDate") or "")[:10] or None,
        "size_m": round(size / 100, 3) if isinstance(size, (int, float)) and size > 0 else None,
        "uses": _uses(cat, tags), "author": "ambientCG",
        "download": {"url": dl["downloadLink"], "size": dl.get("size")},
    }


def from_polyhaven(name: str, raw: dict[str, Any]) -> dict[str, Any] | None:
    """An index record for one texture of the Poly Haven assets API, or None."""
    cats = [c for c in raw.get("categories") or [] if not c.startswith("collection")]
    if not NAME_RE.match(name) or raw.get("type") != 1 or _PH_SKIP & set(cats):
        return None
    cat = next((v for k, v in _PH_CATEGORY if k in cats), None)
    if cat is None:
        return None
    tags = list(dict.fromkeys([*(raw.get("tags") or []), *cats]))
    use = (raw.get("attributes") or {}).get("surface_use")
    dims = raw.get("dimensions") or []
    return {
        "id": f"PolyHaven/{name}", "library": "PolyHaven", "category": cat, "sub": raw.get("category"),
        "name": name, "title": raw.get("name") or name, "description": raw.get("description") or None,
        "keywords": tags, "thumb": f"https://cdn.polyhaven.com/asset_img/thumbs/{name}.png?width=256&height=256",
        "color": None, "license": LICENSE, "url": LIBRARIES["PolyHaven"].format(name=name),
        "version": raw.get("files_hash"),
        "size_m": round(dims[0] / 1000, 3) if dims and dims[0] > 0 else None,
        "uses": _uses(cat, tags + ([use] if use else [])), "author": ", ".join(raw.get("authors") or {}) or "Poly Haven",
        "published": time.strftime("%Y-%m-%d", time.gmtime(raw["date_published"])) if raw.get("date_published") else None,
    }


# ---------------------------------------------------------------- the index

def index_path() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_PBR_INDEX") or DATA / INDEX_NAME)


@lru_cache(maxsize=4)
def _load(path: str) -> tuple[dict[str, Any], ...]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return tuple(json.loads(line) for line in f if line.strip())


def records() -> tuple[dict[str, Any], ...]:
    """The CC0 materials, or none with AGENTIC_SIM_WORLD_GEN_PBR=0 (bundled MDL materials only)."""
    if os.environ.get("AGENTIC_SIM_WORLD_GEN_PBR", "1").lower() in ("0", "false", "no", "off"):
        return ()
    p = index_path()
    return _load(str(p)) if p.exists() else ()


def is_pbr(r: dict[str, Any] | None) -> bool:
    return bool(r) and r.get("library") in LIBRARIES


def by_id(mid: str) -> dict[str, Any] | None:
    return next((r for r in records() if r["id"] == mid), None)


# ---------------------------------------------------------------- fetching the maps

def cache_root() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_CACHE", "~/.cache/agentic-sim-world-gen")).expanduser() / "pbr"


def _dir(r: dict[str, Any]) -> Path:
    lib, name = r["library"], r["name"]
    if lib not in LIBRARIES or not NAME_RE.match(name):
        raise FetchError(f"not a CC0 PBR material id: {r.get('id')!r}")
    return cache_root() / lib / name


def _manifest(d: Path) -> dict[str, Any] | None:
    try:
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return m if all((d / f).is_file() for f in m.get("files", {})) else None


def cached(r: dict[str, Any]) -> dict[str, Any] | None:
    """The manifest of an already fetched material, or None."""
    return _manifest(_dir(r))


def _allowed(lib: str, url: str) -> None:
    if not url.startswith(DOWNLOAD_PREFIXES[lib]):
        raise FetchError(f"{lib}: refusing to download from {url} (not one of {DOWNLOAD_PREFIXES[lib]})")


def _get(lib: str, url: str, timeout: float) -> bytes:
    import requests

    _allowed(lib, url)
    try:
        with requests.get(url, headers=UA, timeout=timeout, stream=True) as resp:
            resp.raise_for_status()
            buf = io.BytesIO()
            for chunk in resp.iter_content(1 << 16):
                buf.write(chunk)
                if buf.tell() > MAX_DOWNLOAD:
                    raise FetchError(f"{url}: larger than {MAX_DOWNLOAD >> 20} MB")
            return buf.getvalue()
    except requests.RequestException as e:
        raise FetchError(f"{lib}: could not download {url}: {e}") from e


def _ambientcg_maps(r: dict[str, Any], timeout: float) -> tuple[dict[str, bytes], list[str], dict[str, str]]:
    url = (r.get("download") or {}).get("url") or f"https://ambientcg.com/get?file={r['name']}_1K-JPG.zip"
    data = _get("ambientCG", url, timeout)
    want = {"color": "_Color.jpg", "roughness": "_Roughness.jpg", "normal": "_NormalGL.jpg"}
    out: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                base = info.filename
                if "/" in base or "\\" in base or info.file_size > MAX_DOWNLOAD:  # only top-level files
                    continue
                for m, suffix in want.items():
                    if base == f"{r['name']}_1K-JPG{suffix}":
                        out[m] = z.read(info)
    except zipfile.BadZipFile as e:
        raise FetchError(f"ambientCG: {url} is not a zip file") from e
    return out, [url], {"zip_sha256": hashlib.sha256(data).hexdigest()}


def _polyhaven_maps(r: dict[str, Any], timeout: float) -> tuple[dict[str, bytes], list[str], dict[str, str]]:
    api = f"https://api.polyhaven.com/files/{r['name']}"
    try:
        files = json.loads(_get("PolyHaven", api, timeout))
    except ValueError as e:
        raise FetchError(f"PolyHaven: bad file list from {api}") from e
    keys = {"color": ("Diffuse", "diffuse", "diff"), "roughness": ("Rough", "rough"), "normal": ("nor_gl",)}
    out, urls, md5 = {}, [], {}
    for m, names in keys.items():
        f = next((files[k]["1k"]["jpg"] for k in names if isinstance(files.get(k), dict)
                  and "jpg" in (files[k].get("1k") or {})), None)
        if f is None:
            continue
        data = _get("PolyHaven", f["url"], timeout)
        if f.get("md5") and hashlib.md5(data).hexdigest() != f["md5"]:
            raise FetchError(f"PolyHaven: {f['url']} does not match its md5")
        out[m], md5[f"{m}_md5"] = data, f.get("md5")
        urls.append(f["url"])
    return out, urls, {k: v for k, v in md5.items() if v}


def _mean_color(data: bytes) -> str | None:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            r, g, b = im.convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return None


def fetch(r: dict[str, Any], timeout: float = 120) -> dict[str, Any]:
    """Download the 1K JPG maps of a record into the cache (once) and return its manifest."""
    d = _dir(r)
    if (m := _manifest(d)) is not None:
        return m
    maps, urls, extra = (_ambientcg_maps if r["library"] == "ambientCG" else _polyhaven_maps)(r, timeout)
    if "color" not in maps:
        raise FetchError(f"{r['id']}: the download has no color map")
    files = {}
    for m, data in maps.items():  # whole files under names of this process's own: two servers may fetch it
        atomic.write(d / f"{m}.jpg", data)
        files[f"{m}.jpg"] = hashlib.sha256(data).hexdigest()
    man = {"id": r["id"], "library": r["library"], "name": r["name"], "title": r.get("title"),
           "url": r.get("url"), "download": urls, "version": r.get("version"), "license": r.get("license", LICENSE),
           "author": r.get("author"), "size_m": r.get("size_m"), "category": r.get("category"),
           "color": _mean_color(maps["color"]), "files": files, "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           **extra}
    atomic.write(d / "manifest.json", json.dumps(man, ensure_ascii=False, indent=1))  # last: it says "fetched"
    return man


# ---------------------------------------------------------------- surfaces and texture refs

def ref(r: dict[str, Any], m: str) -> str:
    return f"{REF}{r['id']}/{m}.jpg"


def parse_ref(s: str) -> tuple[str, str, str] | None:
    """"pbr:ambientCG/Tatami001/color.jpg" -> (library, name, file), or None."""
    if not s.startswith(REF):
        return None
    parts = s[len(REF):].split("/")
    if len(parts) != 3 or parts[0] not in LIBRARIES or not NAME_RE.match(parts[1]) \
            or parts[2] not in {f"{m}.jpg" for m in MAPS}:
        return None
    return parts[0], parts[1], parts[2]


def resolve_ref(s: str, timeout: float = 120) -> Path:
    """The cached file of a "pbr:" texture ref, fetched again if the cache was cleared."""
    p = parse_ref(s)
    if p is None:
        raise ValueError(f"bad PBR texture ref {s!r} (expected pbr:<ambientCG|PolyHaven>/<name>/<map>.jpg)")
    lib, name, file = p
    path = cache_root() / lib / name / file
    if not path.is_file():
        r = by_id(f"{lib}/{name}") or {"id": f"{lib}/{name}", "library": lib, "name": name}
        fetch(r, timeout)
        if not path.is_file():
            raise FetchError(f"{lib}/{name} has no {file}")
    return path


def manifest_of(texture: str | None) -> dict[str, Any] | None:
    """The cached manifest (or else the index record) of the material a "pbr:" texture ref belongs to."""
    p = parse_ref(texture or "")
    if p is None:
        return None
    lib, name, _ = p
    return _manifest(cache_root() / lib / name) or by_id(f"{lib}/{name}") or {"id": f"{lib}/{name}", "library": lib,
                                                                              "name": name, "license": LICENSE}


def surface(r: dict[str, Any], timeout: float = 120) -> dict[str, Any]:
    """A Room.floor / walls surface for a record: fetches its maps (once) and points the surface at them."""
    from agentic_sim_world_gen.materials import CATEGORY, _DEFAULT, linear

    man = fetch(r, timeout)
    c = CATEGORY.get(r["category"], _DEFAULT)
    out: dict[str, Any] = {"texture": ref(r, "color"), "tile_m": r.get("size_m") or DEFAULT_SIZE_M,
                           "roughness": c["roughness"], "static_friction": c["static_friction"],
                           "dynamic_friction": c["dynamic_friction"]}
    if "normal.jpg" in man["files"]:
        out["normal"] = ref(r, "normal")
    if "roughness.jpg" in man["files"]:
        out["roughness_texture"] = ref(r, "roughness")
    if c.get("metallic"):
        out["metallic"] = c["metallic"]
    if man.get("color"):
        out["color"] = list(linear(man["color"]))
    return out


def credit(m: dict[str, Any]) -> str:
    who = m.get("author") or m["library"]
    by = f" by {who}" if who not in (m["library"], "Poly Haven") else ""
    return (f"\"{m.get('title') or m['name']}\"{by}, {m['library']} ({m.get('url') or m['id']}), CC0 1.0"
            + (f", version {m['version']}" if m.get("version") else ""))
