"""Interface every asset source implements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentic_sim_world_gen.assets.record import AssetRecord


@dataclass
class SizeFilter:
    """Optional bounding-box limits in meters (Z-up). None means unbounded."""

    min_x: float | None = None
    max_x: float | None = None
    min_y: float | None = None
    max_y: float | None = None
    min_z: float | None = None
    max_z: float | None = None


@runtime_checkable
class AssetProvider(Protocol):
    name: str

    def search(
        self, query: str, *, limit: int = 10, size: SizeFilter | None = None
    ) -> list[AssetRecord]:
        """Return assets matching an English natural-language query, best first."""
        ...


def size_filter_for(
    min_size: tuple[float, float, float] | None, max_size: tuple[float, float, float] | None
) -> SizeFilter | None:
    """Server-side filter that ignores footprint orientation (an asset may be modeled
    with its long side on X or Y). Only height and the footprint's long side are bounded;
    use `fits` client-side for the exact check."""
    if not (min_size or max_size):
        return None
    f = SizeFilter()
    if max_size:
        f.max_x = f.max_y = max_size[0]
        f.max_z = max_size[2]
    if min_size:
        f.min_z = min_size[2]
    return f


def fits(
    size_m: tuple[float, float, float],
    min_size: tuple[float, float, float] | None,
    max_size: tuple[float, float, float] | None,
) -> bool:
    """Orientation-independent size check: compare (long side, short side, height)."""
    w, d, h = size_m
    dims = (max(w, d), min(w, d), h)
    if min_size and any(v < lo for v, lo in zip(dims, min_size)):
        return False
    if max_size and any(v > hi for v, hi in zip(dims, max_size)):
        return False
    return True
