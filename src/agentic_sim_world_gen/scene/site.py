"""Outdoor site data: ground areas (road, sidewalk, grass, ...) and buildings. Used with Room.outdoor.

An outdoor Room is a site of width x depth (x east, y north, ground top z = 0). Objects stand inside
the site; the ground extends Room.ground_margin beyond it so the horizon is not a cliff.
    {"outdoor": true, "width": 40, "depth": 30, "floor": "grass", "lighting": "sun",
     "areas": [{"id": "road", "kind": "road", "rect": [0, 0, 40, 7]},
               {"id": "walk", "kind": "sidewalk", "rect": [0, 7, 40, 10]}],
     "buildings": [{"id": "shop", "rect": [5, 18, 20, 28], "height": 6, "surface": "brick"}]}
Areas are zones (ObjectSpec.zone = area id; their edges are faces for against_wall). A road keeps
clear of objects unless the object's zone is that road (a parked car, a traffic cone). A raised area
(a sidewalk's curb) lifts the objects placed on it by the solver.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from agentic_sim_world_gen.scene.looks import Surface

Pt = tuple[float, float]

AreaKind = Literal["road", "sidewalk", "crosswalk", "parking", "plaza", "path", "grass", "gravel", "soil", "water", "other"]

# kind -> (surface preset, curb height [m], keep clear)
AREA_DEFAULTS: dict[str, tuple[str, float, bool]] = {
    "road": ("asphalt", 0.0, True),
    "sidewalk": ("paving", 0.15, False),
    "crosswalk": ("crosswalk", 0.0, True),
    "parking": ("asphalt", 0.0, False),
    "plaza": ("paving", 0.0, False),
    "path": ("gravel", 0.0, False),
    "grass": ("grass", 0.0, False),
    "gravel": ("gravel", 0.0, False),
    "soil": ("soil", 0.0, False),
    "water": ("water", 0.0, True),
    "other": ("concrete", 0.0, False),
}


def _outline(polygon: list[Pt] | None, rect: tuple[float, float, float, float] | None, what: str) -> list[Pt]:
    if (polygon is None) == (rect is None):
        raise ValueError(f"{what}: give either polygon or rect [x0, y0, x1, y1]")
    if rect is not None:
        x0, y0, x1, y1 = rect
        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"{what}: rect needs x0 < x1 and y0 < y1")
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [tuple(p) for p in polygon]


def signed_area(poly: list[Pt]) -> float:
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
               for i in range(len(poly))) / 2


def ccw(poly: list[Pt]) -> list[Pt]:
    """Counter-clockwise copy of a polygon."""
    return list(poly) if signed_area(poly) > 0 else list(reversed(poly))


class Area(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    kind: AreaKind = "other"
    name: str | None = Field(default=None, description="display name, e.g. 歩道")
    polygon: list[Pt] | None = Field(default=None, min_length=3, description="outline (either winding)")
    rect: tuple[float, float, float, float] | None = Field(default=None, description="[x0, y0, x1, y1] instead of polygon")
    surface: str | Surface | None = Field(default=None, description="default by kind (road asphalt, sidewalk paving, ...)")
    height: float | None = Field(default=None, ge=0, le=1, description="top above the ground [m]; sidewalk default 0.15 (curb)")
    keep_clear: bool | None = Field(default=None, description="objects outside this zone must not stand here (default: road, crosswalk, water)")

    @model_validator(mode="after")
    def _check(self) -> "Area":
        _outline(self.polygon, self.rect, f"area {self.id}")
        return self

    @property
    def poly(self) -> list[Pt]:
        return ccw(_outline(self.polygon, self.rect, self.id))

    @property
    def top(self) -> float:
        return AREA_DEFAULTS[self.kind][1] if self.height is None else self.height

    @property
    def clear(self) -> bool:
        return AREA_DEFAULTS[self.kind][2] if self.keep_clear is None else self.keep_clear

    @property
    def surface_or_default(self) -> str | Surface:
        return self.surface if self.surface is not None else AREA_DEFAULTS[self.kind][0]


class Building(BaseModel):
    """A closed block (walls + flat roof) standing on the ground; the inside is not modeled."""

    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    name: str | None = None
    polygon: list[Pt] | None = Field(default=None, min_length=3)
    rect: tuple[float, float, float, float] | None = Field(default=None, description="[x0, y0, x1, y1]")
    height: float = Field(default=6.0, gt=0)
    surface: str | Surface = Field(default="concrete", description="facade (and roof) surface")

    @model_validator(mode="after")
    def _check(self) -> "Building":
        _outline(self.polygon, self.rect, f"building {self.id}")
        return self

    @property
    def poly(self) -> list[Pt]:
        return ccw(_outline(self.polygon, self.rect, self.id))


def is_convex(poly: list[Pt]) -> bool:
    sign = 0
    n = len(poly)
    for i in range(n):
        (x0, y0), (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n], poly[(i + 2) % n]
        c = (x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1)
        if abs(c) < 1e-12:
            continue
        if sign == 0:
            sign = 1 if c > 0 else -1
        elif (c > 0) != (sign > 0):
            return False
    return True


def triangulate(poly: list[Pt]) -> list[tuple[int, int, int]]:
    """Ear clipping of a simple polygon (any winding). Returns index triples, counter-clockwise."""
    pts = ccw(poly)
    n = len(poly)
    idx = list(range(n)) if signed_area(poly) > 0 else [n - 1 - i for i in range(n)]  # back to the caller's indices
    tris = []
    ring = list(range(len(pts)))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= -1e-12 and cross(b, c, p) >= -1e-12 and cross(c, a, p) >= -1e-12

    guard = 0
    while len(ring) > 3 and guard < 10 * len(pts) ** 2:
        guard += 1
        for k in range(len(ring)):
            i, j, l = ring[k - 1], ring[k], ring[(k + 1) % len(ring)]
            a, b, c = pts[i], pts[j], pts[l]
            if cross(a, b, c) <= 1e-12:
                continue  # reflex or degenerate
            if any(inside(pts[m], a, b, c) for m in ring if m not in (i, j, l)):
                continue
            tris.append((i, j, l))
            ring.pop(k)
            break
        else:  # no ear found (self-intersecting): fall back to a fan
            break
    for k in range(1, len(ring) - 1):
        tris.append((ring[0], ring[k], ring[k + 1]))
    return [(idx[i], idx[j], idx[k]) for i, j, k in tris]


def convex_pieces(poly: list[Pt]) -> list[list[Pt]]:
    """The polygon itself if convex, else its triangles (the checks use convex shapes)."""
    if is_convex(poly):
        return [list(poly)]
    return [[poly[i], poly[j], poly[k]] for i, j, k in triangulate(poly)]


SITE_LIGHTING = ("sun", "overcast", "dusk", "street_night")


class Site(BaseModel):
    """The outside around an indoor room: a shop with its sidewalk, road and parking in one scene.

    Room coordinates as usual (the room's inside is x in [0, width], y in [0, depth]); the site may reach to
    negative x / y. Objects outside have ObjectSpec.zone = an area id or "outside" (the site without a
    particular area). The room's walls are the building: against_wall "outside:south" puts an object with its
    back to the room's south wall, outside (a vending machine by the shop's entrance). Openings in the room's
    walls connect inside and outside.
        {"rect": [-6, -10, 14, 12], "ground": "asphalt", "lighting": "sun",
         "areas": [{"id": "walk", "kind": "sidewalk", "rect": [-6, -4, 14, -0.2]},
                   {"id": "road", "kind": "road", "rect": [-6, -10, 14, -4]}]}
    """

    rect: tuple[float, float, float, float] | None = Field(
        default=None, description="[x0, y0, x1, y1] in room coordinates; default: the room grown by `around`")
    around: float = Field(default=8.0, ge=0, description="site beyond the room's outer walls when rect is not given [m]")
    ground: str | Surface = Field(default="asphalt", description="ground surface between the areas")
    ground_margin: float = Field(default=30.0, ge=0, description="ground extends this far beyond the site [m]")
    areas: list[Area] = Field(default_factory=list, description="road / sidewalk / parking / grass ... (zones)")
    buildings: list[Building] = Field(default_factory=list, description="neighboring buildings (closed blocks)")
    lighting: Literal["sun", "overcast", "dusk", "street_night"] = Field(
        default="sun", description="the sky and sun outside (the room keeps its own lighting inside)")
    sky: str | None = Field(default=None, description="sky image (as Room.sky); default from the lighting")

    def bounds(self, footprint: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        """The site rectangle: rect, else the room's footprint (outer walls) grown by `around`."""
        if self.rect is not None:
            return tuple(self.rect)
        x0, y0, x1, y1 = footprint
        a = self.around
        return (x0 - a, y0 - a, x1 + a, y1 + a)
