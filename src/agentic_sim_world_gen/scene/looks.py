"""Surface (floor / wall) and lighting presets. Data only (no pxr), shared by spec, preview and USD.

A surface is a preset name or a Surface object:
    "wood_light"
    {"preset": "concrete", "color": [0.5, 0.5, 0.5]}               # preset with an override
    {"color": [0.8, 0.2, 0.2], "roughness": 0.6}                    # plain UsdPreviewSurface
    {"texture": "/data/tex/oak.jpg", "tile_m": 1.0}                 # tiled diffuse texture (+ "normal" map)
    {"mdl": "https://.../Oak.mdl", "mdl_name": "Oak"}               # Omniverse MDL material (RTX only)

Indoor presets carry built-in textures (TEXTURES: planks, tatami borders, grout, brick, ...), drawn by
scripts/make_textures.py into agentic-sim-world-gen/data/textures/ and copied next to the scene by compose. Their `color`
is the average shade, used by the 2D preview; overriding `color` (or giving `texture`) drops the built-in texture.

Colors are linear RGB 0..1. Friction is written as a physics material on the floor (and walls).
Light intensities were tuned in Isaac Sim 6.1 RTX (default tonemapper, no auto exposure) with
scripts/isaac/look_views.py; scale them with Room.lighting_scale.
studio / daylight light the room from outside (dome + sun), so a closed room with a ceiling is dark inside.
Outdoor presets (sun, overcast, dusk, street_night) put a sky image (SKIES: HDRIs bundled with Isaac Sim)
on the dome; there the dome intensity scales the image. Room.sky overrides the preset's sky.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Surface(BaseModel):
    preset: str | None = Field(default=None, description="start from a preset (see SURFACES)")
    color: tuple[float, float, float] | None = Field(
        default=None, description="linear RGB 0..1, or an sRGB hex string '#a8d8c8' as picked from an image "
        "(converted to linear; a color read off a photo includes the light, see the skill)")
    roughness: float | None = Field(default=None, ge=0, le=1)
    metallic: float | None = Field(default=None, ge=0, le=1)
    texture: str | None = Field(default=None, description="diffuse texture path or URL, tiled every tile_m")
    normal: str | None = Field(default=None, description="tangent-space normal map (OpenGL, +Y up), tiled like texture")
    roughness_texture: str | None = Field(default=None, description="roughness map (grayscale), tiled like texture; "
                                                                    "replaces roughness in the preview surface")
    tile_m: float | None = Field(default=None, gt=0, description="texture repeat size [m] (default 1, or the preset's)")
    mdl: str | None = Field(default=None, description="MDL module path/URL (Omniverse RTX); preview color still used elsewhere")
    mdl_name: str | None = Field(default=None, description="material name inside the MDL module (default: file stem)")
    mdl_inputs: dict[str, bool | int | float | list[float]] | None = Field(
        default=None, description="MDL parameter overrides written on the shader, e.g. {'texture_scale': [1.6, 1.6]} "
                                  "(float2 = 2 numbers, color = 3)")
    static_friction: float | None = Field(default=None, ge=0)
    dynamic_friction: float | None = Field(default=None, ge=0)

    @field_validator("color", mode="before")
    @classmethod
    def _hex(cls, v: Any) -> Any:
        return linear_from_hex(v) if isinstance(v, str) else v


# name -> (color, roughness, metallic, static friction, dynamic friction, description)
SURFACES: dict[str, dict[str, Any]] = {
    # floors
    "wood_light": dict(tile_m=1.2, color=(0.55, 0.36, 0.19), roughness=0.45, static_friction=0.5, dynamic_friction=0.4, note="light wood flooring"),
    "wood_dark": dict(tile_m=1.2, color=(0.30, 0.18, 0.10), roughness=0.4, static_friction=0.5, dynamic_friction=0.4, note="dark wood flooring"),
    "tatami": dict(tile_m=1.82, color=(0.66, 0.62, 0.40), roughness=0.85, static_friction=0.6, dynamic_friction=0.5, note="畳"),
    "carpet_gray": dict(tile_m=1.0, color=(0.30, 0.30, 0.32), roughness=0.95, static_friction=0.8, dynamic_friction=0.7, note="office carpet tiles"),
    "tile_white": dict(tile_m=1.2, color=(0.85, 0.85, 0.83), roughness=0.25, static_friction=0.45, dynamic_friction=0.35, note="ceramic / vinyl tiles"),
    "concrete": dict(tile_m=2.0, color=(0.50, 0.50, 0.48), roughness=0.9, static_friction=0.7, dynamic_friction=0.6, note="bare concrete"),
    "epoxy_gray": dict(tile_m=2.0, color=(0.40, 0.43, 0.45), roughness=0.3, static_friction=0.6, dynamic_friction=0.5, note="warehouse / factory epoxy floor"),
    "raised_floor": dict(tile_m=1.2, color=(0.55, 0.56, 0.58), roughness=0.5, static_friction=0.5, dynamic_friction=0.4, note="data center raised floor"),
    # walls
    "paint_white": dict(tile_m=1.0, color=(0.85, 0.85, 0.82), roughness=0.8, note="white painted wall"),
    "paint_beige": dict(tile_m=1.0, color=(0.78, 0.70, 0.58), roughness=0.8, note="beige painted wall"),
    "drywall_gray": dict(tile_m=1.0, color=(0.60, 0.61, 0.62), roughness=0.85, note="gray wall"),
    "brick": dict(tile_m=0.9, color=(0.50, 0.25, 0.18), roughness=0.9, note="red brick"),
    "metal_panel": dict(tile_m=1.0, color=(0.65, 0.67, 0.70), roughness=0.4, metallic=0.6, note="warehouse sandwich panel"),
    # wall trim (agentic_sim_world_gen.scene.walls: baseboards, wainscot)
    "trim_white": dict(tile_m=1.0, color=(0.86, 0.86, 0.84), roughness=0.45, note="white painted trim / baseboard"),
    "trim_wood": dict(tile_m=1.0, color=(0.36, 0.22, 0.12), roughness=0.5, note="stained wood trim / baseboard"),
    # ceilings
    "ceiling_board": dict(tile_m=1.82, color=(0.80, 0.80, 0.78), roughness=0.9, note="white gypsum ceiling board (化粧石膏ボード)"),
    "ceiling_tile": dict(tile_m=1.2, color=(0.78, 0.78, 0.77), roughness=0.9, note="office system ceiling, 600 mm tiles in a grid"),
    "metal_deck": dict(tile_m=1.0, color=(0.58, 0.60, 0.62), roughness=0.45, metallic=0.7, note="exposed folded steel roof deck (折板), warehouse / factory"),
    # outdoor ground / areas / facades
    "grass": dict(color=(0.16, 0.30, 0.08), roughness=0.95, static_friction=0.6, dynamic_friction=0.5, note="lawn"),
    "asphalt": dict(color=(0.09, 0.09, 0.10), roughness=0.85, static_friction=0.8, dynamic_friction=0.7, note="road / parking asphalt"),
    "paving": dict(color=(0.45, 0.43, 0.40), roughness=0.8, static_friction=0.7, dynamic_friction=0.6, note="sidewalk / plaza paving"),
    "crosswalk": dict(color=(0.75, 0.75, 0.72), roughness=0.7, static_friction=0.7, dynamic_friction=0.6, note="painted crossing"),
    "gravel": dict(color=(0.38, 0.36, 0.33), roughness=1.0, static_friction=0.6, dynamic_friction=0.5, note="gravel path"),
    "soil": dict(color=(0.22, 0.15, 0.09), roughness=1.0, static_friction=0.6, dynamic_friction=0.5, note="bare soil"),
    "water": dict(color=(0.05, 0.12, 0.16), roughness=0.05, note="pond (a flat surface, no fluid)"),
    "glass": dict(color=(0.25, 0.32, 0.36), roughness=0.1, metallic=0.3, note="glass curtain wall (opaque)"),
}
SURFACE_DEFAULTS = dict(color=(0.8, 0.8, 0.78), roughness=0.7, metallic=0.0, static_friction=0.5, dynamic_friction=0.4,
                        tile_m=1.0)

# built-in textures (base color <name>.jpg + <name>_normal.png, drawn over the preset's tile_m)
TEXTURE_DIR = Path(__file__).resolve().parents[1] / "data" / "textures"
BUILTIN = "builtin:"
TEXTURES: dict[str, dict[str, Any]] = {
    name: dict(px=1024)
    for name in ("wood_light", "wood_dark", "tatami", "carpet_gray", "tile_white", "concrete", "epoxy_gray",
                 "raised_floor", "paint_white", "paint_beige", "drywall_gray", "brick", "metal_panel", "ceiling_board",
                 "ceiling_tile", "metal_deck")
}


def builtin_texture(ref: str) -> Path:
    """"builtin:wood_light" -> the bundled jpg, "builtin:wood_light_normal" -> its normal map."""
    name = ref.removeprefix(BUILTIN)
    return TEXTURE_DIR / (f"{name}.png" if name.endswith("_normal") else f"{name}.jpg")

# lighting presets: dome / sun (intensity, angle) and ceiling fixtures on a grid
LIGHTING: dict[str, dict[str, Any]] = {
    "studio": dict(dome=250.0, sun=600.0, note="even outdoor-like light, no fixtures (default)"),
    "daylight": dict(dome=350.0, sun=1000.0, sun_angle=(50.0, 0.0, 30.0), note="bright sky and low sun"),
    "home": dict(dome=150.0, fixture="sphere", spacing=2.5, size=0.15, intensity=15000.0, kelvin=3000.0, drop=0.3,
                 note="warm ceiling lamps"),
    "office": dict(dome=200.0, fixture="rect", spacing=2.4, size=(1.2, 0.3), intensity=8000.0, kelvin=5000.0, drop=0.02,
                   note="ceiling panel lights"),
    "warehouse": dict(dome=150.0, fixture="disk", spacing=6.0, size=0.35, intensity=90000.0, kelvin=5000.0, drop=0.5,
                      note="high-bay lights"),
    "night": dict(dome=10.0, fixture="sphere", spacing=3.0, size=0.15, intensity=10000.0, kelvin=2700.0, drop=0.3,
                  note="dark outside, a few warm lamps"),
    # outdoor: a sky image on the dome (SKIES) + the sun; street_night adds lamps on poles (pole = height)
    "sun": dict(sky="clear", dome=800.0, sun=2000.0, sun_angle=(55.0, 0.0, 40.0), note="clear day, high sun (outdoor)"),
    "overcast": dict(sky="overcast", dome=1500.0, note="cloudy, no shadows (outdoor)"),
    "dusk": dict(sky="evening", dome=800.0, sun=800.0, sun_angle=(80.0, 0.0, 120.0), kelvin_sun=3500.0,
                 note="low warm sun (outdoor)"),
    "street_night": dict(sky="night", dome=60.0, fixture="sphere", spacing=12.0, size=0.25, intensity=60000.0,
                         kelvin=3500.0, pole=6.0, note="night with lamps on 6 m poles every 12 m (outdoor)"),
}
OUTDOOR_LIGHTING = ("sun", "overcast", "dusk", "street_night")

# sky images (lat-long HDR) under the Isaac Sim assets root (ISAAC_ASSETS_ROOT, default the public S3)
SKY_ROOT = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.1"
SKIES: dict[str, str] = {
    "clear": "/NVIDIA/Assets/Skies/Clear/noon_grass_4k.hdr",
    "partly_cloudy": "/NVIDIA/Assets/Skies/Cloudy/kloofendal_48d_partly_cloudy_4k.hdr",
    "overcast": "/NVIDIA/Assets/Skies/Cloudy/abandoned_parking_4k.hdr",
    "evening": "/NVIDIA/Assets/Skies/Evening/evening_road_01_4k.hdr",
    "night": "/NVIDIA/Assets/Skies/Night/moonlit_golf_4k.hdr",
}


def sky_url(sky: str | None) -> str | None:
    """A SKIES name -> URL under ISAAC_ASSETS_ROOT (or the public S3); a path or URL is kept; "none" -> None."""
    import os

    if not sky or sky == "none":
        return None
    if sky in SKIES:
        root = os.environ.get("ISAAC_ASSETS_ROOT") or SKY_ROOT
        return root.rstrip("/") + SKIES[sky]
    return sky


def is_words(v: Any) -> bool:
    """A string that is not a preset name: words to search the materials with (set_room / create_scene)."""
    return isinstance(v, str) and v not in SURFACES


def check_surface(v: Any, default: str) -> None:
    """Raise on a bad surface object; words are left for the material search (service resolves them)."""
    if not is_words(v):
        resolve_surface(v, default)


def resolve_surface(s: str | dict | Surface | None, default: str) -> dict[str, Any]:
    """Flat dict with color/roughness/metallic/frictions/texture/normal/tile_m/mdl/mdl_name/name."""
    if s is None:
        s = default
    if isinstance(s, dict):
        s = Surface.model_validate(s)
    if isinstance(s, str):
        s = Surface(preset=s)
    name = s.preset or "custom"
    if s.preset and s.preset not in SURFACES:
        raise ValueError(f"unknown surface preset '{s.preset}'; choose from {sorted(SURFACES)}")
    out = dict(SURFACE_DEFAULTS)
    out.update({k: v for k, v in SURFACES.get(s.preset or "", {}).items() if k != "note"})
    given = {k: v for k, v in s.model_dump().items() if v is not None and k != "preset"}
    if s.preset in TEXTURES and "color" not in given and "texture" not in given:
        out["texture"] = BUILTIN + s.preset
        out["normal"] = BUILTIN + s.preset + "_normal"
    out.update(given)
    out["name"] = name
    return out


# the ceiling `True` gives, by Room.place (shops other than a home center have a system ceiling)
CEILING_BY_PLACE = {"home": "ceiling_board", "cafe": "ceiling_board", "restaurant": "ceiling_board", "bakery": "ceiling_board",
                    "variety_store": "ceiling_board", "florist": "ceiling_board", "greengrocer": "ceiling_board",
                    "wagashi_shop": "ceiling_board", "record_store": "ceiling_board", "asian_grocery": "ceiling_board",
                    "garden_center": "metal_deck",
                    "warehouse": "metal_deck", "factory": "metal_deck", "home_center": "metal_deck",
                    "plant_factory": "metal_panel"}  # a clean room's insulated panels


def ceiling_surface(ceiling: bool | str | dict | Surface | None, lighting: str,
                    place: str | None = None) -> dict[str, Any] | None:
    """The ceiling's flat surface, or None without a ceiling. `True` picks by place (CEILING_BY_PLACE, other
    indoor places get the office grid ceiling), or without a place by lighting: the grid ceiling for office
    fixtures, the steel deck for warehouse ones, the plain white board otherwise."""
    if not ceiling:
        return None
    if ceiling is True:
        if place and place not in ("outdoor", "other"):
            ceiling = CEILING_BY_PLACE.get(place, "ceiling_tile")
        else:
            ceiling = {"office": "ceiling_tile", "warehouse": "metal_deck"}.get(lighting, "ceiling_board")
    return resolve_surface(ceiling, "ceiling_board")


def check_lighting(name: str) -> str:
    if name not in LIGHTING:
        raise ValueError(f"unknown lighting preset '{name}'; choose from {sorted(LIGHTING)}")
    return name


def fixture_grid(width: float, depth: float, spacing: float) -> list[tuple[float, float]]:
    """Evenly spread points: n = ceil(len / spacing) per axis, centered in equal cells."""
    import math

    nx, ny = max(1, math.ceil(width / spacing - 1e-9)), max(1, math.ceil(depth / spacing - 1e-9))
    return [((i + 0.5) * width / nx, (j + 0.5) * depth / ny) for j in range(ny) for i in range(nx)]


def linear_from_hex(h: str) -> tuple[float, float, float]:
    """sRGB hex ('#a8d8c8' or 'a8d8c8', also 3 digits) -> linear RGB 0..1 (the numbers of a picked color are
    sRGB; written as they are into a linear color they come out darker and more saturated)."""
    s = h.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ValueError(f"not an sRGB hex color: {h!r} (use '#rrggbb', or 3 linear numbers)")

    def dec(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return tuple(round(dec(int(s[i:i + 2], 16) / 255), 4) for i in (0, 2, 4))  # type: ignore[return-value]


def hex_color(rgb: tuple[float, float, float]) -> str:
    """Linear RGB -> sRGB hex for previews."""

    def enc(c: float) -> int:
        c = min(max(c, 0.0), 1.0)
        c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        return round(c * 255)

    return "#" + "".join(f"{enc(c):02x}" for c in rgb)
