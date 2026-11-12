"""MDL materials bundled with Isaac Sim, searched offline for floors and walls (index: scripts/build_mdl_index.py).

The index (data/mdl_<version>.jsonl.gz) holds the Omniverse Base library (one OmniPBR material per file) and
NVIDIA vMaterials 2 (several looks per file, with display names, descriptions and key words). Each record:
id, library, category, sub, mdl (path below the assets root), name (material inside the module), title,
description, keywords, thumb (path), color (sRGB hex, mean of the thumbnail center), license.

search() scores records against an English query and its synonyms (like agentic_sim_world_gen.assets.rerank for assets):
words in the name / key words / category / title count fully, the description less, and color words
("light", "white", "dark gray") are matched against the thumbnail color. The last word of the query is
its noun ("light oak *wood*", "white *tile*"): a record without it is weak (a substitute). Categories that
are unusual for the target (fabric for a floor, carpet for a wall) count less unless the query names them.

surface() turns a record into a Room.floor / walls surface: the MDL (RTX), the thumbnail color for the
preview surface and the 2D preview, and roughness / friction by category (CATEGORY).

all_records() adds the CC0 PBR materials of ambientCG and Poly Haven (agentic_sim_world_gen.pbr) with the same record
shape (no mdl; textures fetched into the cache when one is applied). records() stays the bundled MDL only
(SDG material randomization keeps to it).
"""

from __future__ import annotations

import colorsys
import gzip
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from agentic_sim_world_gen.assets.rerank import words

DATA = Path(__file__).resolve().parent / "data"
DEFAULT_VERSION = "6.1"
S3_ROOT = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.1"
WEAK = 0.5

# category -> roughness / metallic of the preview surface, physics friction, and where it is usually used
_FLOOR, _WALL, _BOTH = ("floor",), ("wall",), ("floor", "wall")
CATEGORY: dict[str, dict[str, Any]] = {
    "Wood": dict(roughness=0.45, static_friction=0.5, dynamic_friction=0.4, uses=_BOTH),
    "Stone": dict(roughness=0.5, static_friction=0.7, dynamic_friction=0.6, uses=_BOTH),
    "Ceramic": dict(roughness=0.25, static_friction=0.45, dynamic_friction=0.35, uses=_BOTH),
    "Carpet": dict(roughness=0.95, static_friction=0.8, dynamic_friction=0.7, uses=_FLOOR),
    "Concrete": dict(roughness=0.9, static_friction=0.7, dynamic_friction=0.6, uses=_BOTH),
    "Masonry": dict(roughness=0.9, static_friction=0.7, dynamic_friction=0.6, uses=_BOTH),
    "Ground": dict(roughness=1.0, static_friction=0.6, dynamic_friction=0.5, uses=_FLOOR),
    "Natural": dict(roughness=0.9, static_friction=0.6, dynamic_friction=0.5, uses=_FLOOR),
    "Composite": dict(roughness=0.5, static_friction=0.5, dynamic_friction=0.4, uses=_BOTH),
    "Architecture": dict(roughness=0.6, static_friction=0.5, dynamic_friction=0.4, uses=_BOTH),
    "Plaster": dict(roughness=0.85, static_friction=0.6, dynamic_friction=0.5, uses=_WALL),
    "Paint": dict(roughness=0.7, static_friction=0.5, dynamic_friction=0.4, uses=_WALL),
    "Wall_Board": dict(roughness=0.8, static_friction=0.5, dynamic_friction=0.4, uses=_WALL),
    "Paper": dict(roughness=0.9, static_friction=0.5, dynamic_friction=0.4, uses=_WALL),
    "Metal": dict(roughness=0.4, metallic=0.8, static_friction=0.4, dynamic_friction=0.3, uses=()),
    "Metals": dict(roughness=0.4, metallic=0.8, static_friction=0.4, dynamic_friction=0.3, uses=()),
    "Plastic": dict(roughness=0.4, static_friction=0.5, dynamic_friction=0.4, uses=()),
    "Plastics": dict(roughness=0.4, static_friction=0.5, dynamic_friction=0.4, uses=()),
    "Fabric": dict(roughness=0.95, static_friction=0.7, dynamic_friction=0.6, uses=()),
    "Textiles": dict(roughness=0.95, static_friction=0.7, dynamic_friction=0.6, uses=()),
    "Leather": dict(roughness=0.6, static_friction=0.6, dynamic_friction=0.5, uses=()),
    "Glass": dict(roughness=0.1, static_friction=0.4, dynamic_friction=0.3, uses=()),
}
_DEFAULT = dict(roughness=0.7, static_friction=0.5, dynamic_friction=0.4, uses=())
_USE_OF = {"ceiling": "wall"}  # a ceiling takes wall finishes (paint, plaster, boards)
OFF_USE = 0.6  # score factor for a category unusual for the target that the query does not name

