"""Open USD files that reference http(s) assets with usd-core, which has no http resolver.

Third-party folders often reference Isaac Sim assets on the public S3 bucket. Isaac Sim resolves
those URLs itself, but usd-core cannot, so the bbox of such a file comes out empty or partial.
For indexing only, `openable_copy()` writes a copy of the layer whose remote references point at
local files: the USD layers behind each URL (no textures) are downloaded into the cache, or taken
from ISAAC_ASSETS_ROOT when that is a local copy of the Isaac assets.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

from agentic_sim_world_gen import atomic

USD_EXTS = (".usd", ".usda", ".usdc", ".usdz")
_ISAAC_S3 = re.compile(r"^https?://omniverse-content-production\.s3[^/]*\.amazonaws\.com/Assets/Isaac/[^/]+(/Isaac/.+)$")


def _is_remote(p: str) -> bool:
    return p.startswith(("http://", "https://"))


def _is_usd(p: str) -> bool:
    return urlparse(p).path.lower().endswith(USD_EXTS) if _is_remote(p) else p.lower().endswith(USD_EXTS)


class _Fetcher:
    def __init__(self, cache: Path):
        self.cache = cache
        self.isaac_root = os.environ.get("ISAAC_ASSETS_ROOT")
        self.done: dict[str, str | None] = {}
        self._session = None

    def session(self):
        if self._session is None:
            import requests
            from requests.adapters import HTTPAdapter, Retry

            from agentic_sim_world_gen import UA

            s = requests.Session()
            s.headers.update(UA)
            s.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1)))
            self._session = s
        return self._session

    def local_path(self, url: str) -> str | None:
        """Local file standing in for `url` (fetched with its USD dependencies), or None."""
        if url in self.done:
            return self.done[url]
        self.done[url] = None  # cycle guard
        m = _ISAAC_S3.match(url)
        if m and self.isaac_root and not _is_remote(self.isaac_root):
            p = Path(self.isaac_root).expanduser() / m[1].lstrip("/")
            if p.exists():  # a local mirror resolves its own relative references
                self.done[url] = str(p)
                return self.done[url]
        u = urlparse(url)
        p = self.cache / u.netloc / u.path.lstrip("/")
        if not p.exists():
            try:
                r = self.session().get(url, timeout=300)
                r.raise_for_status()
            except Exception as e:  # noqa: BLE001
                print(f"agentic-sim-world-gen: cannot fetch {url}: {e}", file=sys.stderr)
                return None
            atomic.write(p, r.content)  # another server may be fetching the same layer
        self.done[url] = str(p)
        if p.suffix.lower() != ".usdz":
            self._follow(url, p)
        return self.done[url]

    def _follow(self, url: str, p: Path) -> None:
        """Fetch what a downloaded layer references; rewrite its absolute remote references."""
        from pxr import Sdf, UsdUtils

        layer = Sdf.Layer.FindOrOpen(str(p))
        if layer is None:
            return
        changed = False

        def fix(a: str) -> str:
            nonlocal changed
            if not a or not _is_usd(a):
                return a
            if _is_remote(a):
                loc = self.local_path(a)
                if loc:
                    changed = True
                    return loc
                return a
            if not os.path.isabs(a):
                self.local_path(urljoin(url, a))  # lands next to p, so the relative path works
            return a

        UsdUtils.ModifyAssetPaths(layer, fix)
        if changed:  # not Save(): another server may be reading this file right now
            atomic.export_layer(layer, p)


def remote_deps(path: str | Path) -> list[str]:
    from pxr import Sdf

    layer = Sdf.Layer.FindOrOpen(str(path))
    if layer is None:
        return []
    try:
        deps = layer.GetCompositionAssetDependencies()
    except AttributeError:  # older pxr
        deps = layer.GetExternalReferences()
    return sorted({d for d in deps if d and _is_remote(d)})


def openable_copy(path: str | Path, cache_dir: str | Path) -> Path | None:
    """Copy of `path` with remote USD references replaced by local files, or None if `path` has no
    remote references. Relative references are made absolute so the copy resolves from anywhere."""
    from pxr import Sdf, UsdUtils

    path = Path(path)
    if path.suffix.lower() == ".usdz" or not remote_deps(path):
        return None
    cache = Path(cache_dir)
    fetch = _Fetcher(cache / "remote")
    src = Sdf.Layer.FindOrOpen(str(path))
    out = cache / "rewritten" / f"{hashlib.sha1(str(path).encode()).hexdigest()[:16]}{path.suffix}"
    copy = Sdf.Layer.CreateAnonymous(path.suffix)  # written whole at the end
    copy.TransferContent(src)

    def fix(a: str) -> str:
        if not a:
            return a
        if _is_remote(a):
            return (fetch.local_path(a) or a) if _is_usd(a) else a
        return a if os.path.isabs(a) else src.ComputeAbsolutePath(a)

    UsdUtils.ModifyAssetPaths(copy, fix)
    atomic.export_layer(copy, out)
    return out
