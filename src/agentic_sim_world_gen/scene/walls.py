"""Wall finishes: a surface per wall (accent walls), wainscot (腰壁) and baseboards (巾木). Data only (no pxr).

Room.walls is one surface for every wall, or a map wall id -> surface:
    {"*": "paint_white", "N": "brick"}             # rectangle: S / N / W / E (or south / north / west / east)
    {"default": "paint_beige", "w3": {"color": [0.2, 0.3, 0.5]}}   # plan: the plan's wall ids
"*" (or "default") is every other wall (paint_white without it). A wall's surface covers both of its sides.

Room.wainscot / Room.baseboard are thin boxes on the inner wall faces (agentic_sim_world_gen.layout.room
RoomGeometry.face_runs), cut at doors and stopping at window sills. In USD they sit under the wall's
prim with a static collider like the wall (no rigid body); the solver keeps against_wall objects
`trim_depth` further out so they stand in front of them (agentic_sim_world_gen.layout.solver).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from agentic_sim_world_gen.scene.looks import Surface, check_surface, linear_from_hex, resolve_surface

DEFAULT_KEYS = ("*", "default")
RECT_ALIASES = {"s": "south", "n": "north", "w": "west", "e": "east",
                "south": "south", "north": "north", "west": "west", "east": "east"}
WALL_JA = {"south": "南", "north": "北", "west": "西", "east": "東"}
_SURFACE_FIELDS = set(Surface.model_fields)


def is_per_wall(walls: Any) -> bool:
    """A wall id -> surface map (not one Surface given as a dict)."""
    return isinstance(walls, dict) and not set(walls) <= _SURFACE_FIELDS


def _key(k: str, rect: bool) -> str:
    return RECT_ALIASES.get(k.lower(), k) if rect else k


def default_value(walls: Any) -> Any:
    """The surface of walls without their own entry (None = the paint_white default)."""
    if not is_per_wall(walls):
        return walls
    return next((walls[k] for k in DEFAULT_KEYS if k in walls), None)


def overrides(walls: Any, rect: bool) -> dict[str, Any]:
    """wall id (rectangle: south / north / west / east) -> its own surface."""
    if not is_per_wall(walls):
        return {}
    return {_key(k, rect): v for k, v in walls.items() if k not in DEFAULT_KEYS}


def wall_value(walls: Any, wall_id: str, rect: bool) -> Any:
    """The surface given for one wall (its own entry, else the default)."""
    return overrides(walls, rect).get(wall_id, default_value(walls))


def wall_surface(walls: Any, wall_id: str, rect: bool) -> dict[str, Any]:
    return resolve_surface(wall_value(walls, wall_id, rect), "paint_white")


def check_walls(walls: Any, wall_ids: list[str], rect: bool) -> None:
    """Raise on unknown presets or wall ids in a per-wall map."""
    if not is_per_wall(walls):
        check_surface(walls, "paint_white")
        return
    for k, v in walls.items():
        if k not in DEFAULT_KEYS and _key(k, rect) not in wall_ids:
            names = "S / N / W / E" if rect else ", ".join(wall_ids)
            raise ValueError(f"room.walls: unknown wall '{k}'; use {names} or '*' for the other walls")
        check_surface(v, "paint_white")


def _with_color(surface: str | Surface, color: tuple[float, float, float] | None, default: str) -> dict[str, Any]:
    if color is None:
        return resolve_surface(surface, default)
    s = Surface(preset=surface) if isinstance(surface, str) else surface.model_copy()
    return resolve_surface(s.model_copy(update={"color": color, "texture": None, "normal": None,
                                                        "roughness_texture": None}), default)


def _check_walls_list(v):
    if v is not None and not v:
        raise ValueError("walls: give at least one wall id, or leave it out for every wall")
    return v


class Wainscot(BaseModel):
    """Panelling on the lower part of the walls (腰壁)."""

    height: float = Field(default=0.9, gt=0, le=3.0, description="top above the floor [m]")
    surface: str | Surface = Field(default="wood_light", description="preset or surface object (like room.walls)")
    color: tuple[float, float, float] | None = Field(default=None, description="linear RGB 0..1 or sRGB '#rrggbb' (drops the texture)")
    thickness: float = Field(default=0.01, gt=0, le=0.05, description="how far it stands out of the wall [m]")
    walls: list[str] | None = Field(default=None, description="only these walls (S/N/W/E or plan ids); default all")

    @field_validator("color", mode="before")
    @classmethod
    def _hex(cls, v):
        return linear_from_hex(v) if isinstance(v, str) else v

    @field_validator("walls")
    @classmethod
    def _walls(cls, v):
        return _check_walls_list(v)

    @field_validator("surface")
    @classmethod
    def _surface(cls, v):
        resolve_surface(v, "wood_light")
        return v

    def resolved(self) -> dict[str, Any]:
        return _with_color(self.surface, self.color, "wood_light")


class Baseboard(BaseModel):
    """Skirting at the foot of the walls (巾木)."""

    height: float = Field(default=0.06, gt=0, le=0.3, description="top above the floor [m]")
    thickness: float = Field(default=0.01, gt=0, le=0.03, description="how far it stands out (of the wainscot) [m]")
    surface: str | Surface = Field(default="trim_white", description="preset (trim_white, trim_wood, ...) or surface")
    color: tuple[float, float, float] | None = Field(default=None, description="linear RGB 0..1 or sRGB '#rrggbb' (drops the texture)")
    walls: list[str] | None = Field(default=None, description="only these walls (S/N/W/E or plan ids); default all")

    @field_validator("color", mode="before")
    @classmethod
    def _hex(cls, v):
        return linear_from_hex(v) if isinstance(v, str) else v

    @field_validator("walls")
    @classmethod
    def _walls(cls, v):
        return _check_walls_list(v)

    @field_validator("surface")
    @classmethod
    def _surface(cls, v):
        resolve_surface(v, "trim_white")
        return v

    def resolved(self) -> dict[str, Any]:
        return _with_color(self.surface, self.color, "trim_white")


def applies(trim: Wainscot | Baseboard | None, wall_id: str, rect: bool) -> bool:
    return trim is not None and (trim.walls is None or wall_id in {_key(k, rect) for k in trim.walls})


def check_trim_walls(trim: Wainscot | Baseboard | None, wall_ids: list[str], rect: bool, what: str) -> None:
    for k in (trim.walls or []) if trim else []:
        if _key(k, rect) not in wall_ids:
            names = "S / N / W / E" if rect else ", ".join(wall_ids)
            raise ValueError(f"room.{what}.walls: unknown wall '{k}'; use {names}")


def trim_depth(room: Any, wall_id: str, above: float = 0.0) -> float:
    """How far wainscot + baseboard stand out of a wall's inner face [m] (0 without trim), for an object whose
    bottom is `above` m over the floor (a poster over the wainscot sits on the wall itself)."""
    rect = room.plan is None
    wt, bt = room.wainscot, room.baseboard
    w = wt.thickness if applies(wt, wall_id, rect) and above < wt.height - 1e-6 else 0.0
    b = bt.thickness if applies(bt, wall_id, rect) and above < bt.height - 1e-6 else 0.0
    return w + b
