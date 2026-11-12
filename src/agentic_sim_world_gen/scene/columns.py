"""Columns (柱) and floor areas (床の一部の仕上げ) of a room. Pure Python (no pxr).

A column is a solid box or round post from the floor up to the ceiling (or `height`): a static collider and an
obstacle (hits_column), drawn in layout.svg and blocking the nav map. A floor area is a rect / polygon of the floor
with its own finish (タイルの入口・カーペットの一角), in any room; it is built like a plan zone's floor (a slab
with its top at z=0, the room's floor sinks under it).

    "columns": [{"id": "c1", "at": [4, 3], "size": 0.6},
                {"id": "c2", "at": [8, 3], "diameter": 0.5, "color": "#e8e8e8"}],
    "floor_areas": [{"id": "entrance", "rect": [3, 0, 5, 1.5], "floor": "tile_white"}]
"""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_sim_world_gen.scene.looks import Surface, resolve_surface
from agentic_sim_world_gen.scene.site import Pt, _outline, ccw

ROUND_SIDES = 24  # a round column's outline (and its collider) is this many-sided


def _color_ok(c: str | None) -> None:
    if c is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", c):
        raise ValueError("color must be #rrggbb")


class Column(BaseModel):
    """A post from the floor to the ceiling: square / rectangular (size) or round (diameter)."""

    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    at: Pt = Field(description="its center [x, y] (m, room coordinates)")
    size: float | tuple[float, float] | None = Field(
        default=None, description="box: side [m] or [along x, along y] (before yaw_deg); default 0.5")
    diameter: float | None = Field(default=None, gt=0.05, le=3.0, description="round column instead of a box [m]")
    yaw_deg: float = Field(default=0.0, description="box: turn it (degrees, counter-clockwise)")
    height: float | None = Field(default=None, gt=0, description="default up to the ceiling (the room height)")
    surface: str | Surface | None = Field(default=None, description="default the walls' surface")
    color: str | None = Field(default=None, description="#rrggbb, shorthand for a painted column")

    @field_validator("size", mode="before")
    @classmethod
    def _size(cls, v):
        if isinstance(v, (list, tuple)) and len(v) == 1:
            return float(v[0])
        return v

    @model_validator(mode="after")
    def _check(self) -> "Column":
        _color_ok(self.color)
        if self.diameter is not None and self.size is not None:
            raise ValueError(f"column {self.id}: give size (box) or diameter (round), not both")
        sx, sy = self.dims
        if not (0.05 < sx <= 5.0 and 0.05 < sy <= 5.0):
            raise ValueError(f"column {self.id}: size {sx:g} x {sy:g} m is out of 0.05-5 m")
        if self.surface is not None:
            resolve_surface(self.surface, "paint_white")
        return self

    @property
    def shape(self) -> Literal["box", "round"]:
        return "round" if self.diameter is not None else "box"

    @property
    def dims(self) -> tuple[float, float]:
        if self.diameter is not None:
            return self.diameter, self.diameter
        if self.size is None:
            return 0.5, 0.5
        return (float(self.size), float(self.size)) if isinstance(self.size, (int, float)) else tuple(self.size)

    @property
    def poly(self) -> list[Pt]:
        """Its outline on the floor, counter-clockwise."""
        cx, cy = self.at
        if self.diameter is not None:
            r = self.diameter / 2
            return [(cx + r * math.cos(2 * math.pi * k / ROUND_SIDES), cy + r * math.sin(2 * math.pi * k / ROUND_SIDES))
                    for k in range(ROUND_SIDES)]
        sx, sy = self.dims
        c, s = math.cos(math.radians(self.yaw_deg)), math.sin(math.radians(self.yaw_deg))
        return [(cx + c * x - s * y, cy + s * x + c * y)
                for x, y in ((-sx / 2, -sy / 2), (sx / 2, -sy / 2), (sx / 2, sy / 2), (-sx / 2, sy / 2))]

    def top(self, room_height: float) -> float:
        return self.height if self.height is not None else room_height


class FloorArea(BaseModel):
    """Part of the floor with its own finish."""

    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    name: str | None = Field(default=None, description="display name, e.g. 入口のタイル")
    rect: tuple[float, float, float, float] | None = Field(default=None, description="[x0, y0, x1, y1]")
    polygon: list[Pt] | None = Field(default=None, min_length=3, description="outline instead of rect")
    floor: str | Surface | None = Field(default=None, description="its floor: a preset or a surface")
    color: str | None = Field(default=None, description="#rrggbb, shorthand for a painted floor")

    @model_validator(mode="after")
    def _check(self) -> "FloorArea":
        _outline(self.polygon, self.rect, f"floor area {self.id}")
        _color_ok(self.color)
        if self.floor is None and self.color is None:
            raise ValueError(f"floor area {self.id}: give its floor (a preset such as tile_white) or color")
        return self  # the preset is checked by the room (check()): set_room first turns words into a surface

    @property
    def poly(self) -> list[Pt]:
        return ccw(_outline(self.polygon, self.rect, self.id))

    def surface(self) -> dict:
        """Its resolved floor (color repaints the preset / surface)."""
        if self.color is None:
            return resolve_surface(self.floor, "wood_light")
        from agentic_sim_world_gen.scene.looks import linear_from_hex

        f = self.floor if self.floor is not None else "paint_white"
        base = {"preset": f} if isinstance(f, str) else f.model_dump(exclude_none=True)
        return resolve_surface({**base, "color": linear_from_hex(self.color)}, "wood_light")


def check(columns: list[Column], areas: list[FloorArea], width: float, depth: float, zone_ids: set[str]) -> None:
    """Ids unique; column centers and floor areas inside the room; floor area ids apart from plan zone ids."""
    ids = [c.id for c in columns] + [a.id for a in areas]
    if dup := sorted({i for i in ids if ids.count(i) > 1}):
        raise ValueError(f"duplicate column / floor area ids: {dup}")
    for c in columns:
        x, y = c.at
        if not (0 < x < width and 0 < y < depth):
            raise ValueError(f"column {c.id} at ({x:g}, {y:g}) is outside the room {width:g} x {depth:g}")
    for a in areas:
        a.surface()  # raises on unknown presets
        if a.id in zone_ids:
            raise ValueError(f"floor area id '{a.id}' is a plan zone id: use zone_floors for a whole zone, or rename it")
        for x, y in a.poly:
            if not (-1e-6 <= x <= width + 1e-6 and -1e-6 <= y <= depth + 1e-6):
                raise ValueError(f"floor area {a.id} goes outside the room ({x:g}, {y:g}) vs {width:g} x {depth:g}")


def describe(room) -> dict[str, str | None]:
    """Short Japanese summaries for the change history: 柱 2 本（0.6 m 角・直径 0.5 m）, 床の一部 入口：tile_white."""
    cols = None
    if room.columns:
        kinds = []
        for c in room.columns:
            k = f"直径 {c.diameter:g} m" if c.diameter is not None else (
                f"{c.dims[0]:g} m 角" if c.dims[0] == c.dims[1] else f"{c.dims[0]:g}×{c.dims[1]:g} m")
            if k not in kinds:
                kinds.append(k)
        cols = f"{len(room.columns)} 本（{'・'.join(kinds)}）"
    areas = None
    if room.floor_areas:
        areas = "、".join(f"{a.name or a.id}：{a.surface()['name']}" for a in room.floor_areas)
    return {"columns": cols, "floor_areas": areas}
