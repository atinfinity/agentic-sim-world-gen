"""Third-party USD assets in a local folder (or a mounted share), indexed on first use.

    AGENTIC_SIM_WORLD_GEN_SOURCES=+local:/data/usd_assets (the defaults plus the folder)
    agentic-sim-world-gen index /data/usd_assets          # build/refresh the index and print a summary

What counts as an asset: every .usd/.usda/.usdc/.usdz file under the folder that no other file
references (sublayers, payloads, material libraries are helpers), skipping folders such as
Materials/, Textures/, payloads/, .thumbs/. A file that references many USD files from different
folders is a scene/collection; its references still count as assets.

Per asset: bbox, up axis and meters per unit from usd-core; caption from the layer documentation;
http(s) references (Isaac assets on S3, say) are
fetched as USD layers into the cache for the bbox; the record still points at the original file. Class from SimReady-style `semantic(s):*:params:semanticData` attributes if present; thumbnail from
.thumbs/256x256/<file>.png or <stem>.png/.jpg next to the file; tags from the folder names.

License (recorded for every asset): the nearest agentic-sim-world-gen.json {"license": "..."} or LICENSE/COPYING
file between the asset and the folder root. Unrecognized license files are recorded as
"see <path>" so the license report flags them.

Optional agentic-sim-world-gen.json in any folder (nearest one wins per key):
    {"license": "CC-BY-4.0", "author": "ACME", "tags": ["kitchen"], "exclude": ["*_lod*.usd"],
     "front": 270}  # or {"chair_*.usd": 270, "*": 0}: front direction [deg] in the asset frame, 0 = +X
                    # ("+X", "-Y", ... also work)

Optional asset.json next to the USD file (SimReady-style asset folders):
    {"usd": "<file>", "name": "...", "category": "...", "tags": [...], "description": "...",
     "license": "...", "author": "...", "front": "-Y",
     "image_credits": [{"title": "...", "author": "...", "url": "...", "license": "..."}],
     "screen": {"variant_set": "Screen", "variants": ["On", "Off"], "default": "On",
                "materials": [{"name": "ScreenMat", "image": "./textures/Screen.png", "off": "ScreenMat_Off"}],
                "places": ["cafe", "convenience_store"]},
     "picture": {"variant_set": "Picture", "variants": ["Default", "Blank"], "default": "Default", "off": "Blank",
                 "materials": [{"name": "PosterMat", "image": "./textures/Poster.png", "off": "PosterMat_Blank"}],
                 "places": ["convenience_store"]}}
Its tags and category are added, the description becomes the caption, and license / author / front
override agentic-sim-world-gen.json for that file. image_credits (photos and fonts used in the textures) are
listed in LICENSES.md. screen declares the materials of a lit screen (signage) whose image
ObjectSpec.screen replaces; picture declares a printed picture (poster, board, mat) the same way, whose
Blank variant is plain; places lists the Room.place values its own picture suits (default: shops,
cafés and restaurants, since built-in signage pictures are ads). Collection stages (a catalog that references every asset) are
not listed themselves.

The scan result is cached per file (mtime, size) in ~/.cache/agentic-sim-world-gen/local/, so only changed
files are read again.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from agentic_sim_world_gen import atomic

from agentic_sim_world_gen.assets.providers.base import SizeFilter
from agentic_sim_world_gen.assets.providers.offline import search_records
from agentic_sim_world_gen.assets.record import AssetRecord

EXTS = (".usd", ".usda", ".usdc", ".usdz")
SKIP_DIRS = {".thumbs", "materials", "textures", "payloads", "subusds", "source_assets", ".git", "__pycache__"}
COLLECTION_MIN = 5  # a file referencing this many USD files in >= 2 folders is a scene, not an asset
SIDECAR = "asset.json"
_TOOL_STAMP = re.compile(r"^[A-Za-z][\w .-]* v?\d+(\.\d+)+$")  # "Blender v4.4.0": who wrote the file, not what it is
_AXES = {"+x": 0.0, "x": 0.0, "+y": 90.0, "y": 90.0, "-x": 180.0, "-y": 270.0}
LICENSE_FILES = re.compile(r"^(license|licence|copying)(\.(txt|md))?$", re.I)
CACHE_VERSION = 7  # 2: remote references fetched for the bbox; 3: tool-stamp captions dropped; 4: variant sets;
# 5: asset.json `picture` read into screens; 6: the picture's text / lang / audience; 7: polygons


def default_cache_dir() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_CACHE", "~/.cache/agentic-sim-world-gen")).expanduser() / "local"


def _phrase(s: str) -> str:
    """'KLT_Bin' -> 'klt bin', 'officeChair-02' -> 'office chair 02'."""
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    return re.sub(r"[_\-.\s]+", " ", s).strip().lower()


# --- USD reading (usd-core or Isaac Sim's pxr) -------------------------------


def _deps(path: Path) -> list[str]:
    """Absolute paths of USD files this layer references (sublayers, references, payloads)."""
    if path.suffix == ".usdz":
        return []  # self-contained package
    from pxr import Sdf

    layer = Sdf.Layer.FindOrOpen(str(path))
    if layer is None:
        return []
    try:
        deps = layer.GetCompositionAssetDependencies()
    except AttributeError:  # older pxr
        deps = layer.GetExternalReferences()
    out = []
    for d in deps:
        if not d or "://" in d or not d.lower().endswith(EXTS):
            continue
        out.append(os.path.normpath(layer.ComputeAbsolutePath(d)))
    return sorted(set(out))


def _semantic_class(prim) -> str | None:
    for attr in prim.GetAttributes():
        n = attr.GetName()
        if re.match(r"semantics?:.*:params:semanticData$", n):
            v = attr.Get()
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _info(path: Path, cache_dir: Path | None = None) -> dict[str, Any] | None:
    """bbox / axes / caption / class / variants of one asset file, or None if it does not open.
    http(s) references (e.g. Isaac assets on S3) are fetched into `cache_dir` so the bbox is whole."""
    from pxr import Usd, UsdGeom

    from agentic_sim_world_gen.assets.remote_layers import openable_copy

    try:
        opened = (openable_copy(path, cache_dir) if cache_dir else None) or path
        stage = Usd.Stage.Open(str(opened), Usd.Stage.LoadAll)
    except Exception as e:  # noqa: BLE001 (pxr raises Tf.ErrorException)
        print(f"agentic-sim-world-gen: cannot open {path}: {e}", file=sys.stderr)
        return None
    if stage is None:
        return None
    root = stage.GetDefaultPrim() or stage.GetPseudoRoot()
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    rng = cache.ComputeWorldBound(root).ComputeAlignedRange()
    variants = {}
    vsets = root.GetVariantSets() if root.IsValid() and not root.IsPseudoRoot() else None
    sets = {n: list(vsets.GetVariantSet(n).GetVariantNames()) for n in vsets.GetNames()} if vsets else {}
    if vsets and "PhysX" in vsets.GetNames() and "Enabled" in vsets.GetVariantSet("PhysX").GetVariantNames():
        variants["PhysX"] = "Enabled"  # same choice as for SimReady assets
    faces = 0
    if root.IsValid():
        for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
            if prim.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(prim).ComputePurpose() != UsdGeom.Tokens.guide:
                faces += len(UsdGeom.Mesh(prim).GetFaceVertexCountsAttr().Get() or [])
    doc = stage.GetRootLayer().documentation or (root.GetMetadata("documentation") if not root.IsPseudoRoot() else None)
    return {
        "bbox_min": [0.0, 0.0, 0.0] if rng.IsEmpty() else list(rng.GetMin()),
        "bbox_max": [0.0, 0.0, 0.0] if rng.IsEmpty() else list(rng.GetMax()),
        "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
        "meters_per_unit": float(UsdGeom.GetStageMetersPerUnit(stage)),
        "caption": doc.strip() if isinstance(doc, str) and doc.strip() and not _TOOL_STAMP.match(doc.strip()) else None,
        "semantic_class": _semantic_class(root) if not root.IsPseudoRoot() else None,
        "variant_selections": variants,
        "variant_sets": sets,
        "polygons": faces,
    }


# --- folder metadata -----------------------------------------------------------


def _front_deg(v: Any) -> float | None:
    """A front direction as degrees in the asset frame: a number or an axis such as "-Y"."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        return _AXES.get(v.strip().lower())
    return None


