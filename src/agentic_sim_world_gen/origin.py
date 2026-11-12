"""Where an asset came from, pinned down to a version: which dataset / repository, which commit or
release, and the path inside it. Used by the license report, LICENSES.md, the preview and the USD.

- SimReady Central: Hugging Face dataset; the URL already carries the commit (…/resolve/<sha>/<path>).
- Isaac Sim bundled: release folder (Isaac/6.1/…); pinned to the release only, not to a file revision.
- local folders: the asset's own version (asset.json "version", else the USD's assetInfo.version, e.g.
  example-assets 1.3.0), the git checkout it is in (remote, the last commit that changed the
  asset's folder, uncommitted changes) and the file's SHA-256; outside git only version + hash identify it.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

_HF = re.compile(r"https://huggingface\.co/datasets/([^/]+/[^/]+)/(?:resolve|blob)/([^/]+)/(.+)")
_ISAAC = re.compile(r".*/Isaac/(\d+(?:\.\d+)*)(/Isaac/.+)")
_SHA = re.compile(r"[0-9a-f]{40}")


def origin(uri: str, source: str | None = None, asset_id: str | None = None) -> dict[str, Any]:
    """{"from": dataset / repository, "url": page of this file, "version", "path" (inside "from"),
    "pinned" (the same file can be fetched again), "dirty", "sha256", "note"}."""
    m = _HF.match(uri)
    if m:
        repo, rev, path = m.groups()
        pinned = bool(_SHA.fullmatch(rev))
        return {"from": f"huggingface.co/datasets/{repo}", "url": f"https://huggingface.co/datasets/{repo}/blob/{rev}/{path}",
                "version": rev, "path": path, "pinned": pinned,
                "note": None if pinned else f"branch '{rev}', not a commit: the file can change"}
    if uri.startswith("polyhaven:"):
        from agentic_sim_world_gen import polyhaven_models

        return polyhaven_models.origin(uri)
    if (source or "").startswith("isaac") or (asset_id or "").startswith("isaac:"):
        ver = (source or "").removeprefix("isaac-").removeprefix("isaac") or None
        m = _ISAAC.match(uri)
        path = (asset_id or "").removeprefix("isaac:") or (m.group(2) if m else uri)
        ver = ver or (m.group(1) if m else None)
        return {"from": "Isaac Sim assets", "url": uri, "version": ver, "path": path.lstrip("/"), "pinned": False,
                "note": "pinned to the Isaac Sim release only (files can be updated within it)"}
    p = _local(uri)
    if p is not None:
        return _local_origin(p)
    host = urlparse(uri).netloc or "unknown"
    return {"from": host, "url": uri, "version": None, "path": urlparse(uri).path.lstrip("/"), "pinned": False,
            "note": "no version recorded"}


def label(o: dict[str, Any], with_from: bool = True) -> str:
    """'nvidia/simready-assets @ 653d91e' / 'Isaac Sim assets 6.1' / 'someone/example-assets @ e8e9431 (変更あり)'."""
    name = re.sub(r"^(huggingface\.co/datasets/|github\.com/)", "", o["from"])
    ver = o.get("version") or ""
    if o.get("tag"):
        ver = f"{o['tag']} ({ver[:7]})"
    elif _SHA.fullmatch(ver):
        ver = ver[:7]
    elif ver.startswith("sha256:"):
        ver = ver[:15]
    sep = " " if o["from"] == "Isaac Sim assets" else " @ "
    av = f" v{o['asset_version']}" if o.get("asset_version") else ""
    out = name + av + (sep + ver if ver else "") + (" + uncommitted changes" if o.get("dirty") else "")
    return out if with_from else out[len(name):].removeprefix(" @ ").strip() or "-"


def _local(uri: str) -> Path | None:
    if uri.startswith("file://"):
        return Path(unquote(urlparse(uri).path))
    if uri.startswith("/") or re.match(r"[A-Za-z]:[\\/]", uri):
        return Path(uri)
    return None


@lru_cache(maxsize=4096)
def _sha256(path: str, mtime: float, size: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sha256(p: Path) -> str | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return _sha256(str(p), st.st_mtime, st.st_size)


def _git(cwd: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def remote_page(url: str | None) -> str | None:
    """git@github.com:a/b.git / https://github.com/a/b.git -> https://github.com/a/b (no credentials)."""
    if not url:
        return None
    m = re.match(r"(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$", url)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    u = urlparse(url)
    if u.scheme in ("http", "https"):
        return f"https://{u.hostname}{re.sub(r'.git/?$', '', u.path)}"
    return None


