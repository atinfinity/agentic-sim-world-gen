"""Ceiling fixtures you can see (Room.fixtures) and ceiling beams / soffits (Room.ceiling_features).

Pure Python (no pxr): the 2D preview, the layout checks and agentic_sim_world_gen.usd.look share these plans.

A lighting preset (agentic_sim_world_gen.scene.looks.LIGHTING) puts bare lights on a grid. Room.fixtures replaces those
lights with fixtures of a kind (downlight, pendant, line, panel): a light plus a simple body (a flange, a cord and
a shade, a long housing). The light output per floor area stays the preset's, so changing the kind keeps the room
about as bright.

Beams run across the room under the ceiling (axis x or y, at given positions or evenly spaced); a soffit (下がり壁)
is the same box along one wall. Both are obstacles above their bottom (an object hung from the ceiling or a tall
rack under one is reported as hits_beam), and a fixture under a beam hangs from the beam's bottom.
"""

from __future__ import annotations

import math
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_sim_world_gen.scene.looks import LIGHTING, Surface

FixtureKind = Literal["downlight", "pendant", "line", "panel"]

# per kind: grid spacing [m], size (diameter, or length x width) [m], drop (top of the body below the ceiling to
# the light) [m], body color, emitter shape. Sizes are typical Japanese shop / home fixtures.
KINDS: dict[str, dict[str, Any]] = {
    "downlight": dict(spacing=1.5, size=0.15, drop=0.0, color="#f2f2f2", kelvin=3500.0),
    "pendant": dict(spacing=1.8, size=0.35, drop=0.8, color="#202020", kelvin=2700.0),
    "line": dict(spacing=2.0, size=(1.2, 0.08), drop=0.0, color="#f2f2f2", kelvin=5000.0),
    "panel": dict(spacing=2.4, size=(0.6, 0.6), drop=0.0, color="#f2f2f2", kelvin=5000.0),
}
DEFAULT_DENSITY = 500.0  # light intensity x emitting area per m2 of floor (the office preset's)
SHADE_H = 0.22  # pendant shade height [m]
HEADROOM = 2.1  # a pendant's default cord leaves this much under its shade [m]
LENS = 0.004  # the light sits this far below the body's bottom (no z-fighting, not hidden by it)
# Fixtures that only shine down leave the ceiling to the light bounced off the floor, which the real-time renderer
# draws weak and in the floor's color: a white ceiling came out brown. The ceiling's material glows a little
# instead (emission = its own color x glow). Hidden upward lights under the ceiling did not work: a light's back
# blocks the bounce from the floor, so a large one darkened the ceiling and small ones left bright spots.
FILL = 0.3  # default Fixtures.ceiling_fill
GLOW_PER_LIGHT = 0.002  # emission per (fixture light per m2 of floor x ceiling_fill), measured in Isaac Sim 6.1


class Fixtures(BaseModel):
    """Ceiling fixtures of one kind on a grid; replaces the lighting preset's bare lights."""

    kind: FixtureKind = Field(description="downlight (recessed, round), pendant (cord + shade), line (long housing), "
                                          "panel (square, flush)")
    spacing: float | None = Field(default=None, gt=0, description="grid spacing [m]; default by kind "
                                                                  "(downlight 1.5, pendant 1.8, line 2.0, panel 2.4)")
    size: float | None = Field(default=None, gt=0, description="diameter (downlight, pendant shade) or length "
                                                               "(line, panel side) [m]")
    drop: float | None = Field(default=None, ge=0, description="hang this far below the ceiling [m] (pendant cord, "
                                                               "suspended line); default pendant 0.8, others flush")
    axis: Literal["x", "y"] | None = Field(default=None, description="line: runs along x or y (default the room's "
                                                                     "longer side)")
    color: str | None = Field(default=None, description="body / shade color #rrggbb (default white; pendant black)")
    uplight: float = Field(default=0.0, ge=0, le=1, description="also light the ceiling: an upward light of this "
                                                               "fraction of the downward one (a hanging fixture only: "
                                                               "pendant, or line / panel with drop)")
    ceiling_fill: float | None = Field(default=None, ge=0, le=1, description="light the ceiling as the floor's "
                                       "bounce would: the ceiling glows by this fraction of the fixtures' light "
                                       "(default 0.3 without uplight, 0 = off)")

    @model_validator(mode="after")
    def _up(self) -> "Fixtures":
        if self.uplight and self.kind != "pendant" and not self.drop:
            raise ValueError("uplight needs a hanging fixture: a pendant, or drop > 0 (a recessed light cannot "
                             "shine on the ceiling)")
        return self

    @field_validator("color")
    @classmethod
    def _hex(cls, v):
        if v is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError("color must be #rrggbb")
        return v.lower() if v else v


