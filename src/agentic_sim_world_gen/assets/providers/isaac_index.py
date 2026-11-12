"""Isaac Sim bundled assets, searched offline from a prebuilt index (scripts/build_isaac_index.py).

Records point at the public S3 assets root by default. Set ISAAC_ASSETS_ROOT to a local or Nucleus
assets root (the folder that contains /Isaac) to reference that copy instead.
"""

from __future__ import annotations

import gzip
import json
import os
from functools import lru_cache
from pathlib import Path

from agentic_sim_world_gen.assets.providers.base import SizeFilter
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.assets.providers.offline import search_records, size_ok
from agentic_sim_world_gen.scene.spec import record_from_dict

DATA = Path(__file__).resolve().parents[2] / "data"
DEFAULT_VERSION = "6.1"


def index_path(version: str = DEFAULT_VERSION) -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_ISAAC_INDEX") or DATA / f"isaac_{version}.jsonl.gz")


@lru_cache(maxsize=4)
def _load(path: str, root: str | None) -> tuple[AssetRecord, ...]:
    out = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if root and d.get("isaac_path"):
                d["uri"] = root.rstrip("/") + d["isaac_path"]
            out.append(record_from_dict(d))
    return tuple(out)


_ok = size_ok  # kept for older imports


def rebase_uri(record: AssetRecord, root: str | None = None) -> str | None:
    """URI of an Isaac index record under `root` (default ISAAC_ASSETS_ROOT), or None if not an
    Isaac record / no root. Lets compose switch an already-resolved scene to a local copy."""
    root = root if root is not None else os.environ.get("ISAAC_ASSETS_ROOT")
    if not root or not record.id.startswith("isaac:/Isaac/"):
        return None
    return root.rstrip("/") + record.id.removeprefix("isaac:")


class IsaacIndexProvider:
    name = "isaac"

    def __init__(self, path: str | Path | None = None, assets_root: str | None = None):
        self.path = Path(path) if path else index_path()
        self.assets_root = assets_root if assets_root is not None else os.environ.get("ISAAC_ASSETS_ROOT")

    @property
    def available(self) -> bool:
        return self.path.exists()

    def records(self) -> tuple[AssetRecord, ...]:
        return _load(str(self.path), self.assets_root) if self.available else ()

    def search(self, query: str, *, limit: int = 10, size: SizeFilter | None = None) -> list[AssetRecord]:
        return search_records(self.records(), query, limit, size)