# words that only say where the material goes
_USE_WORDS = {"floor", "flooring", "wall", "walls", "ceiling", "surface", "material", "texture", "finish", "covering", "the", "a",
              "an", "for", "of", "with", "and"}
# query word -> words that count as the same in the index
_SAME = {
    "wooden": {"wood"}, "wood": {"wood", "timber", "plank", "parquet", "laminate", "oak", "ash", "beech", "pine",
                                 "walnut", "birch", "cherry", "mahogany", "bamboo", "poplar", "plywood"},
    "tile": {"tile", "tiles", "tiled", "mosaic", "subway"}, "tiled": {"tile"},
    "parquet": {"parquet", "herringbone", "chevron", "versailles", "chantilly", "woodstrip", "stackbond", "brickbond"},
    "plank": {"plank", "planks", "board", "laminate", "woodstrip"}, "planks": {"plank"},
    "grey": {"gray"}, "gray": {"gray", "grey"},
    "carpet": {"carpet", "rug"}, "rug": {"carpet"},
    "plaster": {"plaster", "stucco"}, "stucco": {"plaster"},
    "cement": {"concrete"}, "concrete": {"concrete", "cement"},
    "painted": {"paint"}, "paint": {"paint", "painted"},
    "brick": {"brick", "bricks", "masonry"},
    "drywall": {"drywall", "board", "gypsum", "plasterboard"}, "plasterboard": {"drywall", "board", "gypsum"},
    "vinyl": {"vinyl", "linoleum", "pvc"}, "linoleum": {"linoleum", "vinyl"},
    "metal": {"metal", "steel", "aluminum", "iron"}, "stone": {"stone", "marble", "granite", "slate", "limestone",
                                                               "travertine", "sandstone", "terrazzo"},
}
_COLORS = {"white", "black", "gray", "red", "orange", "brown", "beige", "yellow", "green", "blue", "purple", "pink",
           "light", "pale", "bright", "dark"}
_SHADES = {"light", "pale", "bright", "dark"}


# MDL parameter overrides so that a material reads at room scale (whole room in view, Isaac Sim 6.1 RTX):
# (module path pattern, material name pattern, inputs). Later rules win. Applied by agentic_sim_world_gen.usd.look when the
# room is composed; Surface.mdl_inputs overrides them.
# vMaterials Wood_Tiles_<wood>[_Multicolor].mdl: 7 woods x 10 laying patterns, one 1 m texture per pattern.
_WOOD_TILES = r"/vMaterials_2/Wood/Wood_Tiles_\w+\.mdl$"
_PATTERNS = "Brickbond|Chantilly|Chevron|Cubes|Diagonal|Herringbone|Mosaic|Stackbond|Versailles|Woodstrip"
TUNING: list[tuple[str, str, dict[str, Any]]] = [
    # a near-mirror clear coat (roughness 0.04) reflects the ceiling and lights and washes the strips out: satin
    (_WOOD_TILES, r".", {"tiles_base_roughness": 0.3}),
    # ash strips differ only 10 % in brightness (the other woods 50 %): too even to see the pattern
    (r"/vMaterials_2/Wood/Wood_Tiles_Ash\.mdl$", rf"_({_PATTERNS})$", {"wood_brightness_variation": 0.4}),
    # 45-degree patterns: strips 4.4 cm wide at texture_scale 1 (the others 6.25 cm); 1.6 -> 7 cm wide
    # (herringbone / chevron strips 1.1 m long: the texture's strips are 16:1)
    (_WOOD_TILES, r"_(Herringbone|Chevron|Diagonal)$", {"texture_scale": [1.6, 1.6]}),
]


