"""CC0 3D models from Poly Haven: offline catalog, download on use, a copy in the scene.

The catalog is an offline index (data/polyhaven_models.jsonl.gz, built by scripts/build_polyhaven_models_index.py
from https://api.polyhaven.com/assets?t=models; metadata only). Each record: id (the Poly Haven slug), title,
category (the site's path, "Furniture/Tables/Dining Tables"), categories, collections, tags, dimensions_mm
(x, y, z of the model in its own Z-up frame: z is the height), thumb (URL), version (files_hash), license
(CC0-1.0), author, url (the model's page), published, description, polycount. agentic_sim_world_gen.assets.providers.polyhaven
searches it and turns records into AssetRecords whose uri is "polyhaven:<id>".

Files are fetched only when a model is composed (fetch): Poly Haven ships every model as USD (a .usdc exported
from Blender, Z-up, meters, with UsdPreviewSurface materials and textures/ next to it), so no conversion is
needed. The 1k .usdc and its textures go to AGENTIC_SIM_WORLD_GEN_CACHE/polyhaven_models/<id>/ with their md5 checked, plus
model.usda: a wrapper that references the .usdc and moves its bounding box's bottom center to the origin (Z-up,
meters), and manifest.json (download URLs, files_hash, sha256 of every file, the measured size). Only the fixed
Poly Haven URLs are used (DOWNLOAD_PREFIXES); file names from the API are checked before anything is written.
compose copies that folder into the scene (assets/polyhaven/<id>/) and references it by a relative path, so a
scene folder works on another machine without the cache.

Physics: the models bring no colliders or rigid bodies, so AssetRecord.has_physics is False and collider=auto
gives them a static box collider (agentic_sim_world_gen.usd.compose.add_box_collider), like the SimReady assets without
physics. Front: Blender's front view looks along +Y, so the models face -Y (FRONT_DEG = 270).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import time
import gzip
from functools import lru_cache
from pathlib import Path
from typing import Any

from agentic_sim_world_gen import atomic

DATA = Path(__file__).resolve().parent / "data"
INDEX_NAME = "polyhaven_models.jsonl.gz"
LICENSE = "CC0-1.0"
SOURCE = "polyhaven"
REF = "polyhaven:"
PAGE = "https://polyhaven.com/a/{name}"
THUMB = "https://cdn.polyhaven.com/asset_img/thumbs/{name}.png?width=256&height=256"
FILES_API = "https://api.polyhaven.com/files/{name}"
# the only URLs anything is fetched from
DOWNLOAD_PREFIXES = ("https://api.polyhaven.com/files/", "https://dl.polyhaven.org/file/ph-assets/Models/")
RES = "1k"
FRONT_DEG = 270.0
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,99}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,150}$")
TEXTURE_EXTS = (".jpg", ".jpeg", ".png", ".exr")
USD_EXTS = (".usdc", ".usd", ".usda")
MAX_DOWNLOAD = 200 * 1024 * 1024  # a 1k model with its textures is a few MB
WRAPPER = "model.usda"
# 2: alpha maps wired to the UsdPreviewSurface opacity (leaf cards)
WRAPPER_VERSION = 2
SCENE_DIR = "assets/polyhaven"
from agentic_sim_world_gen import UA


class FetchError(RuntimeError):
    pass


# ---------------------------------------------------------------- catalog records (from the assets API)

def from_api(name: str, raw: dict[str, Any]) -> dict[str, Any] | None:
    """An index record for one model of https://api.polyhaven.com/assets?t=models, or None."""
    dims = raw.get("dimensions") or []
    if not NAME_RE.match(name) or raw.get("type") != 2 or len(dims) != 3 or not all(
            isinstance(v, (int, float)) and v > 0 for v in dims):
        return None
    cats = raw.get("categories") or []
    return {
        "id": name, "title": raw.get("name") or name, "category": raw.get("category") or None,
        "categories": [c for c in cats if not c.startswith("collection")],
        "collections": [c.split(":", 1)[1].strip() for c in cats if c.startswith("collection:")],
        "tags": list(dict.fromkeys(raw.get("tags") or [])), "dimensions_mm": [round(float(v), 1) for v in dims],
        "thumb": THUMB.format(name=name), "version": raw.get("files_hash"), "license": LICENSE,
        "author": ", ".join(raw.get("authors") or {}) or "Poly Haven", "url": PAGE.format(name=name),
        "published": time.strftime("%Y-%m-%d", time.gmtime(raw["date_published"])) if raw.get("date_published") else None,
        "description": raw.get("description") or None, "polycount": raw.get("polycount"),
    }