def _screens(side: dict[str, Any], f: Path) -> list[dict[str, Any]]:
    """asset.json `screen` (lit signage) and `picture` (printed shop pictures: posters, boards, mats) ->
    AssetRecord.screens (texture paths made absolute). `off_variant` is the value that hides the asset's
    own picture (Screen=Off: a dark screen; Picture=Blank: plain paper or board)."""
    out = []
    for kind in ("screen", "picture"):
        sc = side.get(kind)
        if not isinstance(sc, dict):
            continue
        on = sc.get("default")
        off = sc.get("off") or next((v for v in sc.get("variants") or [] if v != on), None)
        for m in sc.get("materials") or []:
            if not isinstance(m, dict) or not m.get("name") or not m.get("image"):
                continue
            out.append({"kind": kind, "material": str(m["name"]), "image": str((f.parent / m["image"]).resolve()),
                        "off": m.get("off"), "variant_set": sc.get("variant_set"), "on": on, "off_variant": off,
                        "places": [str(x) for x in sc.get("places") or []], **_wording(m, sc)})
    return out


def _wording(m: dict[str, Any], sc: dict[str, Any]) -> dict[str, Any]:
    """What a picture says: `text` (the words printed on it), `lang` and `audience` (men, women, kids ...),
    from the material or else the whole `picture` / `screen` entry of asset.json."""
    out: dict[str, Any] = {}
    for k in ("text", "lang", "audience"):
        v = m.get(k, sc.get(k))
        if isinstance(v, str) and v.strip():
            v = [v.strip()]
        if isinstance(v, list) and (v := [str(x).strip() for x in v if str(x).strip()]):
            out[k] = v if k != "lang" else v[0]
    return out


