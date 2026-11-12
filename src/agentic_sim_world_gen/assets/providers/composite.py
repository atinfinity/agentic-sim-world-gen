"""Search several providers and merge; the first provider's results come first.

Assets present in more than one source (SimReady Central and Isaac's SimReady folder share file
names) are kept once, from the earlier provider.
"""

from __future__ import annotations

import logging

from agentic_sim_world_gen.assets.providers.base import AssetProvider, SizeFilter
from agentic_sim_world_gen.assets.record import AssetRecord

log = logging.getLogger(__name__)


def _stem(r: AssetRecord) -> str:
    return r.name.rsplit(".", 1)[0].lower()


class CompositeProvider:
    def __init__(self, providers: list[AssetProvider]):
        self.providers = providers
        self.name = "+".join(p.name for p in providers)
        self.errors: list[str] = []

    def search(self, query: str, *, limit: int = 10, size: SizeFilter | None = None) -> list[AssetRecord]:
        out, seen = [], set()
        for p in self.providers:
            try:
                recs = p.search(query, limit=limit, size=size)
            except Exception as e:  # one source down (e.g. no network): keep going with the others
                if len(self.providers) == 1:
                    raise
                log.warning("provider %s failed: %s", p.name, e)
                self.errors.append(f"{p.name}: {e}")
                continue
            for r in recs:
                if _stem(r) not in seen:
                    seen.add(_stem(r))
                    out.append(r)
        return out