def tuning(mdl: str | None, name: str | None = None) -> dict[str, Any]:
    """MDL input overrides (TUNING) for a module URL/path (any assets root) and material name."""
    if not mdl:
        return {}
    path = mdl.split("?")[0]
    name = name or Path(path).stem
    out: dict[str, Any] = {}
    for mod, mat, inputs in TUNING:
        if re.search(mod, path) and re.search(mat, name):
            out.update(inputs)
    return out


def index_path(version: str = DEFAULT_VERSION) -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_MDL_INDEX") or DATA / f"mdl_{version}.jsonl.gz")


@lru_cache(maxsize=4)
def _load(path: str) -> tuple[dict[str, Any], ...]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return tuple(json.loads(line) for line in f if line.strip())


def records(path: str | Path | None = None) -> tuple[dict[str, Any], ...]:
    """The bundled MDL materials, or none with AGENTIC_SIM_WORLD_GEN_MDL=0 (CC0 PBR materials only: no Isaac Sim asset in the
    floors and walls). An explicit path is read either way."""
    if path is None and os.environ.get("AGENTIC_SIM_WORLD_GEN_MDL", "1").lower() in ("0", "false", "no", "off"):
        return ()
    p = Path(path) if path else index_path()
    return _load(str(p)) if p.exists() else ()


def url(path: str) -> str:
    """A path below the assets root (/NVIDIA/Materials/...) -> URL under ISAAC_ASSETS_ROOT or the public S3."""
    return (os.environ.get("ISAAC_ASSETS_ROOT") or S3_ROOT).rstrip("/") + path


def all_records() -> tuple[dict[str, Any], ...]:
    """The bundled MDL materials and the CC0 PBR ones (agentic_sim_world_gen.pbr)."""
    from agentic_sim_world_gen import pbr

    return records() + pbr.records()


def by_id(mid: str) -> dict[str, Any] | None:
    """An MDL ("Base/...", "vMaterials_2/...") or CC0 PBR ("ambientCG/...", "PolyHaven/...") record."""
    from agentic_sim_world_gen import pbr

    if mid.split("/", 1)[0] in pbr.LIBRARIES:
        return pbr.by_id(mid)
    return next((r for r in records() if r["id"] == mid), None)


def thumb_url(r: dict[str, Any]) -> str | None:
    t = r.get("thumb")
    return t if not t or "://" in t else url(t)


def by_mdl(mdl: str | None, name: str | None = None) -> dict[str, Any] | None:
    """The index record of an MDL URL/path (any assets root) and material name, or None."""
    if not mdl or "/NVIDIA/Materials/" not in mdl:
        return None
    path = "/NVIDIA/Materials/" + mdl.split("/NVIDIA/Materials/", 1)[1].split("?")[0]
    name = name or Path(path).stem
    return next((r for r in records() if r["mdl"] == path and r["name"] == name), None)


def srgb(hex_: str) -> tuple[float, float, float]:
    h = hex_.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def linear(hex_: str) -> tuple[float, float, float]:
    return tuple(round(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4, 4) for c in srgb(hex_))  # type: ignore[return-value]