def _sing(w: str) -> str:
    from agentic_sim_world_gen.assets.rerank import _sing as sing

    return sing(w)


def kind_phrases(category: str | None) -> list[str]:
    """Things the site's category path names: "Furniture/Tables/Side & End Tables" ->
    ["side table", "end table", "table"]; "Furniture/Seating/Stools & Bar Seating" -> ["stool", "bar seating", "seating"]."""
    out: list[str] = []
    for seg in reversed((category or "").split("/")[1:]):
        parts = [p.strip().lower() for p in re.split(r"\s*(?:&|,|\band\b)\s*", seg) if p.strip()]
        if not parts:
            continue
        noun = parts[-1].split()[-1]
        for p in parts:
            ws = p.split()
            if len(ws) == 1 and not ws[0].endswith("s") and ws[0] != noun:  # "side" of "Side & End Tables"
                ws = [ws[0], noun]
            out.append(" ".join([*ws[:-1], _sing(ws[-1])]))
    return list(dict.fromkeys(out))


def semantic_class(r: dict[str, Any]) -> str:
    """The model's title without the number: "Dining Chair 02" -> "dining chair"."""
    words = [w for w in re.split(r"[\s_]+", r.get("title") or r["id"]) if w and not re.fullmatch(r"\d+[a-z]?", w, re.I)]
    return " ".join(words).lower() or r["id"].lower()


def caption(r: dict[str, Any]) -> str | None:
    """The description without the "Free 8K model:" lead."""
    d = re.sub(r"^\s*free\s+\w+\s+(?:3d\s+)?model\s*[:,-]\s*", "", r.get("description") or "", flags=re.I).strip()
    return d or None


def to_asset_record(r: dict[str, Any], measured: dict[str, Any] | None = None):
    """The AssetRecord of an index record. The bbox is centered on the footprint with the bottom at z=0, like the
    wrapper (model.usda) puts the model; once fetched, the measured size replaces Poly Haven's dimensions."""
    from agentic_sim_world_gen.assets.record import AssetRecord

    size = (measured or {}).get("size_m") or [v / 1000 for v in r["dimensions_mm"]]
    w, d, h = (float(v) for v in size)
    tags = list(dict.fromkeys([*kind_phrases(r.get("category")), *r.get("tags", []), *r.get("categories", [])]))
    return AssetRecord(
        id=f"{REF}{r['id']}", source=SOURCE, uri=f"{REF}{r['id']}", name=r["id"],
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
        semantic_class=semantic_class(r), caption=caption(r), tags=tags, license=r.get("license") or LICENSE,
        author=r.get("author"), rigid_body_count=0, thumbnail_url=r.get("thumb"), front_deg=FRONT_DEG,
    )


# ---------------------------------------------------------------- the index

def index_path() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_POLYHAVEN_INDEX") or DATA / INDEX_NAME)


@lru_cache(maxsize=4)
def _load(path: str) -> tuple[dict[str, Any], ...]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return tuple(json.loads(line) for line in f if line.strip())


def records() -> tuple[dict[str, Any], ...]:
    p = index_path()
    return _load(str(p)) if p.exists() else ()


def by_id(name: str) -> dict[str, Any] | None:
    return next((r for r in records() if r["id"] == name), None)


def parse_ref(uri: str) -> str | None:
    """"polyhaven:dining_chair_02" -> "dining_chair_02", or None."""
    if not uri.startswith(REF):
        return None
    name = uri[len(REF):]
    return name if NAME_RE.match(name) else None


# ---------------------------------------------------------------- fetching

def cache_root() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_CACHE", "~/.cache/agentic-sim-world-gen")).expanduser() / "polyhaven_models"