def _stamp(top: Path) -> tuple[float, ...]:
    """Changes when HEAD moves or the index is touched; keeps git from running on every preview write."""
    out = []
    for f in ("HEAD", "index", "FETCH_HEAD"):
        try:
            out.append((top / ".git" / f).stat().st_mtime)
        except OSError:
            out.append(0.0)
    return tuple(out)


@lru_cache(maxsize=256)
def _repo(top: str, stamp: tuple[float, ...]) -> dict[str, Any]:
    t = Path(top)
    return {"commit": _git(t, "rev-parse", "HEAD"), "tag": _git(t, "describe", "--tags", "--exact-match"),
            "remote": remote_page(_git(t, "config", "--get", "remote.origin.url"))}


@lru_cache(maxsize=4096)
def _dirty(top: str, rel_dir: str, stamp: tuple[float, ...], mtime: float) -> bool:
    out = _git(Path(top), "status", "--porcelain", "--", rel_dir or ".")
    return bool(out)


@lru_cache(maxsize=4096)
def _asset_version(path: str, mtime: float) -> str | None:
    """asset.json "version" next to the file (SimReady-style folders with asset.json), else the USD's
    defaultPrim assetInfo["version"] (needs usd-core)."""
    p = Path(path)
    side = p.parent / "asset.json"
    try:
        d = json.loads(side.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("usd") in (None, p.name) and d.get("version"):
            return str(d["version"])
    except (OSError, ValueError):
        pass
    try:
        from pxr import Sdf
    except ImportError:
        return None
    try:
        layer = Sdf.Layer.FindOrOpen(path)
        prim = layer and layer.defaultPrim and layer.GetPrimAtPath("/" + layer.defaultPrim)
        v = prim.assetInfo.get("version") if prim else None
    except Exception:  # noqa: BLE001 - unreadable layers just have no version
        return None
    return str(v) if v else None


def _local_origin(p: Path) -> dict[str, Any]:
    digest = sha256(p)
    try:
        aver = _asset_version(str(p), max(p.stat().st_mtime, _mtime(p.parent / "asset.json")))
    except OSError:
        aver = None
    top_s = _git(p.parent, "rev-parse", "--show-toplevel") if p.parent.is_dir() else None
    if not top_s:
        return {"from": str(p.parent), "url": p.as_uri() if p.is_absolute() else str(p), "asset_version": aver,
                "version": f"sha256:{digest}" if digest else None, "path": p.name, "pinned": False,
                "sha256": digest, "note": "not in a git repository: only the version and file hash identify it"}
    top = Path(top_s)
    repo = _repo(top_s, _stamp(top))
    rel = p.resolve().relative_to(top.resolve()).as_posix()
    rel_dir = p.resolve().parent.relative_to(top.resolve()).as_posix()
    try:
        mtime = max(f.stat().st_mtime for f in p.parent.rglob("*") if f.is_file())
    except (OSError, ValueError):
        mtime = 0.0
    rd = "" if rel_dir == "." else rel_dir
    dirty = _dirty(top_s, rd, _stamp(top), mtime)
    # the commit that last changed this asset's folder: stays the same while other assets change
    commit = _folder_commit(top_s, rd, _stamp(top)) or repo["commit"]
    remote = repo["remote"]
    name = re.sub(r"^https://", "", remote) if remote else top.name
    url = f"{remote}/blob/{commit}/{rel}" if remote and commit else p.as_uri()
    note = None
    if dirty:
        note = "uncommitted changes in the asset folder: this version cannot be fetched again"
    elif not remote:
        note = "git repository without a remote: the commit cannot be fetched elsewhere"
    return {"from": name, "url": url, "asset_version": aver, "version": commit,
            "tag": repo["tag"] if commit == repo["commit"] else None, "path": rel,
            "pinned": bool(remote and commit and not dirty), "dirty": dirty, "sha256": digest, "note": note}


@lru_cache(maxsize=4096)
def _folder_commit(top: str, rel_dir: str, stamp: tuple[float, ...]) -> str | None:
    return _git(Path(top), "log", "-1", "--format=%H", "--", rel_dir or ".") or None


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0