def color_words(hex_: str | None) -> dict[str, float]:
    """How well color words fit a color (0..1): lightness words graded, hue / neutral words 1."""
    if not hex_:
        return {}
    r, g, b = srgb(hex_)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    clamp = lambda x: max(0.0, min(1.0, x))  # noqa: E731
    out = {"light": clamp((l - 0.35) / 0.35), "dark": clamp((0.55 - l) / 0.35)}
    out["pale"] = out["bright"] = out["light"]
    if max(r, g, b) - min(r, g, b) < 0.08 or s < 0.12:
        out["white" if l > 0.72 else "black" if l < 0.15 else "gray"] = 1.0
        if 0.6 < l <= 0.72:
            out["white"] = 0.6
    else:
        deg = h * 360
        if deg < 15 or deg >= 345:
            out["red"] = 1.0
        elif deg < 50:
            out["brown" if l < 0.55 else "beige" if s < 0.5 else "orange"] = 1.0
            out.setdefault("brown", 0.5)
        elif deg < 70:
            out["yellow" if s > 0.4 else "beige"] = 1.0
        elif deg < 170:
            out["green"] = 1.0
        elif deg < 260:
            out["blue"] = 1.0
        elif deg < 300:
            out["purple"] = 1.0
        else:
            out["pink"] = 1.0
    return out


def _split(text: str | None) -> list[str]:
    return words(re.sub(r"[_\-.:/]|(?<=[a-z])(?=[A-Z])", " ", text or ""))