class CeilingFeature(BaseModel):
    """A box under the ceiling: a beam across the room, or a soffit (下がり壁) along a wall."""

    kind: Literal["beam", "soffit"] = "beam"
    axis: Literal["x", "y"] = Field(default="x", description="beam: runs along x (east-west) or y (north-south)")
    at: list[float] | None = Field(default=None, description="beam: positions across the axis [m] (y for axis x)")
    spacing: float | None = Field(default=None, gt=0, description="beam: evenly every this many m instead of at")
    wall: Literal["south", "north", "west", "east"] | None = Field(default=None, description="soffit: along this wall")
    width: float = Field(default=0.3, gt=0, description="beam width / soffit depth from the wall [m]")
    drop: float = Field(default=0.4, gt=0, description="how far it comes down from the ceiling [m]")
    surface: str | Surface | None = Field(default=None, description="preset or surface; default the ceiling's "
                                                                    "(or white paint)")
    color: str | None = Field(default=None, description="#rrggbb, shorthand for a painted surface")

    @field_validator("at", mode="before")
    @classmethod
    def _one_at(cls, v):
        return [v] if isinstance(v, (int, float)) else v

    @field_validator("wall", mode="before")
    @classmethod
    def _wall_alias(cls, v):
        return {"s": "south", "n": "north", "w": "west", "e": "east"}.get(v.lower(), v.lower()) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _where(self) -> "CeilingFeature":
        if self.kind == "soffit" and self.wall is None:
            raise ValueError("a soffit needs wall (south / north / west / east)")
        if self.kind == "beam" and not self.at and self.spacing is None:
            raise ValueError("a beam needs at (positions) or spacing")
        if self.color is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color):
            raise ValueError("color must be #rrggbb")
        return self


