"""SimReady Central (Hugging Face) search provider.

Uses the same endpoint as the SimReady Central web UI:
    POST https://nvidia-simready-central.hf.space/search_hybrid
    header  x-usdsearch-storage-backend: usdsearch-dev
This endpoint is not officially documented (the documented /v3/deepsearch/search
returns 405 through the public proxy), so keep all knowledge of it in this file.
The official endpoint https://search.simready.omniverse.nvidia.com/ (the simready-search package) takes
the same /search_hybrid request but indexes another catalog (s3://omniverse-content-production, mostly the
Isaac Sim bundled assets, without licenses): checked 2026-11-12, 12/80 relevant top-5 hits on 16 furniture
and warehouse queries against 60/80 here, so it is not used.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests

from agentic_sim_world_gen.assets.providers.base import SizeFilter
from agentic_sim_world_gen.assets.record import AssetRecord

BASE_URL = "https://nvidia-simready-central.hf.space"
SEARCH_PATH = "/search_hybrid"
BACKEND_HEADER = {"x-usdsearch-storage-backend": "usdsearch-dev"}
EMBEDDING_FIELD = "siglip2-embedding.embedding"

PHYSICS_ENGINE = "PhysX"  # Isaac Sim uses PhysX; assets may also carry MuJoCo/Newton variants


# Answers kept in AGENTIC_SIM_WORLD_GEN_CACHE/search for AGENTIC_SIM_WORLD_GEN_SEARCH_CACHE_HOURS (default 24, 0 = not kept): the
# same (query, size limits, limit) is asked again by research_object, by every scene with a desk and by the
# next try of the same scene. The catalog changes rarely; a new asset shows up a day late at worst.
CACHE_HOURS = 24.0
STATS = {"fetched": 0, "cached": 0}  # of this process


def cache_hours() -> float:
    try:
        return max(0.0, float(os.environ.get("AGENTIC_SIM_WORLD_GEN_SEARCH_CACHE_HOURS", CACHE_HOURS)))
    except ValueError:
        return CACHE_HOURS


def _cache_dir() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_CACHE", "~/.cache/agentic-sim-world-gen")).expanduser() / "search"


def cache_get(key: str) -> list[dict[str, Any]] | None:
    hours = cache_hours()
    f = _cache_dir() / f"{key}.json.gz"
    try:
        if not hours or time.time() - f.stat().st_mtime > hours * 3600:
            return None
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            return json.load(fh)["hits"]
    except (OSError, ValueError, KeyError, EOFError):
        return None


def cache_put(key: str, query: str, hits: list[dict[str, Any]]) -> None:
    hours = cache_hours()
    if not hours:
        return
    d = _cache_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{key}.{os.getpid()}.{threading.get_ident()}.part"
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump({"query": query, "hits": hits}, fh, ensure_ascii=False)
        os.replace(tmp, d / f"{key}.json.gz")
        old = time.time() - hours * 3600
        for f in d.glob("*.json.gz"):  # the folder stays as big as a day's searches
            if f.stat().st_mtime < old:
                f.unlink(missing_ok=True)
    except OSError:
        pass  # a cache that cannot be written is no reason to fail a search


def hf_to_https(path: str) -> str:
    """hf://huggingface.co/datasets/... -> https://huggingface.co/datasets/..."""
    if path.startswith("hf://"):
        return "https://" + path[len("hf://") :]
    return path


def thumbnail_url(usd_url: str) -> str:
    """SimReady packaging puts a 256px thumbnail at <dir>/.thumbs/256x256/<file>.png
    where <file> includes the .usd extension."""
    head, _, fname = usd_url.rpartition("/")
    return f"{head}/.thumbs/256x256/{fname}.png"


def _props(source: dict[str, Any]) -> dict[str, Any]:
    return {p["name"]: p.get("value") for p in source.get("usd_properties") or []}


