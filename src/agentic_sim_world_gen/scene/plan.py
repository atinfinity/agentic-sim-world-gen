"""Floor plan data: wall centerlines, zones (rooms) and openings (doors / windows).

A Room is either a rectangle (width x depth, walls south/north/west/east) or, with `plan`, any set
of straight walls. Openings work for both:
    rectangle: {"kind": "door", "wall": "south", "offset": 1.2, "width": 0.8}
               offset = distance of the opening center from the wall's start (west end of south/north,
               south end of west/east), measured on the room interior.
    plan:      {"kind": "window", "wall": "w3", "offset": 2.0, "width": 1.6}
               offset measured from the wall's `start` along its centerline.

Floor plan JSON (agentic_sim_world_gen.plan.json_plan.load_plan, also produced by the DXF / IFC importers):
    {"units": "m",                              # m | cm | mm | px (needs "underlay"; y down, see below)
     "walls": [{"id": "w0", "start": [0, 0], "end": [8, 0], "thickness": 0.15}, ...],
     "openings": [{"kind": "door", "wall": "w0", "offset": 1.0, "width": 0.8}],
     "zones": [{"id": "ldk", "name": "LDK", "polygon": [[0.075, 0.075], ...]},
               {"id": "wa", "name": "和室", "polygon": [...], "floor": "tatami"}]}
A zone's optional "floor" (a surface like Room.floor, or "room") gives it its own floor; without it the
floor is guessed from the zone name (和室 -> tatami, 洗面 / トイレ / 玄関 -> tile_white), see agentic_sim_world_gen.scene.zone_floors.
Coordinates are meters in the room frame (+X east, +Y north); the importers shift the plan so its
outer bounding box starts at (0, 0).
Tracing an image: "units": "px" with "underlay": {"image": "plan.png", "m_per_px": 0.01} lets walls,
zones and opening offsets/widths be written in image pixels (x right, y down); heights and sills stay
in meters. The preview draws the image under the plan to check the trace.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_sim_world_gen.scene.looks import Surface

Pt = tuple[float, float]

DEFAULTS = {"door": (2.0, 0.0), "window": (1.1, 0.9), "opening": (None, 0.0)}  # (height, sill)


class Opening(BaseModel):
    id: str | None = Field(default=None, description="defaults to <kind>_<index>")
    kind: Literal["door", "window", "opening"] = "door"
    wall: str = Field(description="south/north/west/east for a rectangular room, else a plan wall id")
    offset: float = Field(description="opening center, distance from the wall start [m]")
    width: float = Field(default=0.8, gt=0)
    height: float | None = Field(default=None, gt=0, description="default: door 2.0, window 1.1, opening = wall height")
    sill: float | None = Field(default=None, ge=0, description="bottom height; default: window 0.9, else 0")
    clearance: float | None = Field(
        default=None, ge=0, description="doors/openings: keep this depth free on both sides (default = width, max 0.9)"
    )
    style: Literal["open", "auto_sliding"] = Field(
        default="open",
        description="open: an empty hole. auto_sliding: two glass panels on prismatic joints that slide apart "
        "while anything with a collider is within `sensor` of the doorway (Isaac Sim playback, OmniGraph)",
    )
    sensor: float = Field(default=1.0, gt=0, le=5, description="auto_sliding: detection depth on each side [m]")

    @model_validator(mode="after")
    def _style_kind(self):
        if self.style != "open" and self.kind == "window":
            raise ValueError("style auto_sliding is for doors / openings, not windows")
        return self

    def resolved(self, wall_height: float) -> tuple[float, float]:
        """(sill, top) in meters."""
        h0, s0 = DEFAULTS[self.kind]
        sill = self.sill if self.sill is not None else s0
        h = self.height if self.height is not None else (h0 if h0 is not None else wall_height - sill)
        return sill, min(sill + h, wall_height)

    @property
    def keep_clear(self) -> float:
        if self.kind == "window":
            return 0.0
        return self.clearance if self.clearance is not None else min(self.width, 0.9)


class PlanWall(BaseModel):
    id: str | None = Field(default=None, description="defaults to w<index>")
    start: Pt
    end: Pt
    thickness: float | None = Field(default=None, gt=0, description="default: room.wall_thickness")
    height: float | None = Field(default=None, gt=0, description="default: room.height")


class Zone(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    name: str | None = Field(default=None, description="display name, e.g. 寝室")
    polygon: list[Pt] = Field(min_length=3, description="interior outline (either winding)")
    floor: str | Surface | None = Field(
        default=None,
        description="this zone's floor (preset or surface object); 'room' = Room.floor; default: guessed from "
        "the name (和室 -> tatami, 洗面 / 浴室 / トイレ / 玄関 -> tile_white), else Room.floor",
    )

    @field_validator("floor")
    @classmethod
    def _floor(cls, v):
        from agentic_sim_world_gen.scene.zone_floors import check_value

        return check_value(v)


class Underlay(BaseModel):
    """Source image drawn under the plan preview (traced plans). Pixel (px, py) of the image is at
    x = (px - origin_px[0]) * m_per_px + shift[0], y = (origin_px[1] - py) * m_per_px + shift[1]."""

    image: str = Field(description="PNG / JPEG path (a PDF page must be converted first)")
    m_per_px: float = Field(gt=0, description="meters per image pixel")
    origin_px: Pt = Field(default=(0.0, 0.0), description="image pixel of the plan's (0, 0) before normalizing")
    shift: Pt = Field(default=(0.0, 0.0), description="set by normalize")


class FloorPlan(BaseModel):
    walls: list[PlanWall] = Field(min_length=1)
    zones: list[Zone] = Field(default_factory=list)
    underlay: Underlay | None = None

    @model_validator(mode="after")
    def _ids(self) -> "FloorPlan":
        for i, w in enumerate(self.walls):
            if w.id is None:
                w.id = f"w{i}"
        ids = [w.id for w in self.walls]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate wall ids")
        return self