def _dir(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise FetchError(f"not a Poly Haven model id: {name!r}")
    return cache_root() / name


def _manifest(d: Path) -> dict[str, Any] | None:
    try:
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return m if m.get("files") and all((d / f).is_file() for f in m["files"]) else None


def cached(name: str) -> dict[str, Any] | None:
    """The manifest of an already fetched model, or None."""
    try:
        return _manifest(_dir(name))
    except FetchError:
        return None


def _allowed(url: str) -> None:
    if not isinstance(url, str) or not url.startswith(DOWNLOAD_PREFIXES):
        raise FetchError(f"Poly Haven: refusing to download from {url!r} (not one of {DOWNLOAD_PREFIXES})")


def _get(url: str, timeout: float) -> bytes:
    import requests

    _allowed(url)
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
        raise FetchError(f"Poly Haven: could not download {url}: {e}") from e


def _checked(f: dict[str, Any], timeout: float) -> bytes:
    data = _get(f.get("url"), timeout)
    if f.get("md5") and hashlib.md5(data).hexdigest() != f["md5"]:
        raise FetchError(f"Poly Haven: {f['url']} does not match its md5")
    return data


def _by_basename(files: Any, res: str) -> dict[str, dict[str, Any]]:
    """Every downloadable file of the files API at one resolution, by file name (the maps, every format)."""
    out: dict[str, dict[str, Any]] = {}

    def walk(x: Any, path: tuple[str, ...]) -> None:
        if isinstance(x, dict):
            if isinstance(x.get("url"), str) and res in path:
                out.setdefault(x["url"].rsplit("/", 1)[-1], x)
            for k, v in x.items():
                walk(v, (*path, k))

    walk(files, ())
    return out


def _texture_refs(usd_file: Path) -> list[tuple[str, str, str]]:
    """(shader prim path, attribute, asset path as written) of the files the shaders of a model read."""
    from pxr import Sdf, Usd, UsdShade

    stage = Usd.Stage.Open(str(usd_file), load=Usd.Stage.LoadNone)
    out = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        for a in prim.GetAttributes():
            if a.GetTypeName() == Sdf.ValueTypeNames.Asset and (v := a.Get()) is not None and v.path:
                out.append((str(prim.GetPath()), a.GetName(), v.path))
    return out


ALPHA_RE = re.compile(r"_alpha_\d+k\.", re.IGNORECASE)


def _cutouts(src) -> list[tuple[Any, tuple[str, str, str], Any]]:
    """Leaf cards and the like: a material whose UsdPreviewSurface has a constant opacity but whose other graph
    (Blender's MaterialX) reads an `*_alpha_<res>k.*` map. RTX draws the UsdPreviewSurface, so the cards come out
    as dark opaque quads. Returns (surface shader prim, (alpha shader path, attribute, file), the `st` source of
    the surface's color texture)."""
    from pxr import Sdf, Usd, UsdShade

    out = []
    for prim in src.Traverse():
        if not prim.IsA(UsdShade.Material):
            continue
        surface = alpha = st_src = None
        for p in Usd.PrimRange(prim):
            if not p.IsA(UsdShade.Shader):
                continue
            sh = UsdShade.Shader(p)
            if sh.GetIdAttr().Get() == "UsdPreviewSurface":
                surface = sh
            for a in p.GetAttributes():
                if a.GetTypeName() == Sdf.ValueTypeNames.Asset and (v := a.Get()) is not None \
                        and ALPHA_RE.search(v.path) and alpha is None:
                    alpha = (str(p.GetPath()), a.GetName(), v.path)
        if surface is None or alpha is None:
            continue
        op = surface.GetInput("opacity")
        if op and op.HasConnectedSource():
            continue  # the exporter already wired it
        color = surface.GetInput("diffuseColor")
        if color and color.HasConnectedSource():
            tex = UsdShade.Shader(color.GetConnectedSources()[0][0].source.GetPrim())
            st_in = tex.GetInput("st")
            if st_in and st_in.HasConnectedSource():
                s = st_in.GetConnectedSources()[0][0]
                st_src = (s.source.GetPath(), s.sourceName)
        out.append((surface.GetPrim(), alpha, st_src))
    return out


def _add_cutout(st, geom: Any, src_root: Any, surface, file: str, st_src) -> None:
    """In the wrapper: a UsdUVTexture of the alpha map next to the surface, wired to its opacity, with an opacity
    threshold (a cutout, not a blend)."""
    from pxr import Sdf, UsdShade

    def here(p):
        return geom.AppendPath(Sdf.Path(p).MakeRelativePath(src_root))

    surf_path = here(surface.GetPath())
    tex = UsdShade.Shader.Define(st, surf_path.GetParentPath().AppendChild("agentic_sim_world_gen_alpha"))
    tex.CreateIdAttr("UsdUVTexture")
    tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(file))
    tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")
    for w in ("wrapS", "wrapT"):
        tex.CreateInput(w, Sdf.ValueTypeNames.Token).Set("repeat")
    if st_src is not None:
        tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(here(st_src[0]).AppendProperty(
            f"outputs:{st_src[1]}"))
    out = tex.CreateOutput("r", Sdf.ValueTypeNames.Float)
    over = UsdShade.Shader(st.OverridePrim(surf_path))
    over.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(out)
    over.CreateInput("opacityThreshold", Sdf.ValueTypeNames.Float).Set(0.5)


