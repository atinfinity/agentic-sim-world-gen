"""Scene specification: the single intermediate format every input converges to.

Natural language, photos and floor plans are all translated into a SceneSpec.
Coordinates: Z-up, meters. The room interior spans x in [0, width], y in [0, depth];
the floor top surface is z = 0. With a floor plan (Room.plan, see agentic_sim_world_gen.scene.plan) the walls are
arbitrary segments and width/depth are the plan's outer extents.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.scene.columns import Column, FloorArea
from agentic_sim_world_gen.scene.columns import check as check_columns
from agentic_sim_world_gen.scene.fixtures import CeilingFeature, Fixtures
from agentic_sim_world_gen.scene.levels import Level, Stairs
from agentic_sim_world_gen.scene.levels import check as check_levels
from agentic_sim_world_gen.scene.looks import Surface, check_lighting, check_surface, resolve_surface
from agentic_sim_world_gen.scene.plan import FloorPlan, Opening
from agentic_sim_world_gen.scene.site import Area, Building, Site
from agentic_sim_world_gen.scene.walls import Baseboard, Wainscot, check_trim_walls, check_walls, is_per_wall


# kinds of shop (a picture made for "shop" fits each of them; see validate.place_fits)
STORE_PLACES = ("convenience_store", "supermarket", "drugstore", "home_center", "hundred_yen_shop", "bookstore",
                "electronics_store", "apparel_store", "bakery", "variety_store", "florist", "greengrocer", "toy_store",
                "pet_shop", "garden_center", "shoe_store", "optician", "liquor_store",
                "bicycle_shop", "wagashi_shop", "record_store", "sporting_goods_store", "cosmetics_store",
                "furniture_store", "asian_grocery")
PLACES = ("home", "office", "shop", *STORE_PLACES, "cafe", "restaurant", "factory", "plant_factory",
          "warehouse", "datacenter", "lab", "hospital", "school", "station", "public", "outdoor", "other")
Place = Literal[PLACES]  # type: ignore[valid-type]


class Room(BaseModel):
    name: str = "room"
    place: Place | None = Field(
        default=None,
        description="what the space is (home, office, shop, convenience_store, supermarket, drugstore, home_center, "
        "hundred_yen_shop, bookstore, electronics_store, apparel_store, bakery, variety_store, florist, greengrocer, "
        "toy_store, pet_shop, garden_center, shoe_store, optician, liquor_store, bicycle_shop, wagashi_shop, "
        "record_store, sporting_goods_store, cosmetics_store, furniture_store, asian_grocery, cafe, restaurant, "
        "factory, warehouse, datacenter, lab, hospital, school, station, public, outdoor, other); checks that "
        "built-in screen pictures (shop ads) fit",
    )
    width: float | None = Field(default=None, gt=0, description="interior size along X [m] (set from plan if given)")
    depth: float | None = Field(default=None, gt=0, description="interior size along Y [m] (set from plan if given)")
    height: float = Field(default=2.6, gt=0)
    wall_thickness: float = Field(default=0.12, gt=0)
    floor: str | Surface = Field(
        default="wood_light",
        description="preset (wood_light, wood_dark, tatami, carpet_gray, tile_white, concrete, epoxy_gray, raised_floor) "
        "or {preset/color/roughness/texture/tile_m/mdl/static_friction...}",
    )
    walls: str | Surface | dict[str, str | Surface] = Field(
        default="paint_white",
        description="preset (paint_white, paint_beige, drywall_gray, brick, metal_panel, concrete) or a surface object; "
        "or a surface per wall {wall id: surface, '*': the other walls} (S/N/W/E, or plan wall ids) for an accent wall",
    )
    wainscot: Wainscot | None = Field(
        default=None, description="腰壁: {height 0.9, surface wood_light, color, thickness 0.01, walls} (true = defaults)")
    baseboard: Baseboard | None = Field(
        default=None, description="巾木: {height 0.06, thickness 0.01, surface trim_white, color, walls} (true = defaults)")
    lighting: str = Field(
        default="studio",
        description="studio, daylight, home, office, warehouse, night; outdoor: sun, overcast, dusk, street_night",
    )
    lighting_scale: float = Field(default=1.0, gt=0, description="multiplies every light intensity")
    light_kelvin: float | None = Field(
        default=None, gt=1000, lt=12000,
        description="color temperature of the fixtures and the sun [K] instead of the preset's (warm 2700, neutral "
        "4000, cool 6500); the dome / sky keeps its color")
    ceiling: bool | str | Surface = Field(
        default=False,
        description="add a ceiling slab (blocks the dome light; fixtures still light the room): true (by place: "
        "ceiling_board for home / cafe / restaurant, metal_deck for warehouse / factory / home_center, ceiling_tile for "
        "other indoor places; without a place by lighting), or its own surface: a preset (ceiling_board, "
        "ceiling_tile, metal_deck, paint_white, ...) or a surface object. The ceiling no longer takes the wall surface",
    )
    fixtures: Fixtures | None = Field(
        default=None,
        description="ceiling fixtures you can see, replacing the lighting preset's bare lights: {kind downlight "
        "/ pendant / line / panel, spacing, size, drop (pendant cord / suspended line), axis, color} or just the kind; "
        "the light per floor area stays the preset's (agentic_sim_world_gen.scene.fixtures)",
    )
    ceiling_features: list[CeilingFeature] = Field(
        default_factory=list,
        description="boxes under the ceiling: beams {kind beam, axis x/y, at [m] or spacing, width, drop, surface / "
        "color} and soffits (下がり壁) {kind soffit, wall, width, drop}; obstacles for hung / tall objects (hits_beam)",
    )
    levels: list[Level] = Field(
        default_factory=list,
        description="raised floors (段差・小上がり・一段高い売り場): {id, rect [x0, y0, x1, y1] or polygon, height, "
        "floor / color, rail [edges]}; objects whose center is over one stand on it (on_step_edge if they straddle it)",
    )
    stairs: list[Stairs] = Field(
        default_factory=list,
        description="flights of solid steps: onto a level {id, level, edge, offset, width, rail left/right/both} "
        "or by themselves {id, at [x, y], up south/north/west/east or degrees, rise (default the room height), width, "
        "color}; count = rise / 0.18 rounded up, going 0.28; objects must keep off them (hits_stairs)",
    )
    columns: list[Column] = Field(
        default_factory=list,
        description="columns (柱): {id, at [x, y], size (side m or [x, y]; default 0.5) or diameter (round), yaw_deg, "
        "height (default up to the ceiling), surface / color (default the walls)}; static colliders, objects must keep "
        "off them (hits_column)",
    )
    floor_areas: list[FloorArea] = Field(
        default_factory=list,
        description="parts of the floor with their own finish (any room, a plan zone uses zone_floors): {id, name, "
        "rect [x0, y0, x1, y1] or polygon, floor (preset / surface), color}; later ones lie over earlier ones",
    )

    openings: list[Opening] = Field(default_factory=list, description="doors / windows (see agentic_sim_world_gen.scene.plan)")
    plan: FloorPlan | None = Field(default=None, description="arbitrary walls + zones instead of a rectangle")
    zone_floors: dict[str, str | Surface] = Field(
        default_factory=dict,
        description="plan zone id -> its floor (preset or surface object; 'room' = Room.floor). Overrides the "
        "plan zone's own floor and the guess from its name (和室 -> tatami, 洗面 / トイレ / 玄関 -> tile_white)",
    )
    plan_file: str | None = Field(
        default=None,
        description="floor plan file (.json / .dxf / .ifc) to import into plan + openings (kept as a record)",
    )
    sky: str | None = Field(
        default=None,
        description="dome sky image: clear, partly_cloudy, overcast, evening, night (Isaac Sim skies), a path/URL "
        "to a lat-long HDR, or none; default from the lighting preset",
    )

    # outdoor site (see agentic_sim_world_gen.scene.site): no walls / ceiling; width x depth is the site
    density: Literal["sparse", "normal", "lived_in"] | None = Field(
        default=None,
        description="how lived-in the room looks: sparse = only the objects asked for, normal = a few small things "
        "added per desk / sofa / table / shelf (books, plant, cushions, monitor, products on every shelf), lived_in = more; "
        "the added objects have decor=true and are re-made when this changes (agentic_sim_world_gen.scene.decor)",
    )
    goods: list[str] = Field(
        default_factory=list,
        description="a shop of a kind not in the place list (sporting goods, wagashi, records ...): what its "
        "fixtures hold with density, as English product queries (\"soccer ball\", \"tennis racket\"); used in place of "
        "the place's own products, and a stocked product must name one of their words (or goods_words), and its own query's head noun "
        "plus its qualifier (\"ball\" and \"soccer\" of \"soccer ball\") or a goods_words word. "
        "Sets place to shop when there is none",
    )
    goods_words: list[str] = Field(
        default_factory=list,
        description="more words a product stocked from goods may name instead (the kind: \"sports\", \"wagashi\")",
    )
    outdoor: bool = Field(default=False, description="a site with ground, areas and buildings instead of a walled room")
    ground_margin: float = Field(default=30.0, ge=0, description="outdoor: ground extends this far beyond the site [m]")
    areas: list[Area] = Field(default_factory=list, description="outdoor: road / sidewalk / grass / parking ... (zones)")
    buildings: list[Building] = Field(default_factory=list, description="outdoor: building blocks (obstacles)")
    site: Site | None = Field(
        default=None,
        description="indoor room with the outside around it: ground, road / sidewalk / parking areas, "
        "neighboring buildings and the sky (agentic_sim_world_gen.scene.site.Site); objects outside have zone = an area id or 'outside'",
    )

    @field_validator("site", mode="before")
    @classmethod
    def _site_off(cls, v):
        if v is True:
            return {}  # set_room(site=true): the default site (asphalt, sun)
        return None if v is False else v  # set_room(site=false) removes it

    def footprint(self) -> tuple[float, float, float, float]:
        """The room with its outer walls from above: x0, y0, x1, y1."""
        if self.plan is not None:
            return (0.0, 0.0, self.width, self.depth)
        t = self.wall_thickness
        return (-t, -t, self.width + t, self.depth + t)

    @model_validator(mode="before")
    @classmethod
    def _load_plan_file(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("plan_file") and not data.get("plan"):
            from agentic_sim_world_gen.plan import load_any

            fields = load_any(data["plan_file"])
            fields.pop("warnings", None)
            data = {**data, "plan": fields["plan"], "openings": [*fields["openings"], *data.get("openings", [])]}
            for k in ("height", "wall_thickness"):
                if k in fields and k not in data:
                    data[k] = fields[k]
        return data

    @field_validator("fixtures", mode="before")
    @classmethod
    def _fixture_kind(cls, v):
        if v is False:  # set_room(fixtures=false): back to the lighting preset's fixtures
            return None
        return {"kind": v} if isinstance(v, str) else v

    @model_validator(mode="before")
    @classmethod
    def _outdoor_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("outdoor"):
            data = {"floor": "grass", "lighting": "sun", "height": 10.0, **data}
        return data

    @model_validator(mode="after")
    def _goods_place(self) -> "Room":
        if self.goods and self.place is None:
            self.place = "shop"  # goods are a shop's
        return self

    @model_validator(mode="after")
    def _size(self) -> "Room":
        if self.outdoor:
            if self.plan is not None or self.openings:
                raise ValueError("an outdoor site has no walls: drop plan / openings (use buildings and areas)")
            if self.ceiling or self.fixtures or self.ceiling_features:
                raise ValueError("an outdoor site has no ceiling (nor fixtures / beams under one)")
            if self.levels or self.stairs:
                raise ValueError("an outdoor site has no levels / stairs (use areas with a height)")
        elif self.areas or self.buildings:
            raise ValueError("areas / buildings need outdoor: true")
        ids = [a.id for a in self.areas] + [b.id for b in self.buildings]
        if dup := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"duplicate area / building ids: {dup}")
        if self.site is not None:
            if self.outdoor:
                raise ValueError("site is the outside around an indoor room; an outdoor room has areas / buildings itself")
            sids = [a.id for a in self.site.areas] + [b.id for b in self.site.buildings]
            if dup := sorted({i for i in sids if sids.count(i) > 1 or i in ("outside", "room")}):
                raise ValueError(f"site: duplicate or reserved area / building ids: {dup}")
        if self.plan is not None:
            xs, ys = [], []
            for w in self.plan.walls:
                h = (w.thickness or self.wall_thickness) / 2
                for x, y in (w.start, w.end):
                    xs += [x - h, x + h]
                    ys += [y - h, y + h]
            if min(xs) < -1e-6 or min(ys) < -1e-6:
                raise ValueError(
                    f"plan extends to ({min(xs):.3f}, {min(ys):.3f}); shift it so the outer walls start at (0, 0) "
                    "(agentic_sim_world_gen.plan.json_plan.normalize does this)"
                )
            self.width, self.depth = round(max(xs), 6), round(max(ys), 6)
        elif self.width is None or self.depth is None:
            raise ValueError("room needs width and depth (or a plan)")
        if self.site is not None:
            fx0, fy0, fx1, fy1 = self.footprint()
            sx0, sy0, sx1, sy1 = self.site.bounds(self.footprint())
            if sx0 > fx0 + 1e-6 or sy0 > fy0 + 1e-6 or sx1 < fx1 - 1e-6 or sy1 < fy1 - 1e-6:
                raise ValueError(f"site rect {[sx0, sy0, sx1, sy1]} must contain the room with its walls "
                                 f"{[round(v, 3) for v in (fx0, fy0, fx1, fy1)]}")
        if self.zone_floors:
            known = {z.id for z in self.plan.zones} if self.plan is not None else set()
            if bad := sorted(set(self.zone_floors) - known):
                raise ValueError(f"zone_floors: unknown zone ids {bad}; the plan's zones are {sorted(known)}")
        if (self.columns or self.floor_areas) and not self.outdoor:
            check_columns(self.columns, self.floor_areas, self.width, self.depth,
                          {z.id for z in self.plan.zones} if self.plan is not None else set())
        elif self.columns or self.floor_areas:
            raise ValueError("an outdoor site has no columns / floor areas: use buildings / areas")
        if self.levels or self.stairs:
            check_levels(self.levels, self.stairs, self.width, self.depth, self.height)
        for i, o in enumerate(self.openings):
            if o.id is None:
                o.id = f"{o.kind}_{i}"
        if not self.outdoor:
            rect = self.plan is None
            ids = ["south", "north", "west", "east"] if rect else [w.id for w in self.plan.walls]
            check_walls(self.walls, ids, rect)
            check_trim_walls(self.wainscot, ids, rect, "wainscot")
            check_trim_walls(self.baseboard, ids, rect, "baseboard")
        elif self.wainscot or self.baseboard:
            raise ValueError("an outdoor site has no walls: drop wainscot / baseboard")
        return self

    @field_validator("floor")
    @classmethod
    def _surface(cls, v):
        check_surface(v, "wood_light")  # words (not a preset) are searched by create_scene / set_room
        return v

    @field_validator("walls", mode="before")
    @classmethod
    def _walls(cls, v):
        if isinstance(v, dict) and not is_per_wall(v):
            return Surface.model_validate(v)  # one surface given as a dict, not a wall id -> surface map
        return v

    @field_validator("wainscot", "baseboard", mode="before")
    @classmethod
    def _trim(cls, v):
        if v is True:
            return {}
        return None if v is False else v

    @field_validator("zone_floors")
    @classmethod
    def _zone_floors(cls, v):
        from agentic_sim_world_gen.scene.zone_floors import check_value

        for x in v.values():
            check_value(x)
        return v

    @field_validator("ceiling")
    @classmethod
    def _ceiling(cls, v):
        if not isinstance(v, bool):
            check_surface(v, "ceiling_board")
        return v

    @field_validator("areas", "buildings")
    @classmethod
    def _site_surfaces(cls, v):
        for x in v:
            resolve_surface(x.surface_or_default if isinstance(x, Area) else x.surface, "concrete")
        return v

    @field_validator("lighting")
    @classmethod
    def _lighting(cls, v):
        return check_lighting(v)


Wall = str  # north / south / east / west / any, or a plan wall id
# layout.facing may name a direction instead of an object: front toward that wall
FACING_DIRS = {"east": 0.0, "north": 90.0, "west": 180.0, "south": -90.0}


class Layout(BaseModel):
    """Relational placement, turned into position / yaw_deg / z by agentic_sim_world_gen.layout.solver.

    Walls: south y=0, north y=depth, west x=0, east x=width. "Front" is the side a person uses
    (desk/shelf/sofa front); see agentic_sim_world_gen.layout.solver for the asset front convention.
    """

    against_wall: Wall | None = Field(
        default=None,
        description="back to this wall, front facing into the room: north/south/east/west/any, or a plan wall id (w3)",
    )
    along: float | None = Field(
        default=None, ge=0, le=1,
        description="position along the wall, 0..1 (west->east for north/south, south->north for east/west); "
        "omit to pick a free spot",
    )
    in_front_of: str | None = Field(default=None, description="object id; stand at its front, facing it (chair at desk)")
    next_to: str | None = Field(default=None, description="object id; stand beside it with the same facing")
    side: Literal["left", "right"] = Field(default="right", description="for next_to: right/left as seen by a person facing the target's front (sitting at the desk)")
    on: str | None = Field(default=None, description="object id; sit on top of it (lamp on desk)")
    on_height: float | None = Field(
        default=None, ge=0,
        description="for on: height of the surface above the support's bottom [m] (counter top under a POS "
        "terminal, inside an open stand); default: top of its bounding box",
    )
    offset: tuple[float, float] | None = Field(
        default=None,
        description="for on: shift from the support's center [m] as (right, front): right as seen by a person "
        "facing the support's front, front toward its front (negative = toward its back)",
    )
    facing: str | None = Field(
        default=None,
        description="object id, or east/west/north/south: turn the front toward it (with on: instead of the "
        "support's facing)",
    )
    center: bool = Field(default=False, description="room center")
    ceiling: bool = Field(
        default=False,
        description="hang from the ceiling: top at the room height (ceiling signage, lamps); combine with "
        "against_wall / center / position for x-y",
    )
    wall_height: float | None = Field(
        default=None, ge=0,
        description="hang on the wall: bottom this high above the floor [m] (posters, POP, clocks, shelves on "
        "brackets); with against_wall, gap default 0.005",
    )
    gap: float | None = Field(
        default=None, description="distance to the wall/target [m]; negative = tuck under (only if the knee space is open, else PhysX explodes)"
    )

    @model_validator(mode="after")
    def _on_only(self) -> "Layout":
        if self.on is None and (self.on_height is not None or self.offset is not None):
            raise ValueError("on_height / offset need layout.on")
        if self.wall_height is not None and (self.on or self.ceiling):
            raise ValueError("wall_height hangs on a wall; not with on / ceiling")
        return self


class Array(BaseModel):
    """Repeat the object in a grid: `count` units side by side per row, `rows` rows (see agentic_sim_world_gen.layout.array).
    The block is placed by `layout` like one object; its front is the units' front."""

    count: int = Field(default=1, ge=1, description="units side by side in each row")
    rows: int = Field(default=1, ge=1)
    spacing: float = Field(default=0.0, ge=0, description="gap between units in a row [m]")
    aisle: float = Field(default=1.2, ge=0, description="gap in front of a row [m] (forklift aisle ~3.0)")
    alternate: bool = Field(
        default=False,
        description="rows face each other across aisles and stand back to back otherwise (warehouse racks)",
    )
    back_to_back: bool = Field(
        default=False,
        description="like alternate, but rows start back to back, then face each other across aisles "
        "(store gondola islands, double-sided shelving: 2 rows = one island with shelves on both sides)",
    )
    back_gap: float = Field(default=0.1, ge=0, description="gap between back-to-back rows [m] (alternate / back_to_back)")