def linear_rgb(hex_: str) -> tuple[float, float, float]:
    """#rrggbb (sRGB) -> linear RGB (what Surface.color holds)."""

    def dec(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return tuple(round(dec(int(hex_[i: i + 2], 16)), 4) for i in (1, 3, 5))  # type: ignore[return-value]


def feature_surface(f: CeilingFeature, ceiling: dict | None) -> dict | str:
    """The surface of a beam / soffit: its own, its color, else the ceiling's (or white paint)."""
    if f.surface is not None:
        return f.surface
    if f.color is not None:
        return {"preset": "paint_white", "color": linear_rgb(f.color)}
    return ceiling["name"] if ceiling and ceiling.get("name") not in (None, "custom") else "paint_white"


def beams(room) -> list[dict[str, Any]]:
    """Boxes of Room.ceiling_features: {id, rect (x0, y0, x1, y1), z (bottom, top), feature}. Over the room's
    extents (a floor plan's bounding box; its walls hide the parts outside the rooms)."""
    if room.outdoor or not room.ceiling_features:
        return []
    w, d, h = room.width, room.depth, room.height
    out = []
    for i, f in enumerate(room.ceiling_features):
        z = (round(h - f.drop, 4), h)
        rects = []
        if f.kind == "soffit":
            rects = [{"south": (0.0, 0.0, w, f.width), "north": (0.0, d - f.width, w, d),
                      "west": (0.0, 0.0, f.width, d), "east": (w - f.width, 0.0, w, d)}[f.wall]]
        else:
            span = d if f.axis == "x" else w
            ats = list(f.at or [])
            if not ats:
                # every `spacing` m, centered in the span (no beam right at a wall)
                n = int((span - 1e-6) / f.spacing)
                ats = [(span - (n - 1) * f.spacing) / 2 + f.spacing * k for k in range(n)] if n else [span / 2]
            for a in ats:
                lo, hi = max(a - f.width / 2, 0.0), min(a + f.width / 2, span)
                if hi > lo:
                    rects.append((0.0, lo, w, hi) if f.axis == "x" else (lo, 0.0, hi, d))
        for j, r in enumerate(rects):
            out.append({"id": f"{f.kind}{i}" + (f"_{j}" if len(rects) > 1 else ""),
                        "rect": tuple(round(v, 4) for v in r), "z": z, "feature": f})
    return out


def ceiling_at(room, x: float, y: float, boxes: list[dict] | None = None) -> float:
    """Underside of the ceiling above (x, y): the room height, or the bottom of a beam / soffit there."""
    top = room.height
    for b in beams(room) if boxes is None else boxes:
        x0, y0, x1, y1 = b["rect"]
        if x0 - 1e-6 <= x <= x1 + 1e-6 and y0 - 1e-6 <= y <= y1 + 1e-6:
            top = min(top, b["z"][0])
    return top


def _area(kind: str, size) -> float:
    """Emitting area of the light of a fixture [m2]."""
    if kind in ("downlight", "pendant"):
        r = _lens_radius(kind, size)
        return math.pi * r * r
    return size[0] * size[1]


def _lens_radius(kind: str, d: float) -> float:
    return d * 0.4 if kind == "downlight" else d * 0.42  # a downlight's lens inside its flange; a shade's opening


def density(room) -> float:
    """Light (intensity x area) per m2 of floor of the room's preset, or DEFAULT_DENSITY without fixtures."""
    p = LIGHTING[room.lighting]
    if not p.get("fixture") or p.get("pole"):
        return DEFAULT_DENSITY
    s = p["size"]
    area = {"rect": lambda: s[0] * s[1], "disk": lambda: math.pi * s * s}.get(p["fixture"], lambda: 4 * math.pi * s * s)()
    return p["intensity"] * area / (p["spacing"] ** 2)


def ceiling_glow(room) -> float:
    """Emission scale of the ceiling's material under Room.fixtures (0 = none): the fixtures' light per m2 of
    floor x ceiling_fill x GLOW_PER_LIGHT. 0 by default under fixtures with an uplight."""
    f = room.fixtures
    if f is None or room.outdoor or not room.ceiling:
        return 0.0
    fill = f.ceiling_fill if f.ceiling_fill is not None else 0.0 if f.uplight else FILL
    return round(fill * density(room) * room.lighting_scale * GLOW_PER_LIGHT, 4)


def kelvin_tint(k: float) -> tuple[float, float, float]:
    """Color of light at k kelvin (Tanner Helland's fit, 0-1 per channel) scaled to luminance 1."""
    t = k / 100
    r = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
    b = 255.0 if t >= 66 else 0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    rgb = [min(max(c, 0.0), 255.0) / 255 for c in (r, g, b)]
    y = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return tuple(round(c / y, 4) for c in rgb)


def ceiling_tint(room) -> tuple[float, float, float]:
    """The fixtures' light color for the ceiling's glow, so a warm room's ceiling is warm like its walls."""
    k = KINDS[room.fixtures.kind] if room.fixtures else {}
    return kelvin_tint(room.light_kelvin or LIGHTING[room.lighting].get("kelvin") or k.get("kelvin") or 5000.0)


def plan(room, points=None) -> list[dict[str, Any]]:
    """Fixtures of Room.fixtures: one dict per fixture with kind, x, y, ceiling (z of the underside it hangs
    from), light (shape, z, size, intensity, kelvin, yaw) and body (parts as boxes / cylinders in world
    coordinates). `points` (the grid) defaults to RoomGeometry(room).fixture_points(spacing)."""
    f = room.fixtures
    if f is None or room.outdoor:
        return []
    k = KINDS[f.kind]
    spacing = f.spacing or k["spacing"]
    if points is None:
        from agentic_sim_world_gen.layout.room import RoomGeometry

        points = RoomGeometry(room).fixture_points(spacing)
    axis = f.axis or ("x" if room.width >= room.depth else "y")
    yaw = 0.0 if axis == "x" else 90.0
    if f.kind in ("downlight", "pendant"):
        size: Any = f.size or k["size"]
    elif f.kind == "line":
        size = (min(f.size or k["size"][0], max(spacing - 0.2, 0.3)), k["size"][1])
    else:
        size = (f.size, f.size) if f.size else k["size"]
    drop = k["drop"] if f.drop is None else f.drop
    if f.kind == "pendant" and f.drop is None:  # keep 2.1 m of headroom under the shade
        drop = round(min(drop, max(room.height - HEADROOM - SHADE_H, 0.1)), 3)
    color = f.color or k["color"]
    area = _area(f.kind, size)
    intensity = density(room) * spacing * spacing / area * room.lighting_scale
    kelvin = room.light_kelvin or LIGHTING[room.lighting].get("kelvin") or k["kelvin"]
    boxes = beams(room)
    out = []
    for x, y in points:
        top = ceiling_at(room, x, y, boxes)
        parts: list[dict[str, Any]] = []
        up: dict[str, Any] = {}
        if f.kind == "downlight":
            parts.append({"shape": "cylinder", "center": (x, y, top - 0.005), "radius": size / 2, "height": 0.01})
            lz, light = top - 0.01 - LENS, {"shape": "disk", "radius": _lens_radius("downlight", size)}
        elif f.kind == "pendant":
            bottom = top - drop - SHADE_H
            if drop > 0:
                parts.append({"shape": "cylinder", "center": (x, y, top - drop / 2), "radius": 0.004, "height": drop})
            parts.append({"shape": "shade", "center": (x, y, bottom + SHADE_H / 2), "radius": size / 2,
                          "top_radius": size * 0.18, "height": SHADE_H})
            lz, light = bottom + 0.02, {"shape": "disk", "radius": _lens_radius("pendant", size)}
            up = {"shape": "disk", "radius": size * 0.18, "z": bottom + SHADE_H + LENS}
        else:
            hb = 0.05 if f.kind == "line" else 0.02
            body = (size[0] + 0.04, size[1] + 0.04, hb)
            if drop > 0:
                for s in (-1, 1):  # two wires
                    u = s * size[0] * 0.4
                    wx, wy = (x + u, y) if yaw == 0 else (x, y + u)
                    parts.append({"shape": "cylinder", "center": (wx, wy, top - drop / 2), "radius": 0.002,
                                  "height": drop})
            parts.append({"shape": "box", "center": (x, y, top - drop - hb / 2), "size": body, "yaw": yaw})
            lz, light = top - drop - hb - LENS, {"shape": "rect", "width": size[0], "height": size[1]}
            up = {**light, "z": top - drop + LENS}
        light.update(z=round(lz, 4), intensity=round(intensity, 1), kelvin=kelvin, yaw=yaw)
        flush = drop == 0 and f.kind != "pendant"
        if f.uplight and not flush:  # emits toward +Z, from the top of the body
            up.update(z=round(up["z"], 4), intensity=round(intensity * f.uplight, 1), kelvin=kelvin, yaw=yaw)
            light["up"] = up
        out.append({"kind": f.kind, "x": x, "y": y, "ceiling": top, "light": light, "parts": parts,
                    "color": color, "flush": flush, "lowest": round(min(_bottom(p) for p in parts), 4)})
    return out


def _bottom(p: dict) -> float:
    h = p["height"] if "height" in p else p["size"][2]
    return p["center"][2] - h / 2