def _write_wrapper(d: Path, usd_name: str, r: dict[str, Any],
                   repoint: list[tuple[str, str, str]] = ()) -> dict[str, Any]:
    """model.usda: Z-up, meters, the model's bbox bottom center at the origin. /Model (translate) / Geom
    (references the .usdc, scale for its metersPerUnit, rotateX for a Y-up file). `repoint`: (shader prim path in
    the .usdc, attribute, new path) for textures the .usdc names by a path that is not next to it (the 1k USD of
    dining_table reads /mnt/prod/Assets/...): the wrapper overrides them. Returns the measurements."""
    from pxr import Gf, Sdf, Usd, UsdGeom

    src = Usd.Stage.Open(str(d / usd_name), load=Usd.Stage.LoadAll)
    up, mpu = UsdGeom.GetStageUpAxis(src), UsdGeom.GetStageMetersPerUnit(src) or 1.0
    path = d / WRAPPER
    if path.exists():
        path.unlink()
    st = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    root = UsdGeom.Xform.Define(st, "/Model")
    st.SetDefaultPrim(root.GetPrim())
    Usd.ModelAPI(root.GetPrim()).SetKind("component")
    geom = UsdGeom.Xform.Define(st, "/Model/Geom")
    geom.GetPrim().GetReferences().AddReference(f"./{usd_name}")
    src_root = src.GetDefaultPrim()
    if not src_root:
        raise FetchError(f"Poly Haven {r['id']}: {usd_name} has no defaultPrim")
    for prim_path, attr, new in repoint:
        rel = Sdf.Path(prim_path).MakeRelativePath(src_root.GetPath())
        over = st.OverridePrim(Sdf.Path("/Model/Geom").AppendPath(rel))
        over.CreateAttribute(attr, Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(new))
    moved = {(p, a): new for p, a, new in repoint}
    for surface, alpha, st_src in _cutouts(src):
        _add_cutout(st, Sdf.Path("/Model/Geom"), src_root.GetPath(), surface, moved.get(alpha, alpha[2]), st_src)
    gx = UsdGeom.Xformable(geom)
    if abs(mpu - 1.0) > 1e-9:
        gx.AddScaleOp().Set(Gf.Vec3f(mpu, mpu, mpu))
    if up == UsdGeom.Tokens.y:
        gx.AddRotateXOp().Set(90.0)
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    box = bc.ComputeWorldBound(root.GetPrim()).ComputeAlignedRange()
    if box.IsEmpty():
        raise FetchError(f"Poly Haven {r['id']}: the model has no geometry")
    mn, mx = box.GetMin(), box.GetMax()
    c = Gf.Vec3d((mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, mn[2])
    UsdGeom.Xformable(root).AddTranslateOp().Set(-c)
    prim = root.GetPrim()
    prim.SetAssetInfo({"name": r.get("title") or r["id"], "identifier": f"{REF}{r['id']}",
                       "version": str(r.get("version") or "")})
    prim.SetCustomDataByKey("agentic_sim_world_gen:polyhaven", {k: str(v) for k, v in {
        "id": r["id"], "url": r.get("url") or PAGE.format(name=r["id"]), "license": r.get("license") or LICENSE,
        "author": r.get("author"), "version": r.get("version")}.items() if v})
    st.GetRootLayer().documentation = (f"{r.get('title') or r['id']} by {r.get('author') or 'Poly Haven'}, "
                                      f"{PAGE.format(name=r['id'])}, CC0 1.0 (wrapper written by agentic-sim-world-gen)")
    st.GetRootLayer().Save()
    size = [round(float(mx[i] - mn[i]), 4) for i in range(3)]
    return {"size_m": size, "source_up_axis": str(up), "source_meters_per_unit": mpu}


def fetch(name: str, timeout: float = 120) -> dict[str, Any]:
    """Download the 1k USD of a model and its textures into the cache (once), write model.usda, return the manifest."""
    d = _dir(name)
    if (m := _manifest(d)) is not None:
        return m if m.get("wrapper_version") == WRAPPER_VERSION else _rewrap(d, m)
    r = by_id(name) or {"id": name, "title": name, "license": LICENSE, "url": PAGE.format(name=name)}
    api = FILES_API.format(name=name)
    try:
        files = json.loads(_get(api, timeout))
    except ValueError as e:
        raise FetchError(f"Poly Haven: bad file list from {api}") from e
    entry = ((files.get("usd") or {}).get(RES) or {}).get("usd") if isinstance(files, dict) else None
    if not isinstance(entry, dict) or not entry.get("url"):
        raise FetchError(f"Poly Haven {name}: no {RES} USD download (files API {api})")
    usd_name = entry["url"].rsplit("/", 1)[-1]
    if not FILE_RE.match(usd_name) or not usd_name.lower().endswith(USD_EXTS):
        raise FetchError(f"Poly Haven {name}: unexpected USD file name {usd_name!r}")
    # every file first (checked), then write: a bad file list leaves nothing behind
    blobs: dict[str, bytes] = {usd_name: _checked(entry, timeout)}
    urls = [entry["url"]]
    md5 = {usd_name: entry.get("md5")}
    for rel, f in (entry.get("include") or {}).items():
        parts = rel.split("/")
        if len(parts) != 2 or parts[0] != "textures" or not FILE_RE.match(parts[1]) \
                or not parts[1].lower().endswith(TEXTURE_EXTS) or not isinstance(f, dict):
            raise FetchError(f"Poly Haven {name}: unexpected file in the download: {rel!r}")
        blobs[rel] = _checked(f, timeout)
        urls.append(f["url"])
        md5[rel] = f.get("md5")
    tmp = atomic.temp_for(d)  # a directory of this process's own: another server may fetch the same model
    (tmp / "textures").mkdir(parents=True)
    for rel, data in blobs.items():
        (tmp / rel).write_bytes(data)
    # textures the USD reads but the download left out (the 1k USD of dining_chair_02 reads rough_1k.exr and
    # ships rough_1k.jpg): fetched by file name from the model's other downloads at the same resolution.
    # Textures named by a path elsewhere (absolute paths of Poly Haven's own machines) point at textures/<file>.
    others, missing, repoint, moved = _by_basename(files, RES), [], [], []
    for prim_path, attr, p in _texture_refs(tmp / usd_name):
        base = re.split(r"[\\/]", p)[-1]
        rel = f"textures/{base}"
        if not FILE_RE.match(base) or not base.lower().endswith(TEXTURE_EXTS):
            missing.append(p)
            continue
        if not (tmp / rel).is_file():
            if (f := others.get(base)) is None:
                missing.append(p)
                continue
            data = _checked(f, timeout)
            (tmp / rel).write_bytes(data)
            blobs[rel], md5[rel] = data, f.get("md5")
            urls.append(f["url"])
        if p.removeprefix("./") != rel:
            repoint.append((prim_path, attr, f"./{rel}"))
            moved.append(p)
    meas = _write_wrapper(tmp, usd_name, r, repoint)
    filelist = {rel: hashlib.sha256(data).hexdigest() for rel, data in blobs.items()}
    filelist[WRAPPER] = hashlib.sha256((tmp / WRAPPER).read_bytes()).hexdigest()
    man = {"id": name, "title": r.get("title"), "url": r.get("url") or PAGE.format(name=name), "download": urls,
           "version": r.get("version"), "license": r.get("license") or LICENSE, "author": r.get("author"),
           "usd": usd_name, "wrapper": WRAPPER, "wrapper_version": WRAPPER_VERSION, "resolution": RES, "dimensions_mm": r.get("dimensions_mm"),
           **meas, "files": filelist, "md5": {k: v for k, v in md5.items() if v},
           "missing_textures": sorted(set(missing)), "repointed_textures": sorted(set(moved)),
           "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (tmp / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(d, ignore_errors=True)
    d.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.replace(d)
    except OSError:  # another server put the same model there between the two lines above: keep it
        shutil.rmtree(tmp, ignore_errors=True)
        if not (d / "manifest.json").is_file():
            raise
    return man


def _rewrap(d: Path, m: dict[str, Any]) -> dict[str, Any]:
    """A model fetched by an older agentic-sim-world-gen: write model.usda again from the files already in the cache."""
    repoint = []
    for prim_path, attr, p in _texture_refs(d / m["usd"]):
        base = re.split(r"[\\/]", p)[-1]
        rel = f"textures/{base}"
        if p.removeprefix("./") != rel and (d / rel).is_file():
            repoint.append((prim_path, attr, f"./{rel}"))
    r = by_id(m["id"]) or {"id": m["id"], "title": m.get("title"), "license": m.get("license"), "url": m.get("url"),
                           "author": m.get("author"), "version": m.get("version")}
    _write_wrapper(d, m["usd"], r, repoint)
    m = {**m, "wrapper_version": WRAPPER_VERSION,
         "files": {**m["files"], WRAPPER: hashlib.sha256((d / WRAPPER).read_bytes()).hexdigest()}}
    (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    return m


def local_usd(uri: str, timeout: float = 120) -> Path:
    """The cached model.usda of a "polyhaven:" uri (fetched if needed)."""
    name = parse_ref(uri)
    if name is None:
        raise ValueError(f"bad Poly Haven uri {uri!r} (expected polyhaven:<id>)")
    fetch(name, timeout)
    return _dir(name) / WRAPPER


def scene_ref(uri: str, scene_dir: Path, timeout: float = 120) -> str:
    """Copy the fetched model into <scene>/assets/polyhaven/<id>/ (when it is not there yet or changed) and return
    the relative reference compose writes into scene.usda."""
    name = parse_ref(uri)
    if name is None:
        raise ValueError(f"bad Poly Haven uri {uri!r} (expected polyhaven:<id>)")
    man = fetch(name, timeout)
    src, dst = _dir(name), Path(scene_dir) / SCENE_DIR / name
    if _manifest(dst) != man:
        shutil.rmtree(dst, ignore_errors=True)
        dst.mkdir(parents=True)
        for rel in [*man["files"], "manifest.json"]:
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, dst / rel)
    return f"./{SCENE_DIR}/{name}/{WRAPPER}"


def info(name: str) -> dict[str, Any]:
    """The cached manifest of a model, else its index record, else just the id (for licenses and origin)."""
    return cached(name) or by_id(name) or {"id": name, "license": LICENSE, "url": PAGE.format(name=name)}


def origin(uri: str) -> dict[str, Any]:
    """agentic_sim_world_gen.origin for a "polyhaven:" uri: the site, the model's page, files_hash, the USD's sha256."""
    name = parse_ref(uri) or uri
    m = info(name)
    usd = m.get("usd")
    return {"from": "polyhaven.com (CC0 models)", "url": m.get("url") or PAGE.format(name=name),
            "version": m.get("version"), "path": f"Models/usd/{RES}/{name}/{usd or name + '_' + RES + '.usdc'}",
            "pinned": False, "sha256": (m.get("files") or {}).get(usd) if usd else None,
            "download": m.get("download"),
            "note": None if m.get("version") else "no files_hash recorded: the version cannot be told"}


def credit(name: str, title: str | None = None) -> str:
    m = info(name)
    who = m.get("author") or "Poly Haven"
    return (f"\"{m.get('title') or title or name}\" by {who}, Poly Haven ({m.get('url') or PAGE.format(name=name)}), CC0 1.0"
            + (f", files_hash {m['version']}" if m.get("version") else ""))