class Fill(BaseModel):
    """Stock the shelves of another object with this one (products on a gondola, drinks in a cooler).
    Shelf boards are found from the fixture's geometry (agentic_sim_world_gen.assets.shelves); the solver writes
    `slots`, one per product (see agentic_sim_world_gen.layout.fill)."""

    on: str = Field(description="object id of the fixture (shelf, cooler, showcase); arrays fill every unit")
    levels: list[int] | None = Field(
        default=None, description="shelf levels to stock, 0 = lowest board (default: every level the product fits)"
    )
    span: tuple[float, float] = Field(
        default=(0.0, 1.0), description="part of each board's width to use, 0..1 from the left as seen from the "
        "front (two products side by side: (0, 0.5) and (0.5, 1))",
    )
    rows: int | None = Field(default=None, ge=1, description="product rows from the front (default: as deep as fits)")
    gap: float = Field(default=0.01, ge=0, description="gap between products [m]")
    units: list[str] | None = Field(
        default=None, description="array units of the fixture to stock, e.g. [\"r0c0\", \"r1c2\"] (default: all)"
    )
    lay_flat: bool = Field(
        default=False, description="lay each product on its back, front up and its top toward the back of the "
        "board (hanging blister packs: they stand only on the card's thin bottom edge and fall over)",
    )
    physics: Literal["all", "front", "none"] = Field(
        default="all", description="which products are rigid bodies: all; front = only the front row (the rows "
        "behind are static display stock: they look the same, cost no simulation and do not count toward the "
        "body budget); none = all static"
    )
    fit_only: bool = Field(
        default=False, description="with levels: skip the levels the product does not fit instead of reporting them"
    )
    hang: bool = Field(
        default=False, description="hang the products on the fixture's hooks (pegboards, hook racks; blister packs) "
        "instead of standing them on its shelves: levels = hook rows (0 = lowest), rows = products per hook from "
        "the tip. Hung products are static (the hooks have no colliders)",
    )
    slots: list[tuple[str, float, float, float, float]] = Field(
        default_factory=list, description="set by the solver: (name, x, y, yaw_deg, z) per product"
    )

    def simulated(self, name: str) -> bool:
        """Whether the product in slot `name` (<unit>_l<level>[b<board>]_<col>_<row>) is a rigid body."""
        if self.hang:
            return False
        if self.physics == "all":
            return True
        return self.physics == "front" and name.rsplit("_", 1)[-1] == "0"

    @field_validator("span")
    @classmethod
    def _span(cls, v):
        a, b = v
        if not 0 <= a < b <= 1:
            raise ValueError("span must be (a, b) with 0 <= a < b <= 1")
        return v

    @field_validator("on", mode="before")
    @classmethod
    def _one_host(cls, v: Any) -> Any:
        """A list of one host is that host; several hosts need one object each."""
        if isinstance(v, (list, tuple)):
            if len(v) == 1:
                return v[0]
            raise ValueError(f"fill.on takes one fixture id, not {list(v)}: add one object per fixture "
                             "(or make the fixtures one array, which fills every unit)")
        return v



