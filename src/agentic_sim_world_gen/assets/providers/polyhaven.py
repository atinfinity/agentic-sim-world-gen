"""Poly Haven's CC0 3D models, searched offline from a prebuilt index (scripts/build_polyhaven_models_index.py).

Records are "polyhaven:<id>"; the model files are fetched only when a scene is composed
(agentic_sim_world_gen.polyhaven_models.fetch). Set AGENTIC_SIM_WORLD_GEN_POLYHAVEN_INDEX to use another index file.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from agentic_sim_world_gen import polyhaven_models as ph
from agentic_sim_world_gen.assets.providers.base import SizeFilter
from agentic_sim_world_gen.assets.providers.offline import search_records
from agentic_sim_world_gen.assets.record import AssetRecord

_RECORDS: dict[tuple, tuple[AssetRecord, ...]] = {}
_RECORDS_KEPT = 4  # index / cache folder pairs
_LOCK = threading.Lock()


def _stamp(p: Path) -> tuple[int, int, int] | None:
    """When the file was written: the time, and the inode and size where the clock is coarse (FAT, some mounts)."""
    try:
        st = p.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_ino, st.st_size


class PolyHavenProvider:
    name = "polyhaven"

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else ph.index_path()

    @property
    def available(self) -> bool:
        return self.path.exists()

    def records(self) -> tuple[AssetRecord, ...]:
        if not self.available:
            return ()
        # a fetched model's measured size replaces the catalog dimensions. The cache folder is listed once: a
        # model without a folder there was not fetched, and its manifest is not opened
        root = ph.cache_root()
        try:
            fetched = set(os.listdir(root))
        except OSError:
            fetched = set()
        index = ph._load(str(self.path))
        fetched &= {r["id"] for r in index}
        # the records are kept while the cache folder and the fetched models' manifests stay the same, and
        # shared like the Isaac index's: never change one in place. Whether a fetched model has all its files is
        # checked when the records are made: a file removed (or put back) later without a new manifest.json leaves
        # the size as it was (fetch() checks the files again when a scene is composed). The index is read once per
        # path (ph._load), as before.
        key = (str(self.path), str(root), frozenset((n, _stamp(root / n / "manifest.json")) for n in fetched))
        if (got := _RECORDS.get(key)) is None:
            with _LOCK:  # parallel searches (AGENTIC_SIM_WORLD_GEN_SEARCH_WORKERS) make them once
                if (got := _RECORDS.get(key)) is None:
                    got = tuple(ph.to_asset_record(r, ph.cached(r["id"]) if r["id"] in fetched else None)
                                for r in index)
                    if len(_RECORDS) >= _RECORDS_KEPT:
                        _RECORDS.pop(next(iter(_RECORDS)))
                    _RECORDS[key] = got
        return got

    def search(self, query: str, *, limit: int = 10, size: SizeFilter | None = None) -> list[AssetRecord]:
        return search_records(self.records(), query, limit, size)
