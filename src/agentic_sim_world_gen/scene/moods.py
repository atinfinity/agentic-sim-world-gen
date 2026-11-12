"""Looks to pick from sample pictures: each mood is a set of floor, walls, trim, light color and
furniture colors (the same shape as a reference image's taste). The sample pictures are a standard room
of each place group drawn once in Isaac Sim (scripts/make_mood_images.py) and kept in data/moods/, so showing
them costs nothing at run time. Picking one applies the room part to the scene; the furniture words go to the
agent, who looks for matching assets."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_sim_world_gen.scene.spec import STORE_PLACES, Taste

IMAGES = Path(__file__).resolve().parent.parent / "data" / "moods"


@dataclass(frozen=True)
class Mood:
    name: str
    label: str  # Japanese, shown on the card
    note: str  # one line for the user (Japanese)
    floor: str
    walls: str | dict[str, str]
    baseboard: str | None  # trim surface, None = no baseboard
    wainscot: str | None  # wainscot surface, None = none
    ceiling: str | None  # None = by place (when the room has a ceiling)
    kelvin: float
    scale: float
    furniture: dict[str, str] = field(default_factory=dict)  # kind -> color / material words (English)
    style: tuple[str, ...] = ()

    def room(self, has_ceiling: bool) -> dict[str, Any]:
        """The set_room changes. Trim the mood has not is removed, so moods can be switched back and forth."""
        out: dict[str, Any] = {"floor": self.floor, "walls": self.walls, "light_kelvin": self.kelvin,
                               "lighting_scale": self.scale,
                               "baseboard": {"surface": self.baseboard} if self.baseboard else False,
                               "wainscot": {"surface": self.wainscot} if self.wainscot else False}
        if has_ceiling:
            out["ceiling"] = self.ceiling or True
        return out

    def taste(self) -> Taste:
        walls = self.walls if isinstance(self.walls, str) else ", ".join(f"{k}: {v}" for k, v in self.walls.items())
        return Taste(floor=self.floor, walls=walls, light_kelvin=self.kelvin, lighting_scale=self.scale,
                     furniture=dict(self.furniture), style=list(self.style), note=self.note)


MOODS: dict[str, Mood] = {m.name: m for m in (
    Mood("natural", "木目ナチュラル", "明るい木の床と白い壁、暖かい光、木の家具",
         floor="wood_light", walls="paint_white", baseboard="trim_wood", wainscot=None, ceiling=None,
         kelvin=3000, scale=1.0,
         furniture={"table": "light oak wood", "desk": "light oak wood", "shelf": "light wood", "chair": "wooden",
                    "sofa": "beige fabric", "cabinet": "light wood"},
         style=("natural", "scandinavian")),
    Mood("white_modern", "白モダン", "白い床と壁、白い光で明るく、白・グレーの家具",
         floor="tile_white", walls="paint_white", baseboard="trim_white", wainscot=None, ceiling=None,
         kelvin=5000, scale=1.15,
         furniture={"table": "white", "desk": "white", "shelf": "white", "chair": "white", "sofa": "light gray fabric",
                    "cabinet": "white"},
         style=("modern", "minimal")),
    Mood("industrial", "工業系", "コンクリートの床とレンガの壁、天井は折板、黒い金属の家具",
         floor="concrete", walls={"*": "drywall_gray", "N": "brick"}, baseboard=None, wainscot=None,
         ceiling="metal_deck", kelvin=3500, scale=0.95,
         furniture={"table": "black metal", "desk": "black metal", "shelf": "black metal", "chair": "black metal",
                    "sofa": "brown leather", "cabinet": "metal"},
         style=("industrial", "loft")),
    Mood("calm_dark", "落ち着いた濃い木", "濃い木の床と腰壁、ベージュの壁、暗めの暖かい光",
         floor="wood_dark", walls="paint_beige", baseboard="trim_wood", wainscot="wood_dark", ceiling=None,
         kelvin=2700, scale=0.85,
         furniture={"table": "dark walnut wood", "desk": "dark walnut wood", "shelf": "dark wood",
                    "chair": "dark wood", "sofa": "dark brown leather", "cabinet": "dark wood"},
         style=("classic", "cozy")),
)}

# which moods each place group offers, in the order shown (the first is the usual one)
GROUPS: dict[str, tuple[str, ...]] = {
    "home": ("natural", "white_modern", "industrial", "calm_dark"),
    "office": ("white_modern", "natural", "industrial"),
    "shop": ("white_modern", "natural", "industrial"),
    "cafe": ("natural", "industrial", "calm_dark", "white_modern"),
}


def group(place: str | None) -> str | None:
    """The place group with sample pictures, or None (warehouses, factories, outdoor ... have none)."""
    if place in (None, "home"):
        return "home"
    if place == "office":
        return "office"
    if place == "shop" or place in STORE_PLACES:
        return "shop"
    if place in ("cafe", "restaurant"):
        return "cafe"
    return None


def for_place(place: str | None) -> list[Mood]:
    g = group(place)
    return [MOODS[n] for n in GROUPS[g]] if g else []


def get(name: str, place: str | None) -> Mood:
    offered = for_place(place)
    for m in offered:
        if m.name == name:
            return m
    if not offered:
        raise ValueError(f"no moods for place '{place}' (moods are for home, office, shops and cafes)")
    raise ValueError(f"unknown mood '{name}' for {group(place)}; one of {', '.join(m.name for m in offered)}")


def image(place: str | None, name: str) -> Path | None:
    """The sample picture of the mood for the place group, when it has been drawn."""
    g = group(place)
    f = IMAGES / f"{g}_{name}.jpg"
    return f if g and f.is_file() else None


def furniture_words(mood: Mood, kind: str) -> str | None:
    """The color / material words for a kind of furniture (the query's last noun), or None."""
    k = kind.lower().split()[-1] if kind else ""
    aliases = {"bookshelf": "shelf", "bookcase": "shelf", "shelving": "shelf", "rack": "shelf", "stool": "chair",
               "bench": "chair", "couch": "sofa", "armchair": "sofa", "counter": "cabinet", "dresser": "cabinet",
               "wardrobe": "cabinet", "sideboard": "cabinet", "stand": "cabinet"}
    return mood.furniture.get(k) or mood.furniture.get(aliases.get(k, ""))