class Screen(BaseModel):
    """Image shown on the asset's screens (signage; the asset declares them in asset.json `screen`).
    compose fits the image into the screen's face of the texture and records its source in LICENSES.md.
    Source fields left out are read from <stem>.source.json next to the image."""

    image: str | dict[str, str] = Field(
        description="image file for every screen, or screen material name -> image file (assets with several screens); "
        "a relative path is relative to the scene directory (set_screen makes it absolute)"
    )
    fit: Literal["contain", "cover", "stretch"] = Field(
        default="contain", description="contain = whole image, background around it; cover = fill, crop; stretch"
    )
    background: str = Field(default="#000000", description="color around a contained image, #rrggbb")
    url: str | None = Field(default=None, description="where the image came from (page URL)")
    title: str | None = None
    author: str | None = None
    license: str | None = Field(default=None, description="e.g. CC0-1.0, own work")

    @field_validator("background")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError("background must be #rrggbb")
        return v

    def images(self, materials: list[str]) -> dict[str, str]:
        """screen material -> image file, for the asset's screens."""
        if isinstance(self.image, str):
            return {m: self.image for m in materials}
        return dict(self.image)


class Tint(BaseModel):
    """A new color for some of the asset's materials (a red rack where the asset is white).
    compose overrides the shaders' base color in the scene's root layer (UsdPreviewSurface, OmniPBR, OpenPBR);
    on a textured material the color multiplies the texture."""

    color: str = Field(description="#rrggbb (sRGB)")
    materials: list[str] | None = Field(
        default=None,
        description="material names or patterns (fnmatch, e.g. Tray*Mat); null = the asset's main color: the "
        "largest painted area (glass, metal and screens left out) and every material of that same color",
    )
    texture: bool = Field(
        default=True,
        description="false = drop the material's color and normal textures and paint it flat (a printed sign made "
        "plain white); true multiplies the texture by the color, so it can only darken it",
    )

    @field_validator("color")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError("color must be #rrggbb")
        return v.lower()


