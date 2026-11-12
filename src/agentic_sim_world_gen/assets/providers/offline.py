"""Search helpers for providers that hold all their records in memory (Isaac index, local folders)."""

from __future__ import annotations

from collections.abc import Iterable

from agentic_sim_world_gen.assets.providers.base import SizeFilter
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.assets.rerank import score


def size_ok(size_m: tuple[float, float, float], f: SizeFilter | None) -> bool:
    if f is None:
        return True
    if not any(size_m):
        return False  # unknown size cannot satisfy a size request
    for v, axis in zip(size_m, "xyz"):
        lo, hi = getattr(f, f"min_{axis}"), getattr(f, f"max_{axis}")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            return False
    return True


def search_records(records: Iterable[AssetRecord], query: str, limit: int, size: SizeFilter | None) -> list[AssetRecord]:
    """Lexical search: metadata match score (agentic_sim_world_gen.assets.rerank) > 0, best first."""
    scored = []
    for i, r in enumerate(records):
        if not size_ok(r.size_m, size):
            continue
        m = score(r, query)
        if m.score > 0:
            scored.append((-m.score, i, r))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [r for _, _, r in scored[:limit]]
