"""Poly Haven's CC0 3D models, searched offline from a prebuilt index (scripts/build_polyhaven_models_index.py).

Records are "polyhaven:<id>"; the model files are fetched only when a scene is composed
(agentic_sim_world_gen.polyhaven_models.fetch). Set AGENTIC_SIM_WORLD_GEN_POLYHAVEN_INDEX to use another index file.
"""

from __future__ import annotations

from pathlib import Path

from agentic_sim_world_gen import polyhaven_models as ph
from agentic_sim_world_gen.assets.providers.base import SizeFilter
from agentic_sim_world_gen.assets.providers.offline import search_records
from agentic_sim_world_gen.assets.record import AssetRecord


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
        # a fetched model's measured size replaces the catalog dimensions
        return tuple(ph.to_asset_record(r, ph.cached(r["id"])) for r in ph._load(str(self.path)))

    def search(self, query: str, *, limit: int = 10, size: SizeFilter | None = None) -> list[AssetRecord]:
        return search_records(self.records(), query, limit, size)