class ObjectSpec(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", description="USD-safe prim name")
    query: str = Field(description="English search query (translated from the user's Japanese)")
    alt_queries: list[str] = Field(
        default_factory=list, description="extra English synonyms; results of all queries are merged and re-ranked"
    )
    label: str | None = Field(default=None, description="display label, usually Japanese")
    semantic_class: str | None = Field(
        default=None, description="English class label for synthetic data (default: the asset's class, else the query)"
    )
    position: tuple[float, float] | None = Field(
        default=None, description="footprint center (x, y) [m]; P0 requires it, P1 solver fills it"
    )
    yaw_deg: float = 0.0
    facing_deg: float | None = Field(
        default=None,
        description="with position and no layout: the direction the front faces [deg, 0 = east (+X), 90 = north]; "
        "yaw_deg is then set so the chosen asset's front points this way (objects imported from IFC)",
    )
    z: float = Field(default=0.0, description="bottom height [m]; set by the solver for layout.on")
    layout: Layout | None = Field(
        default=None, description="relational placement; when set, the solver overwrites position/yaw_deg/z"
    )
    zone: str | None = Field(default=None, description="floor plan zone id (room) this object belongs to")
    array: Array | None = Field(default=None, description="repeat in rows (racks, desks); ids become <id>.r<row>c<col>")
    fill: Fill | None = Field(default=None, description="stock another object's shelves with this product")
    variants: dict[str, str] = Field(
        default_factory=dict,
        description="USD variant selections for this object: variant set name -> value (e.g. {\"Lighting\": \"Off\"}); "
        "the asset's sets are in its candidate summary (variants)",
    )
    opening: str | None = Field(
        default=None,
        description="room opening id this object fills (door leaf / frame asset); the solver centers it on the "
        "wall and faces it into the room, and the checks skip that wall and the opening's keep-clear area. "
        "A window asset is stretched to the window's width and height (fit_scale)",
    )
    covers: str | None = Field(
        default=None,
        description="window id a curtain / blind hangs in front of: centered on the window on the room side, "
        "the top 0.1 m above the window head, stretched to the window width + 0.2 m",
    )
    fit_scale: tuple[float, float, float] | None = Field(
        default=None, description="set by the solver: stretch along the asset's own x, y, z to fit its opening"
    )
    ifc_guid: str | None = Field(default=None, description="GlobalId of the IFC element this object came from")
    screen: Screen | None = Field(
        default=None, description="image to show on the asset's screens (signage), instead of the asset's own"
    )
    tint: list[Tint] = Field(
        default_factory=list,
        description="new colors for the asset's materials (later entries win); a bare \"#rrggbb\" = the main color",
    )
    collider: Literal["auto", "box", "none"] = Field(
        default="auto",
        description="static box collider (the asset's bbox, invisible, never moves) for assets without physics: "
        "auto = only when the asset is known to have none (SimReady with no PhysX variant and no rigid body), "
        "box = always (check reported no_collider), none = never",
    )
    front_offset_deg: float = Field(
        default=0.0, description="correction added to the assumed asset front (e.g. 180 if it ends up backwards)"
    )
    placed_by_user: bool = Field(
        default=False, description="set by the service: the user put it here (moved it on the preview, or asked "
        "for the move: correction=true); the automatic fixes leave it where it is")
    decor: bool = Field(
        default=False, description="added by Room.density, not asked for; re-made when the density changes"
    )
    seen_in: list[str] | None = Field(
        default=None, description="ids of the input photos this object is seen in. Once any object names a "
        "photo, the others are 'not in that photo' and are warned about (in_photo_view) when they stand in its view"
    )
    min_size: tuple[float, float, float] | None = Field(
        default=None, description="lower bound (footprint long side, short side, height) [m]"
    )
    max_size: tuple[float, float, float] | None = Field(
        default=None, description="upper bound (footprint long side, short side, height) [m]"
    )

    @field_validator("tint", mode="before")
    @classmethod
    def _one_tint(cls, v: Any) -> Any:
        if isinstance(v, (str, dict)):
            v = [v]
        return [{"color": x} if isinstance(x, str) else x for x in v or []]

    @model_validator(mode="after")
    def _fill_alone(self) -> "ObjectSpec":
        if self.fill is not None and (self.layout is not None or self.array is not None):
            raise ValueError(f"{self.id}: fill places the products itself; drop layout / array")
        if self.fill is not None and self.fill.on == self.id:
            raise ValueError(f"{self.id}: fill.on must be another object")
        if self.opening is not None and (self.layout is not None or self.array is not None or self.fill is not None):
            raise ValueError(f"{self.id}: opening places the object itself; drop layout / array / fill")
        if self.covers is not None and (self.opening is not None or self.layout is not None or self.array is not None
                                        or self.fill is not None):
            raise ValueError(f"{self.id}: covers places the object itself; drop opening / layout / array / fill")
        return self


class PhotoCamera(BaseModel):
    """Where the photo was taken from, in room coordinates (z-up, meters, floor top z=0)."""

    position: tuple[float, float, float] = Field(description="camera position [m] (eye height ~1.2-1.6)")
    look_at: tuple[float, float, float] = Field(description="point at the image center [m]")
    hfov_deg: float = Field(default=65.0, gt=5, lt=170, description="horizontal field of view (phone main camera ~65-70)")


class AssumptionFeedback(BaseModel):
    """What the user sent about one assumption row from the preview page; cleared when the row is re-recorded."""

    text: str = Field(description="the user's words, e.g. '西の壁にして'")
    at: str | None = Field(default=None, description="when it was sent (ISO time)")


class Note(BaseModel):
    """What the user wrote at a spot on the preview page: 「ここに棚」「この辺が狭い」. Kept for the agent
    until it handles it (resolve_notes)."""

    id: str = Field(description="n1, n2, ... (numbered pins on the page)")
    text: str = Field(description="the user's words")
    position: tuple[float, float] | None = Field(default=None, description="the spot on the floor (x, y) in meters")
    z: float | None = Field(default=None, description="height of the spot (on top of an object; 0 = the floor)")
    object: str | None = Field(default=None, description="the object (or array unit, <id>.r0c1) at the spot")
    view: Literal["plan", "3d"] = Field(default="plan", description="plan: the top view; 3d: the check's orbit viewer")
    frame: int | None = Field(default=None, description="for 3d: the orbit frame it was written on")
    at: str | None = Field(default=None, description="when it was written (ISO time)")


class Assumption(BaseModel):
    """One decision made for the scene, shown before generating: what the user said vs. what was guessed."""

    item: str = Field(description="what was decided, short: room size, place, floor, walls, lighting, count, "
                      "position, facing, size, asset, ... (Japanese is fine: 部屋の寸法, 台数)")
    value: str = Field(description="the decision in words, e.g. '3.64 x 2.73 m, 天井 2.4 m', '北の壁、窓向き'")
    given: bool = Field(default=False, description="true: the user asked for it (指示どおり); false: guessed (推測)")
    basis: str | None = Field(default=None, description="for a guess, why in a few words: '6畳の標準', "
                              "'コンビニの標準の通路幅'")
    object: str | None = Field(default=None, description="the object id this is about (none for the room)")
    options: list[str] = Field(default_factory=list, description="choices the user can pick for this row on the "
                               "preview page (e.g. the options from plan_questions); kept when the row is re-recorded")
    feedback: AssumptionFeedback | None = Field(default=None, description="set by the preview page (the user says "
                                                "this row is wrong); leave it out: re-recording the row clears it")
    review: str | None = Field(default=None, description="set by the service when the row may be out of date "
                               "(its object's asset was swapped); leave it out: re-recording the row clears it")

    auto: bool = Field(default=False, description="set by the service: a row it wrote from the scene as it is "
                       "; leave it out: a row you record replaces it")

    def key(self) -> tuple[str | None, str]:
        return (self.object, self.item.strip().lower())


class RequestCheck(BaseModel):
    """A relation the service can measure on the placed scene."""

    kind: Literal["against_wall", "next_to", "near", "count", "facing", "in_zone", "on"] = Field(
        description="against_wall: object backs onto target wall (compass / wall id); next_to: within value m "
        "(default 0.5) of target object; near: within value m (default 1.5) of target object or opening; "
        "count: objects together have value units; facing: object faces target (object, opening, or compass); "
        "in_zone: object stands in zone target; on: object sits on target object (its top or a shelf board, "
        "e.g. fill products; target may be one array unit, 'shelf.r0c1')")
    object: str | None = Field(default=None, description="object id (count: use objects)")
    objects: list[str] = Field(default_factory=list, description="count: the objects whose units add up")
    target: str | None = Field(default=None, description="wall / object / opening / zone id or compass name")
    value: float | None = Field(default=None, description="count: units wanted; next_to / near: max gap [m]; "
                                "facing: max angle [deg] (default 45)")


class Requirement(BaseModel):
    """One thing the user asked for, matched against the result after generating."""

    text: str = Field(description="the request in the user's words, one item: '本棚は東の壁', '椅子 2 脚'")
    checks: list[RequestCheck] = Field(default_factory=list, description="measurable relations; empty = judge by eye")
    verdict: Literal["met", "unmet", "unknown"] | None = Field(
        default=None, description="your own judgment when nothing is measurable (style, color, asset choice)")
    note: str | None = Field(default=None, description="what was done / why it is not met, short")


class Taste(BaseModel):
    """The look read off a reference image, filled in by the calling agent from the image; the server only
    records and shows it. Apply it with the usual tools (set_room for floor / walls / lighting, query modifiers in
    alt_queries + select_asset for furniture) and record each choice with set_assumptions."""

    floor: str | None = Field(default=None, description="floor color + material + pattern in English, as given to "
                              "set_room: 'light oak herringbone parquet', or a preset name")
    walls: str | None = Field(default=None, description="walls the same way: 'white paint', 'lime green paint "
                              "accent wall' (say which wall when only one differs)")
    ceiling: str | None = Field(default=None, description="ceiling look if visible: 'white acoustic ceiling tiles'")
    lighting: str | None = Field(default=None, description="lighting preset that fits: home, office, daylight, night ...")
    light_kelvin: float | None = Field(default=None, gt=1000, lt=12000,
                                       description="color temperature of the light [K]: warm 2700-3000, neutral "
                                       "3500-4500, cool 5000-6500")
    lighting_scale: float | None = Field(default=None, gt=0, description="brightness: 0.6 dim / 1 usual / 1.3 bright")
    furniture: dict[str, str] = Field(default_factory=dict, description="kind of furniture (English, or an object "
                                      "id) -> its color / material words: {'desk': 'white lacquer', 'chair': 'black "
                                      "leather with chrome', 'shelf': 'honey-colored wood'}")
    style: list[str] = Field(default_factory=list, description="style words: scandinavian, minimal, rustic, industrial")
    density: str | None = Field(default=None, description="how full the room is: sparse / normal / lived_in")
    note: str | None = Field(default=None, description="one line for the user (Japanese): '明るい北欧風、白い家具と木の床'")

    @field_validator("style", mode="before")
    @classmethod
    def _style_words(cls, v: Any) -> Any:
        """'colorful, cheerful' (one string) -> ['colorful', 'cheerful']."""
        return [w.strip() for w in v.split(",") if w.strip()] if isinstance(v, str) else v


class InputRef(BaseModel):
    """An input the scene was made from (photo, floor plan image ...). Its source is recorded in the USD
    and in LICENSES.md. Missing fields are filled from a sidecar <stem>.source.json next to the file.
    kind "reference" = a reference image (moodboard, web photo) used only for its taste: no camera,
    not rendered by compare_photo."""

    id: str = Field(default="photo", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    kind: Literal["photo", "reference", "plan", "sketch", "other"] = "photo"
    path: str | None = Field(default=None, description="local file")
    url: str | None = Field(default=None, description="where it came from (page URL)")
    title: str | None = None
    author: str | None = None
    license: str | None = Field(default=None, description="e.g. CC0-1.0, public domain, own photo")
    size_px: tuple[int, int] | None = Field(default=None, description="image width, height (read from the file)")
    video: str | None = Field(default=None, description="a frame taken from a video (extract_frames): the video "
                              "file; url/title/author/license are the video's")
    time_s: float | None = Field(default=None, ge=0, description="a video frame: its time in the video [s]")
    camera: PhotoCamera | None = Field(default=None, description="viewpoint of the photo, for compare_photo")
    taste: Taste | None = Field(default=None, description="reference images: the look read off the image")

    @model_validator(mode="after")
    def _reference_has_no_camera(self) -> InputRef:
        if self.kind == "reference" and self.camera is not None:
            raise ValueError(f"input {self.id}: a reference image has no camera (it is not the same room); "
                             "use kind 'photo' to reproduce the room from it")
        return self


ANNOTATORS = (
    "rgb", "bounding_box_2d_tight", "bounding_box_2d_loose", "bounding_box_3d", "semantic_segmentation",
    "instance_segmentation", "instance_id_segmentation", "distance_to_image_plane", "distance_to_camera",
    "normals", "occlusion", "camera_params", "pointcloud",
)
Annotator = Literal[ANNOTATORS]  # type: ignore[valid-type]


class SdgCamera(BaseModel):
    """Random camera poses: standing in free floor space, aimed at an object (or the room center)."""

    height: tuple[float, float] = Field(default=(0.6, 1.8), description="camera height range [m]")
    hfov_deg: tuple[float, float] = Field(default=(60.0, 90.0), description="horizontal field of view range")
    distance: tuple[float, float] = Field(default=(1.2, 6.0), description="distance to the aimed object [m]")
    clearance: float = Field(default=0.3, ge=0, description="minimum gap to walls and objects [m]")
    targets: list[str] = Field(default_factory=list, description="object ids to aim at (default: every object)")
    aim_jitter: float = Field(default=0.25, ge=0, description="random offset of the aim point [m]")
    zone: str | None = Field(default=None, description="floor plan zone to stay in")


class SdgMaterialChoice(BaseModel):
    """Material candidates of one room surface for SDG. Explicit `surfaces` (presets, "mdl:<index id>",
    Surface dicts) and / or a search of the bundled MDL index (agentic_sim_world_gen.materials): `query` (English words,
    with `alt_queries`), filtered by `category` (index category, e.g. "Wood") and `tags` (all must be key
    words / name words). Only a category / tags picks `count` records at random (from the seed).
    A plain list is `surfaces`, a plain string is a preset name or a query."""

    surfaces: list[str | Surface] = Field(default_factory=list)
    query: str | None = None
    alt_queries: list[str] = Field(default_factory=list)
    category: str | list[str] | None = None
    tags: list[str] = Field(default_factory=list)
    count: int = Field(default=6, ge=1, le=64, description="how many searched materials to use")
    keep_current: bool = Field(default=True, description="the room's own surface is a candidate too")

    @model_validator(mode="before")
    @classmethod
    def _short(cls, v: Any) -> Any:
        if isinstance(v, list):
            return {"surfaces": v}
        if isinstance(v, str):
            from agentic_sim_world_gen.scene.looks import SURFACES

            return {"surfaces": [v]} if v in SURFACES or v.startswith("mdl:") else {"query": v}
        return v

    @field_validator("surfaces", mode="before")
    @classmethod
    def _items(cls, v: Any) -> Any:
        from agentic_sim_world_gen.scene.looks import SURFACES

        for x in v or []:
            if isinstance(x, dict) and (bad := set(x) - set(Surface.model_fields)):
                raise ValueError(f"surfaces item {x}: unknown keys {sorted(bad)} (a search goes on the choice itself: "
                                 '{"surfaces": [...], "query": "...", "category": ..., "tags": [...]})')
            if isinstance(x, str) and x not in SURFACES and not x.startswith("mdl:"):
                raise ValueError(f"surfaces item '{x}': not a preset ({sorted(SURFACES)}) or 'mdl:<index id>'")
        return v

    @property
    def searched(self) -> bool:
        return bool(self.query or self.category or self.tags)


class SdgMaterials(BaseModel):
    floor: SdgMaterialChoice | None = Field(default=None, description="floor (outdoor: the ground)")
    walls: SdgMaterialChoice | None = None
    ceiling: SdgMaterialChoice | None = None

    def items(self) -> list[tuple[str, SdgMaterialChoice]]:
        return [(k, v) for k in ("floor", "walls", "ceiling") if (v := getattr(self, k)) is not None]


class SdgRandomize(BaseModel):
    light_scale: tuple[float, float] | None = Field(
        default=(0.5, 1.5), description="multiply every light's intensity by a factor in this range (None = off)"
    )
    color_temp_k: tuple[float, float] | None = Field(
        default=(3000.0, 6500.0), description="light color temperature range [K] (None = keep)"
    )
    room_colors: bool = Field(default=False, description="random floor/wall colors (untextured surfaces only)")
    materials: SdgMaterials = Field(
        default_factory=SdgMaterials,
        description="swap the floor / wall / ceiling material itself per frame (agentic_sim_world_gen.sdg.material_candidates)",
    )


class SdgSpec(BaseModel):
    """Synthetic data generation with Replicator (scripts/isaac/sdg.py)."""

    frames: int = Field(default=20, ge=1, le=100000)
    resolution: tuple[int, int] = (1280, 720)
    seed: int = 0
    annotators: list[Annotator] = Field(  # type: ignore[valid-type]
        default_factory=lambda: ["rgb", "bounding_box_2d_tight", "semantic_segmentation",
                                 "instance_segmentation", "distance_to_image_plane"],
        description="BasicWriter outputs",
    )
    writers: list[Literal["basic", "coco", "kitti"]] = Field(
        default_factory=lambda: ["basic"], description="basic (always written) plus COCO / KITTI datasets"
    )
    rt_subframes: int = Field(default=8, ge=1, le=128, description="renders per frame (more = cleaner)")
    classes: dict[str, str] = Field(
        default_factory=dict,
        description='object id -> class name for this dataset (overrides semantic_class); "" = no label (background)',
    )
    others: Literal["label", "background"] = Field(
        default="label",
        description="objects not in `classes` (when it is given): keep their own class, or leave them unlabelled "
                    "(background: no box, not in COCO / KITTI)",
    )
    structure: bool = Field(
        default=True, description="label floor / walls / ceiling (ground, areas, buildings outdoors); false = background"
    )
    camera: SdgCamera = Field(default_factory=SdgCamera)
    randomize: SdgRandomize = Field(default_factory=SdgRandomize)


class RenderSpec(BaseModel):
    """Renderer settings stored in the root layer (Kit applies them when the stage opens)."""

    transmission_bounces: int = Field(
        default=6, ge=0, le=63,
        description="RTX Real-Time specular/transmission bounces (Kit default 3 shows no liquid inside bottles; "
                    "6 does at ~1.8x the frame time, 12 at ~2.5x). 0 = leave the renderer default",
    )


class ObjectPatch(BaseModel):
    """How one object is placed in a layout option: only the fields given are changed."""

    model_config = ConfigDict(extra="forbid")

    layout: Layout | None = None
    array: Array | None = None
    position: tuple[float, float] | None = None
    yaw_deg: float | None = None
    facing_deg: float | None = None
    zone: str | None = None
    front_offset_deg: float | None = None

    @model_serializer(mode="wrap")
    def _only_given(self, handler):  # a field left out must stay left out after save / load (not become "clear it")
        return {k: v for k, v in handler(self).items() if k in self.model_fields_set}

    def apply(self, o: "ObjectSpec") -> None:
        given = self.model_fields_set
        if "layout" in given:
            o.layout = self.layout
            if self.layout is not None:
                o.opening = o.facing_deg = None
        if "position" in given and self.position is not None:
            o.position = self.position
            if "layout" not in given:
                o.layout = o.opening = None  # placed by hand in this option
        if "array" in given:
            o.array = self.array
        if "yaw_deg" in given and self.yaw_deg is not None:
            o.yaw_deg, o.facing_deg = self.yaw_deg, None
        if "facing_deg" in given:
            o.facing_deg = self.facing_deg
        if "zone" in given:
            o.zone = self.zone
        if "front_offset_deg" in given and self.front_offset_deg is not None:
            o.front_offset_deg = self.front_offset_deg


class LayoutOption(BaseModel):
    """One of 2-3 layouts shown side by side before generating; the assets are shared, only the placement differs."""

    name: str = Field(pattern=r"^[A-Za-z0-9_-]+$", description="short id, e.g. 'island' / 'wall'")
    label: str = Field(description="what it is, in the user's language, e.g. '島型（4 人で向かい合う）'")
    note: str | None = Field(default=None, description="why one might pick it (one line)")
    objects: dict[str, ObjectPatch] = Field(default_factory=dict,
                                            description="object id -> placement in this option (others as they are now)")
    room: dict[Literal["width", "depth"], float] | None = Field(
        default=None, description="a draft option: the room's size in this option (a rectangular room; "
        "objects placed by position move in proportion, door / window offsets too)")
    without: list[str] = Field(default_factory=list, description="a draft option: object ids left out in this "
                               "option (the scene holds everything of the fullest option; decor on them goes too)")


class Contrast(BaseModel):
    """Narrowing down a 「なんか違う」 without a reason: the scene as it is next to one changed in ONE thing,
    the user says which is closer; the steps already answered are not offered again."""

    step: str | None = Field(default=None, description="the comparison open now: size+ / size- (a bigger / smaller "
                             "room), amount- / amount+ (fewer / more things), mood, layout")
    other: str | None = Field(default=None, description="the mood axis: the mood shown next to the scene as it is")
    tried: list[str] = Field(default_factory=list, description="steps the user answered (or said neither to)")


class DecorSkip(BaseModel):
    """A decor item Room.density planned but left out: why, and what on its host was in the way."""

    label: str = Field(description="what was not added, e.g. 'パン（shelf_e）'")
    host: str | None = Field(default=None, description="object it was for (None = a room-level item)")
    reason: Literal["no_asset", "no_spot", "crowded", "no_kind", "heavy"] = Field(
        description="no_asset = only substitutes in the catalog, no_spot = every spot added a problem, "
        "crowded = over the rigid body or drawn face budget, no_kind = a shop of no kind: nothing says what the "
        "fixture holds, heavy = the only matches are too heavy to stock by the hundred (scanned, ~1 M faces)")
    blockers: list[str] = Field(default_factory=list, description="problems the host already had then, e.g. "
                                "'overlap: shelf_e.r0c1, cake_case'; once they are gone the item is tried again")


class MoodChoice(BaseModel):
    """The look picked from the sample pictures (agentic_sim_world_gen.scene.moods)."""

    name: str = Field(description="mood name: natural, white_modern, industrial, calm_dark")
    by: Literal["agent", "preview"] = "agent"
    furniture_pending: bool = Field(default=False, description="picked on the preview page: the agent still has to "
                                    "look for furniture in the mood's colors")
    before_room: dict[str, Any] | None = Field(default=None, description="the room fields the mood replaced, as they "
                                               "were before the first mood (apply_mood name='undo' restores them)")
    before_assumptions: list[dict[str, Any]] | None = Field(default=None, description="the room assumption rows the "
                                                            "mood replaced (restored by undo)")


class PurposeSpec(BaseModel):
    """What the scene is for (agentic_sim_world_gen.scene.purpose): its defaults are recorded as assumptions."""

    name: Literal["demo", "amr", "manipulation", "sdg"]
    robot_radius: float | None = Field(default=None, gt=0, le=2.0,
                                       description="the robot's footprint radius [m] (amr / manipulation; default 0.3)")
    aisle: float | None = Field(default=None, gt=0, le=10.0,
                                description="walking width the robot needs [m]; the usability check uses it when "
                                "wider than the place's own")
    targets: list[str] = Field(default_factory=list,
                               description="sdg: what the detector should find (English words, e.g. 'mug', 'chair')")


class PhotoAisle(BaseModel):
    """An aisle width measured in a photo (measure_photo aisle_pair): what the layout aims at."""

    m: float = Field(gt=0, le=20.0, description="the width [m] (measure_photo best_m)")
    range_m: tuple[float, float] | None = Field(default=None, description="how far the measurement may be off [lo, hi]")
    input: str | None = Field(default=None, description="the photo input it was measured in")
    points: list[tuple[float, float]] = Field(default_factory=list, description="the two floor points [x, y] the "
                                              "width was measured between: the scene's width on that line is compared")


class SceneSpec(BaseModel):
    name: str = "scene"
    room: Room
    objects: list[ObjectSpec] = Field(default_factory=list)
    inputs: list[InputRef] = Field(default_factory=list, description="photos / drawings this scene was made from")
    sdg: SdgSpec | None = Field(default=None, description="synthetic data settings (generate_data)")
    purpose: PurposeSpec | None = Field(default=None, description="what the scene is for (set_purpose)")
    photo_aisle: PhotoAisle | None = Field(default=None, description="the aisle width measured in a photo "
                                           "(measure_photo aisle_pair): the usability widths follow it")
    render: RenderSpec = Field(default_factory=RenderSpec, description="renderer settings written into the USD")
    assumptions: list[Assumption] = Field(default_factory=list,
                                          description="decisions shown before generating: given (指示どおり) or guessed (推測)")
    requirements: list[Requirement] = Field(default_factory=list,
                                            description="the request split into items, matched after generating")
    layout_options: list[LayoutOption] = Field(default_factory=list,
                                               description="layouts to choose from before generating (propose_layouts)")
    options_axis: Literal["size", "amount", "composition"] | None = Field(
        default=None, description="what the open layout_options differ in when they are draft options ("
        "propose_drafts); None = layouts of the same things (propose_layouts)")
    mood: MoodChoice | None = Field(default=None, description="the look picked from the sample pictures (apply_mood)")
    contrast: Contrast | None = Field(default=None, description="the two-way comparisons after a 「なんか違う」 ("
                                      "propose_contrast): the one open and the ones answered")
    decor_skipped: list[DecorSkip] = Field(default_factory=list,
                                           description="decor items Room.density left out, with the reason")
    notes: list[Note] = Field(default_factory=list, description="what the user wrote at spots on the preview")
    auto_fix: bool = Field(default=True, description="apply the fixes that need no judgement at every save: widen a "
                           "narrow aisle, part touching jointed chairs, hang a footless TV on the wall behind its "
                           "stand (reported as auto_fixed). false: only report them as issues")
    lang: str | None = Field(default=None, description="the user's language ('ja', 'en'); unset: guessed from the "
                             "labels, request items and assumptions written in the user's words")

    @classmethod
    def load(cls, path: str | Path) -> "SceneSpec":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


# --- resolved scene (spec + chosen assets) ---------------------------------


def record_to_dict(r: AssetRecord) -> dict[str, Any]:
    d = asdict(r)
    d.pop("raw", None)
    return d


_RECORD_FIELDS = {f.name for f in fields(AssetRecord)}


def record_from_dict(d: dict[str, Any]) -> AssetRecord:
    """Inverse of record_to_dict. Extra keys (e.g. "match" added by rerank) are ignored."""
    if "screens" not in d and str(d.get("source", "")).startswith("local:"):  # saved before screens existed
        from agentic_sim_world_gen.assets.providers.local_folder import screens_of

        d = {**d, "screens": screens_of(Path(d["uri"]))}
    d = {k: v for k, v in d.items() if k in _RECORD_FIELDS}
    d["bbox_min"] = tuple(d["bbox_min"])
    d["bbox_max"] = tuple(d["bbox_max"])
    return AssetRecord(**d)


class Placement(BaseModel):
    """One object with its candidate assets. Edit `selected` to swap assets."""

    object_id: str
    selected: int = 0
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    # good matches that the size limits dropped (only filled when every candidate is weak)
    size_misses: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def substitute(self) -> bool:
        """Every candidate is a weak match: the requested kind of object was not found."""
        return bool(self.candidates) and all((c.get("match") or {}).get("weak") for c in self.candidates)

    @property
    def asset(self) -> AssetRecord | None:
        if not self.candidates:
            return None
        return record_from_dict(self.candidates[self.selected])


class ResolvedScene(BaseModel):
    spec: SceneSpec
    placements: list[Placement]

    def obj(self, object_id: str) -> ObjectSpec:
        return next(o for o in self.spec.objects if o.id == object_id)

    def save(self, path: str | Path) -> None:
        # write then rename, so the preview's USD thread never reads half a file
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(self.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "ResolvedScene":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
