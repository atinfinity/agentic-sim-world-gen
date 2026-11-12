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


def localize(scene: ResolvedScene, cache_dir: str | Path | None = None) -> dict[str, str]:
    """Download the selected asset of every placement. Returns {url: local_path}."""
    from huggingface_hub import snapshot_download  # optional dependency: pip install .[hf]

    out: dict[str, str] = {}
    for p in scene.placements:
        a = p.asset
        if a is None:
            continue
        parsed = parse_hf_url(a.uri)
        if not parsed:
            continue
        repo, rev, path = parsed
        asset_dir = path.split("/", 1)[0]  # <logical-asset-name>/...
        local_root = snapshot_download(
            repo_id=repo,
            repo_type="dataset",
            revision=rev,
            allow_patterns=[f"{asset_dir}/**"],
            cache_dir=str(cache_dir) if cache_dir else None,
        )
        out[a.uri] = str(Path(local_root) / path)
    return out