def _fields(r: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    strong = set(_split(r["name"])) | set(_split(r.get("title"))) | set(_split(r["category"])) | set(_split(r.get("sub")))
    strong |= {w for k in r.get("keywords", []) for w in _split(k)}
    cat = set(_split(r["category"])) | set(_split(r.get("sub")))
    return strong, set(_split(r.get("description"))), cat


def _same(w: str) -> set[str]:
    return {w} | _SAME.get(w, set())


def score(r: dict[str, Any], query: str, alt_queries: list[str] | None = None, target: str | None = None) -> dict[str, Any]:
    """{score 0..1, matched, weak} of one record for a query (and synonyms) and target (floor / wall / ceiling)."""
    strong, weak_words, cat = _fields(r)
    colors = color_words(r.get("color"))
    use = _record_uses(r)
    # a hue named in the material ("Lime Green") overrides the thumbnail's guess (olive reads as beige)
    named_hues = (strong & _COLORS) - _SHADES
    best: dict[str, Any] = {"score": 0.0, "matched": [], "weak": True}
    for i, q in enumerate([query, *(alt_queries or [])]):
        tw = [w for w in words(q) if w not in _USE_WORDS]
        if not tw:
            continue
        nouns = [w for w in tw if w not in _COLORS] or tw
        head = nouns[-1]
        got, total, matched, head_ok = 0.0, 0.0, [], False
        for w in tw:
            wt = 1.5 if w == head else 1.0
            total += wt
            if w in _COLORS and w != head:
                # named in the material ("Antique White") and / or seen in the thumbnail; the name counts more
                # (glossy vMaterials spheres reflect a dark studio, so white tiles average out gray)
                seen = 0.0 if w not in _SHADES and named_hues and not _same(w) & named_hues else colors.get(w, 0.0)
                fit = 0.85 * bool(_same(w) & strong) + 0.6 * seen
                if fit > 0:
                    got += wt * min(1.0, fit)
                    matched.append(w if _same(w) & strong else f"{w} (color)")  # (color): thumbnail only
                continue
            if _same(w) & strong:
                got += wt
                matched.append(w)
                head_ok |= w == head
            elif _same(w) & weak_words:
                got += wt * 0.4
                matched.append(f"{w} (description)")
        s = got / total
        if target and _USE_OF.get(target, target.rstrip("s")) not in use and not any(_same(w) & cat for w in tw):
            s *= OFF_USE
        if i > 0:
            s *= 0.9
        if s > best["score"]:
            best = {"score": round(s, 3), "matched": matched, "weak": not head_ok or s < WEAK}
    return best


def _record_uses(r: dict[str, Any]) -> tuple[str, ...]:
    return tuple(r["uses"]) if r.get("uses") is not None else CATEGORY.get(r["category"], _DEFAULT)["uses"]


def has_words(r: dict[str, Any], tags: list[str]) -> bool:
    """Every tag (with its synonyms) is among the record's name / title / category / key words."""
    strong = _fields(r)[0]
    return all(_same(w) & strong for t in tags for w in words(t))


def used_for(r: dict[str, Any], target: str) -> bool:
    """The record's category is usual for a floor / walls / ceiling (CATEGORY uses)."""
    return _USE_OF.get(target, target.rstrip("s")) in _record_uses(r)


def search(query: str, alt_queries: list[str] | None = None, target: str | None = None, limit: int = 8,
           recs: tuple[dict[str, Any], ...] | None = None, per_file: int = 2) -> list[dict[str, Any]]:
    """Best records first, each with "match"; strong matches before weak ones. At most `per_file` looks of one
    MDL file come first (a vMaterials file has up to 20 variants of one pattern), the rest fill up after."""
    scored = []
    for i, r in enumerate(recs if recs is not None else records()):
        m = score(r, query, alt_queries, target)
        if m["score"] > 0:
            scored.append((m["weak"], -m["score"], i, r, m))
    scored.sort(key=lambda x: x[:3])
    seen: dict[str, int] = {}
    ranked = []  # strong, strong beyond the per-file cap, weak, weak beyond the cap
    for x in scored:
        key = _group(x[3])
        n = seen[key] = seen.get(key, 0) + 1
        ranked.append(((x[0], n > per_file), x))
    ranked.sort(key=lambda y: y[0])  # stable: score order within each group
    out = [x for _, x in ranked[:limit]]
    # side by side: the best few of each source (bundled MDL / CC0 PBR) are in the list even when the other
    # scores higher (a PBR record has no color to match "white"), replacing the last ones of the other source
    for src in {_source(x[3]) for x in scored}:
        want = min(MIN_PER_SOURCE, sum(_source(x[3]) == src and not x[0] for _, x in ranked), limit // 2)
        extra = [x for _, x in ranked[limit:] if _source(x[3]) == src and not x[0]]
        while sum(_source(x[3]) == src for x in out) < want and extra:
            i = max(i for i, x in enumerate(out) if _source(x[3]) != src)
            out[i] = extra.pop(0)
            out.sort(key=lambda x: x[:3])
    return [{**r, "match": m} for (*_, r, m) in out]


MIN_PER_SOURCE = 3


def _source(r: dict[str, Any]) -> str:
    return "mdl" if r.get("mdl") else "pbr"


def _group(r: dict[str, Any]) -> str:
    """Looks counted together for the per-file cap: one MDL file, one ambientCG series (Wallpaper001A/B/C)."""
    return r.get("mdl") or re.sub(r"(\d)[A-Z]$", r"\1", r["id"])


def surface(r: dict[str, Any]) -> dict[str, Any]:
    """A Room.floor / walls surface (agentic_sim_world_gen.scene.looks.Surface fields) for an index record. A CC0 PBR record
    downloads its texture maps into the cache first (agentic_sim_world_gen.pbr.fetch; FetchError offline)."""
    from agentic_sim_world_gen import pbr

    if pbr.is_pbr(r):
        return pbr.surface(r)
    c = CATEGORY.get(r["category"], _DEFAULT)
    out: dict[str, Any] = {"mdl": url(r["mdl"]), "mdl_name": r["name"], "roughness": c["roughness"],
                           "static_friction": c["static_friction"], "dynamic_friction": c["dynamic_friction"]}
    if c.get("metallic"):
        out["metallic"] = c["metallic"]
    if r.get("color"):
        out["color"] = list(linear(r["color"]))
    return out


def short(r: dict[str, Any], index: int | None = None) -> dict[str, Any]:
    """One candidate for an agent: index, id, title, category, color, match."""
    out = {"index": index, "id": r["id"], "name": r["name"], "title": r.get("title"),
           "category": "/".join(x for x in (r["library"], r["category"], r.get("sub")) if x),
           "color": r.get("color"), "license": r.get("license")}
    if r.get("library") in ("ambientCG", "PolyHaven"):  # agentic_sim_world_gen.pbr
        out.update(source=f"{r['library']} (CC0 PBR textures, downloaded once when applied)", size_m=r.get("size_m"),
                   url=r.get("url"))
    else:
        out["source"] = "Isaac Sim bundled MDL"
    if r.get("keywords"):
        out["keywords"] = r["keywords"][:12]
    if r.get("match"):
        out["match"] = r["match"]
    return {k: v for k, v in out.items() if v is not None}
