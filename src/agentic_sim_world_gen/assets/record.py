"""Provider-independent asset description.

Every asset source (SimReady Central, Isaac Sim bundled assets, third-party
libraries) converts its results into an AssetRecord, so layout, preview and
USD composition never depend on where an asset came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Vec3 = tuple[float, float, float]


@dataclass
class AssetRecord:
    id: str
    source: str  # provider name, e.g. "simready-central"
    uri: str  # URL or path usable directly as a USD reference (pinned version)
    name: str
    bbox_min: Vec3  # in asset units, asset's own axes
    bbox_max: Vec3
    up_axis: str = "Z"
    meters_per_unit: float = 1.0
    semantic_class: str | None = None  # e.g. "office chair"
    qcode: str | None = None  # Wikidata Q-code
    caption: str | None = None
    tags: list[str] = field(default_factory=list)
    license: str | None = None
    author: str | None = None
    # third-party material inside the asset (texture photos, the font of printed text), e.g. asset.json image_credits:
    # [{"title", "author", "url", "license", "license_url", "subject"}]
    credits: list[dict[str, Any]] = field(default_factory=list)
    mass_kg: float | None = None
    rigid_body_count: int | None = None
    # faces of all its meshes (SimReady Central's __polygon_count, counted for local: folders); None = not known.
    # Scanned assets have ~1 M: thousands of them on shelves run Isaac Sim out of memory
    polygons: int | None = None
    thumbnail_url: str | None = None
    # variant set name -> selection to apply when referencing (e.g. {"PhysX": "Enabled"})
    variant_selections: dict[str, str] = field(default_factory=dict)
    # variant sets on the asset's root and their values (e.g. {"Lighting": ["Off", "On"]}); ObjectSpec.variants
    # picks from these. Empty = not known (only local assets are read)
    variant_sets: dict[str, list[str]] = field(default_factory=dict)
    # direction the asset's front points to in its own Z-up footprint frame [deg] (0 = +X, 90 = +Y);
    # None = the default from layout/frame.py
    front_deg: float | None = None
    # shelf boards found from the geometry (agentic_sim_world_gen.assets.shelves), filled in when something is stocked on it
    shelves: list[dict[str, Any]] | None = None
    # hooks found with the shelves (agentic_sim_world_gen.assets.shelves): pegboards, hook racks
    hooks: list[dict[str, Any]] | None = None
    # screens and printed pictures whose image ObjectSpec.screen can replace (asset.json `screen` / `picture`):
    # [{"kind": "screen", "material": "ScreenMat", "image": "<abs path of the original texture>",
    #   "off": "ScreenMat_Off", "variant_set": "Screen", "on": "On", "off_variant": "Off", "places": [...]}]
    screens: list[dict[str, Any]] = field(default_factory=list)
    score: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # --- geometry in stage terms (Z-up, meters) ---------------------------

    def _to_zup(self, v: Vec3) -> Vec3:
        """Map an asset-space vector to a Z-up frame (Y-up assets are rotated +90 deg about X)."""
        if self.up_axis.upper() == "Y":
            x, y, z = v
            return (x, -z, y)
        return v

    @property
    def size_m(self) -> Vec3:
        """(width along X, depth along Y, height along Z) in meters, Z-up."""
        dx, dy, dz = (b - a for a, b in zip(self.bbox_min, self.bbox_max))
        w, d, h = self._to_zup((dx, dy, dz))
        s = self.meters_per_unit
        return (abs(w) * s, abs(d) * s, abs(h) * s)

    @property
    def pivot(self) -> Vec3:
        """Asset-space point that should sit on the floor at the placement position:
        footprint center, bottom of the bounding box."""
        mn, mx = self.bbox_min, self.bbox_max
        mid = tuple((a + b) / 2 for a, b in zip(mn, mx))
        if self.up_axis.upper() == "Y":
            return (mid[0], mn[1], mid[2])
        return (mid[0], mid[1], mn[2])

    @property
    def has_physics(self) -> bool | None:
        """Whether the asset brings its own colliders. False only when the metadata says so (SimReady Central:
        no physics variant and 0 rigid bodies, e.g. the Vertiv racks; Poly Haven models, render meshes only);
        None = not known."""
        if self.variant_selections or (self.rigid_body_count or 0) > 0:
            return True
        if self.source in ("simready-central", "polyhaven") and self.rigid_body_count == 0:
            return False
        return None

    def summary(self) -> dict[str, Any]:
        """Compact, JSON-friendly view for agents and previews."""
        w, d, h = self.size_m
        return {
            "id": self.id,
            "source": self.source,
            "name": self.name,
            "class": self.semantic_class,
            "caption": self.caption,
            "size_m": [round(w, 3), round(d, 3), round(h, 3)],
            "license": self.license,
            "mass_kg": self.mass_kg,
            "uri": self.uri,
            "thumbnail_url": self.thumbnail_url,
            "front_deg": self.front_deg,
            **({"variants": self.variant_sets} if self.variant_sets else {}),
            **({"physics": False} if self.has_physics is False else {}),
            **({"faces": self.polygons} if self.polygons is not None else {}),
            **({"screens": [x["material"] for x in self.screens]} if self.screens else {}),
            "score": self.score,
        }


def wants_box_collider(collider: str, a: AssetRecord) -> bool:
    """ObjectSpec.collider: box = always, none = never, auto = when the asset is known to have no physics."""
    return collider == "box" or (collider == "auto" and a.has_physics is False)