def screens_of(f: Path) -> list[dict[str, Any]]:
    """Screens declared in the asset.json next to a USD file (for records saved before AssetRecord.screens)."""
    try:
        return _screens(_sidecar(f), f)
    except (OSError, ValueError):
        return []


def _sidecar(f: Path) -> dict[str, Any]:
    """asset.json next to `f` that describes it ("usd" names the file, or is absent)."""
    side = f.parent / SIDECAR
    if not side.is_file():
        return {}
    try:
        d = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"agentic-sim-world-gen: ignoring {side}: {e}", file=sys.stderr)
        return {}
    if not isinstance(d, dict) or d.get("usd") not in (None, f.name):
        return {}
    return d


class _Folders:
    """agentic-sim-world-gen.json and LICENSE lookups, nearest folder first, cached per directory."""

    def __init__(self, root: Path):
        self.root = root
        self._meta: dict[Path, dict[str, Any]] = {}
        self._lic: dict[Path, str | None] = {}

    def chain(self, d: Path) -> list[Path]:
        out = []
        while True:
            out.append(d)
            if d == self.root or self.root not in d.parents:
                return out
            d = d.parent

    def meta(self, d: Path) -> dict[str, Any]:
        if d not in self._meta:
            f = d / "agentic-sim-world-gen.json"
            try:
                self._meta[d] = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
            except (OSError, ValueError) as e:
                print(f"agentic-sim-world-gen: ignoring {f}: {e}", file=sys.stderr)
                self._meta[d] = {}
        return self._meta[d]

    def license_file(self, d: Path) -> str | None:
        if d not in self._lic:
            from agentic_sim_world_gen.license import detect

            self._lic[d] = None
            for f in sorted(d.iterdir()) if d.is_dir() else []:
                if f.is_file() and LICENSE_FILES.match(f.name):
                    try:
                        text = f.read_text(encoding="utf-8", errors="replace")[:20000]
                    except OSError:
                        continue
                    self._lic[d] = detect(text) or f"see {f}"
                    break
        return self._lic[d]

    def lookup(self, d: Path, key: str) -> Any:
        for x in self.chain(d):
            v = self.meta(x).get(key)
            if v is not None:
                return v
        return None

    def license(self, d: Path) -> str | None:
        for x in self.chain(d):  # nearest folder wins; agentic-sim-world-gen.json beats a LICENSE file in the same folder
            v = self.meta(x).get("license") or self.license_file(x)
            if v:
                return v
        return None

    def front(self, f: Path) -> float | None:
        """Front direction from the nearest agentic-sim-world-gen.json "front": a number, or {file glob: deg}."""
        for x in self.chain(f.parent):
            v = self.meta(x).get("front")
            if _front_deg(v) is not None:
                return _front_deg(v)
            if isinstance(v, dict):
                rel = f.relative_to(x).as_posix()
                for pat, deg in v.items():
                    if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(f.name, pat):
                        return _front_deg(deg)
        return None

    def excluded(self, f: Path) -> bool:
        for x in self.chain(f.parent):
            rel = f.relative_to(x).as_posix()
            if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(f.name, pat) for pat in self.meta(x).get("exclude", [])):
                return True
        return False