def _vlm(source: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in source.get("plugin_thumbnail_to_vision_metadata_metadata_vlm_generated") or []:
        vals = item.get("value_text") or []
        out[item.get("name", "")] = list(vals) if isinstance(vals, list) else [vals]
    return out


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def hit_to_record(hit: dict[str, Any]) -> AssetRecord:
    src = hit["source"]
    dims = src.get("usd_dimensions") or {}
    props = _props(src)
    vlm = _vlm(src)
    url = hf_to_https(src["path"])

    bbox_min = tuple(float(dims.get(f"bbox_min_{a}", 0.0)) for a in "xyz")
    bbox_max = tuple(float(dims.get(f"bbox_max_{a}", 0.0)) for a in "xyz")

    variants: dict[str, str] = {}
    vs_name = props.get(f"simready_metadata_Variants__Physics__{PHYSICS_ENGINE}__variantSetName")
    vs_opt = props.get(f"simready_metadata_Variants__Physics__{PHYSICS_ENGINE}__activateOption")
    if vs_name and vs_opt:
        variants[str(vs_name)] = str(vs_opt)

    rbc = _float(props.get("simready_metadata_rigid_body_count"))
    faces = _float(props.get("__polygon_count"))
    return AssetRecord(
        id=hit.get("id") or url,
        source="simready-central",
        uri=url,
        name=src.get("name", url.rsplit("/", 1)[-1]),
        bbox_min=bbox_min,  # type: ignore[arg-type]
        bbox_max=bbox_max,  # type: ignore[arg-type]
        up_axis=str(dims.get("scene_up_axis") or "Z"),
        meters_per_unit=float(dims.get("scene_mpu") or 1.0),
        semantic_class=props.get("semantic:wikidata_class:params:semanticData")
        or (vlm.get("object_type") or [None])[0],
        qcode=props.get("semantic:wikidata_qcode:params:semanticData")
        or props.get("simready_metadata_qcode"),
        caption=(vlm.get("caption") or [None])[0],
        tags=vlm.get("tags") or [],
        license=props.get("simready_metadata_asset_license"),
        author=props.get("simready_metadata_author"),
        mass_kg=_float(props.get("simready_metadata_mass")),
        rigid_body_count=int(rbc) if rbc is not None else None,
        polygons=int(faces) if faces is not None else None,
        thumbnail_url=thumbnail_url(url) if hit.get("thumbnail_exists", True) else None,
        variant_selections=variants,
        score=hit.get("score"),
        raw=hit,
    )


class SimReadyCentralProvider:
    name = "simready-central"

    def __init__(self, base_url: str = BASE_URL, timeout: float = 90.0, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        if session is None:
            from agentic_sim_world_gen import UA

            session = requests.Session()
            session.headers.update(UA)
        self.session = session

    def build_request(self, query: str, *, limit: int = 10, size: SizeFilter | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "hybrid_text_query": query,
            "vector_queries": [{"field_name": EMBEDDING_FIELD, "query_type": "text", "query": query}],
            "file_extension_include": "usd*",
            "limit": limit,
            "return_usd_dimensions": True,
            "return_usd_properties": True,
            "return_vision_generated_metadata": True,
        }
        if size:
            # API filters are in asset units/axes; SimReady Central assets observed so far
            # are all Z-up with meters_per_unit = 1, so meters map directly.
            for axis in "xyz":
                for bound in ("min", "max"):
                    v = getattr(size, f"{bound}_{axis}")
                    if v is not None:
                        body[f"{bound}_bbox_{axis}"] = v
        return body

    def search(self, query: str, *, limit: int = 10, size: SizeFilter | None = None) -> list[AssetRecord]:
        body = self.build_request(query, limit=limit, size=size)
        key = hashlib.sha1(json.dumps([self.base_url, body], sort_keys=True).encode()).hexdigest()[:20]
        if (hits := cache_get(key)) is not None:
            STATS["cached"] += 1
            return parse_response({"hits": hits})
        STATS["fetched"] += 1
        resp = self.session.post(
            self.base_url + SEARCH_PATH,
            data=json.dumps(body),
            headers={"Content-Type": "application/json", **BACKEND_HEADER},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("hits"):  # an empty answer is asked again: the service has its bad moments
            cache_put(key, query, data["hits"])
        return parse_response(data)


def parse_response(data: dict[str, Any]) -> list[AssetRecord]:
    return [hit_to_record(h) for h in data.get("hits") or [] if h.get("source", {}).get("path")]
