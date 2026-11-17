"""Download referenced Hugging Face assets to a local cache.

Fallback for when Isaac Sim cannot reference https://huggingface.co URLs directly
(redirects to the HF CDN, offline machines, etc.).
"""

from __future__ import annotations

import re
from pathlib import Path

from agentic_sim_world_gen.scene.spec import ResolvedScene

_HF_RE = re.compile(
    r"^https://huggingface\.co/datasets/(?P<repo>[^/]+/[^/]+)/resolve/(?P<rev>[0-9a-f]{40})/(?P<path>.+)$"
)


def parse_hf_url(url: str) -> tuple[str, str, str] | None:
    """Return (repo_id, revision, path_in_repo) for a pinned HF dataset URL."""
    m = _HF_RE.match(url)
    if not m:
        return None
    return m["repo"], m["rev"], m["path"]


def localize(scene: ResolvedScene, cache_dir: str | Path | None = None,
             only: set[str] | None = None) -> dict[str, str]:
    """Download the selected asset of every placement (or of the objects in `only`). Returns {url: local_path}.
    An asset already in the cache is used without asking the Hub."""
    from huggingface_hub import snapshot_download  # optional dependency: pip install .[hf]

    out: dict[str, str] = {}
    for p in scene.placements:
        a = p.asset
        if a is None or (only is not None and p.object_id not in only) or a.uri in out:
            continue
        parsed = parse_hf_url(a.uri)
        if not parsed:
            continue
        repo, rev, path = parsed
        asset_dir = path.split("/", 1)[0]  # <logical-asset-name>/...
        kw = dict(repo_id=repo, repo_type="dataset", revision=rev, allow_patterns=[f"{asset_dir}/**"],
                  cache_dir=str(cache_dir) if cache_dir else None)
        local = None
        try:
            local = Path(snapshot_download(**kw, local_files_only=True)) / path
        except Exception:  # not in the cache yet
            pass
        if local is None or not local.exists():
            local = Path(snapshot_download(**kw)) / path
        out[a.uri] = str(local)
    return out


def screened_remote(scene: ResolvedScene) -> set[str]:
    """Objects with a screen image whose asset declares no screen and is a Hugging Face URL: compose finds the
    screen in the asset's materials, so the asset must be local like a tinted one."""
    shown = {o.id for o in scene.spec.objects if o.screen is not None}
    return {p.object_id for p in scene.placements
            if p.object_id in shown and p.asset is not None and not p.asset.screens and parse_hf_url(p.asset.uri)}


def tinted_remote(scene: ResolvedScene) -> set[str]:
    """Objects with a tint whose asset is a Hugging Face URL: the composing pxr cannot open https, so their
    materials are not there to repaint unless the asset is local (#31)."""
    tinted = {o.id for o in scene.spec.objects if o.tint}
    return {p.object_id for p in scene.placements
            if p.object_id in tinted and p.asset is not None and parse_hf_url(p.asset.uri)}