def _thumbnail(f: Path) -> str | None:
    cands = [f.parent / ".thumbs" / "256x256" / f"{f.name}.png"]
    cands += [f.with_suffix(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")]
    cands += [f.parent / ".thumbs" / "256x256" / f"{f.stem}.png"]
    for c in cands:
        if c.is_file():
            return c.as_uri()
    return None


# --- index -------------------------------------------------------------------------


def _walk(root: Path) -> list[Path]:
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x.lower() not in SKIP_DIRS and not x.startswith("."))
        out += [Path(d) / f for f in sorted(files) if f.lower().endswith(EXTS)]
    return out


def build_index(root: str | Path, cache_dir: str | Path | None = None, rebuild: bool = False) -> list[AssetRecord]:
    root = Path(root).expanduser().resolve()
    cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
    cache_file = cache_dir / f"{hashlib.sha1(str(root).encode()).hexdigest()[:16]}.json"
    cache: dict[str, Any] = {}
    if cache_file.exists() and not rebuild:
        try:
            c = json.loads(cache_file.read_text())
            if c.get("version") == CACHE_VERSION and c.get("root") == str(root):
                cache = c["files"]
        except ValueError:
            pass

    files = _walk(root)
    entries: dict[str, dict[str, Any]] = {}
    for f in files:
        st = f.stat()
        key = str(f)
        old = cache.get(key)
        if old and old["mtime"] == st.st_mtime and old["size"] == st.st_size:
            entries[key] = old
        else:
            entries[key] = {"mtime": st.st_mtime, "size": st.st_size, "deps": _deps(f), "info": None, "scanned": False}

    referenced: set[str] = set()
    collections: set[str] = set()
    for key, e in entries.items():
        deps = e["deps"]
        many = len(deps) >= COLLECTION_MIN and len({os.path.dirname(x) for x in deps}) >= 2
        if many or (deps and Path(key).stem.lower() == "catalog"):  # a catalog.usda, however few assets
            collections.add(key)  # a scene/catalog: what it references are assets, not helpers
            continue
        referenced.update(deps)

    folders = _Folders(root)
    records = []
    for f in files:
        key = str(f)
        if key in referenced or key in collections or folders.excluded(f):
            continue
        e = entries[key]
        if not e["scanned"]:
            e["info"], e["scanned"] = _info(f, cache_dir), True
        info = e["info"]
        if info is None:
            continue
        rel_dirs = f.parent.relative_to(root).parts
        side = _sidecar(f)
        tags = [_phrase(x) for x in rel_dirs] + [_phrase(f.stem)] + [str(t).lower() for t in folders.lookup(f.parent, "tags") or []]
        tags += [str(t).lower() for t in [side.get("category"), *(side.get("tags") or [])] if t]
        desc = side.get("description")
        side_front = _front_deg(side.get("front"))
        records.append(
            AssetRecord(
                id=f"local:{f}",
                source=f"local:{root.name}",
                uri=f.as_posix(),
                name=f.name,
                bbox_min=tuple(info["bbox_min"]),
                bbox_max=tuple(info["bbox_max"]),
                up_axis=info["up_axis"],
                meters_per_unit=info["meters_per_unit"],
                semantic_class=info["semantic_class"],
                caption=desc if isinstance(desc, str) and desc.strip() else info["caption"],
                tags=list(dict.fromkeys(t for t in tags if t)),
                license=side.get("license") or folders.license(f.parent),
                author=side.get("author") or folders.lookup(f.parent, "author"),
                credits=[c for c in side.get("image_credits") or [] if isinstance(c, dict) and c.get("title")],
                thumbnail_url=_thumbnail(f),
                variant_selections=info["variant_selections"],
                variant_sets=info.get("variant_sets") or {},
                polygons=info.get("polygons"),
                front_deg=side_front if side_front is not None else folders.front(f),
                screens=_screens(side, f),
                raw={"rel_path": f.relative_to(root).as_posix(), **({"label": side["name"]} if side.get("name") else {}),
                     # the folder's English ID; rerank treats it as the class when the USD has none
                     **({"kind": _phrase(side["slug"])} if isinstance(side.get("slug"), str) and side["slug"].strip() else {})},
            )
        )

    atomic.write(cache_file, json.dumps({"version": CACHE_VERSION, "root": str(root), "files": entries}))
    return records


class LocalFolderProvider:
    def __init__(self, root: str | Path, cache_dir: str | Path | None = None):
        self.root = Path(root).expanduser().resolve()
        self.cache_dir = cache_dir
        self.name = f"local:{self.root.name}"
        self._records: list[AssetRecord] | None = None

    @property
    def available(self) -> bool:
        return self.root.is_dir()

    def records(self, refresh: bool = False) -> list[AssetRecord]:
        if self._records is None or refresh:
            self._records = build_index(self.root, self.cache_dir)
        return self._records

    def search(self, query: str, *, limit: int = 10, size: SizeFilter | None = None) -> list[AssetRecord]:
        return search_records(self.records(), query, limit, size)
