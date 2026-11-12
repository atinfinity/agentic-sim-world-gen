"""Bundled MDL material index (agentic_sim_world_gen.materials) and floor / walls chosen by words (set_room)."""

from __future__ import annotations

import gzip
import json

import pytest

from agentic_sim_world_gen import materials, service

M = "/NVIDIA/Materials"


def _rec(id_, category, color, title=None, keywords=(), name=None, library=None):
    lib = library or id_.split("/")[0]
    stem = id_.split("/")[-1].split(":")[0]
    name = name or id_.split(":")[-1].split("/")[-1]
    mdl = f"{M}/{'/'.join(id_.split(':')[0].split('/')[:-1])}/{stem}.mdl"
    thumb = mdl.rsplit("/", 1)[0] + f"/.thumbs/256x256/{stem}.mdl" + ("" if lib == "Base" else f"@{name}") + ".png"
    r = {"id": id_, "library": lib, "category": category, "mdl": mdl, "name": name, "thumb": thumb, "color": color,
         "license": "NVIDIA-Omniverse-Materials" if lib == "Base" else "NVIDIA-vMaterials", "keywords": list(keywords)}
    if title:
        r["title"] = title
    return r


RECS = [
    _rec("Base/Wood/Walnut", "Wood", "#4a3020"),
    _rec("Base/Wood/Oak", "Wood", "#d0b082"),
    _rec("vMaterials_2/Ceramic/Tiles_Square:Tiles_Square_White", "Ceramic", "#e4e4e4",
         "Glazed Square Tiles - White", ["tiles", "ceramic", "glazed", "white"]),
    _rec("vMaterials_2/Ceramic/Tiles_Square:Tiles_Square_White_Matte", "Ceramic", "#e0e0e0",
         "Glazed Square Tiles - White Matte", ["tiles", "ceramic", "glazed", "white", "matte"]),
    _rec("vMaterials_2/Ceramic/Tiles_Square:Tiles_Square_White_Gloss", "Ceramic", "#e6e6e6",
         "Glazed Square Tiles - White Gloss", ["tiles", "ceramic", "glazed", "white"]),
    _rec("vMaterials_2/Ceramic/Tiles_Mosaic:Tiles_Mosaic_White", "Ceramic", "#dcdcdc",
         "Glazed Mosaic Tiles - White", ["tiles", "mosaic", "ceramic", "white"]),
    _rec("Base/Masonry/Brick_Wall_Red", "Masonry", "#bb7957"),
    _rec("Base/Carpet/Carpet_Gray", "Carpet", "#9b9b9a"),
    _rec("vMaterials_2/Paint/Paint_Eggshell:Paint_Eggshell_White", "Paint", "#cfcac4",
         "Eggshell Paint - White", ["paint", "white", "eggshell"]),
]


@pytest.fixture(autouse=True)
def fake_index(tmp_path, monkeypatch):
    p = tmp_path / "mdl.jsonl.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r) for r in RECS) + "\n")
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_MDL_INDEX", str(p))
    monkeypatch.delenv("ISAAC_ASSETS_ROOT", raising=False)
    return p


def _ids(query, alt=None, target=None, **kw):
    return [r["id"] for r in materials.search(query, alt, target, **kw)]


def test_bundled_index_loads():
    import os

    os.environ.pop("AGENTIC_SIM_WORLD_GEN_MDL_INDEX")
    recs = materials.records()
    assert len(recs) > 2000 and {"Base", "vMaterials_2"} == {r["library"] for r in recs}
    assert all(r["mdl"].startswith(M + "/") and r["thumb"].endswith(".png") for r in recs[:50])


def test_mdl_off(monkeypatch):
    """AGENTIC_SIM_WORLD_GEN_MDL=0: no bundled MDL material is searched (floors and walls from CC0 PBR or plain colors only)."""
    assert materials.records()
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_MDL", "0")
    assert materials.records() == ()
    assert materials.search("light wood floor", target="floor") == []
    assert materials.records(materials.index_path())  # an explicit index is still read


def test_color_words():
    assert materials.color_words("#d0b082").get("light", 0) > 0
    assert materials.color_words("#4a3020").get("dark", 0) > 0
    assert materials.color_words("#e4e4e4").get("white", 0) > 0
    assert "red" in materials.color_words("#bb3020")


def test_named_hue_beats_thumbnail():
    """An olive thumbnail reads as beige, but a material named "Lime Green" is not beige."""
    lime = {"name": "Ceramic_Tiles_Diamond_Lime_Green", "title": "Glazed Diamond Tiles - Lime Green",
            "category": "vMaterials_2/Ceramic", "color": "#6d7336", "keywords": ["tiles", "ceramic", "floor"]}
    plain = {**lime, "name": "Ceramic_Tiles_Diamond", "title": "Glazed Diamond Tiles"}
    assert "beige" in materials.color_words("#6d7336")
    assert "beige (color)" not in materials.score(lime, "beige ceramic tile")["matched"]
    assert "beige (color)" in materials.score(plain, "beige ceramic tile")["matched"]
    assert "light (color)" in materials.score({**lime, "color": "#b8be95"}, "light ceramic tile")["matched"]


def test_search_light_wood_floor():
    ids = _ids("light wood", ["pale oak planks"], "floor")
    assert ids[0] == "Base/Wood/Oak" and "Base/Wood/Walnut" in ids
    assert _ids("dark wood", target="floor")[0] == "Base/Wood/Walnut"


def test_search_white_tile_walls_keeps_variety():
    res = materials.search("white tile", ["white ceramic tiles"], "walls")
    assert all(r["category"] == "Ceramic" for r in res[:4]) and not res[0]["match"]["weak"]
    # two looks of one file at most, then the other file before the third variant
    assert [r["mdl"].rsplit("/", 1)[1] for r in res[:4]] == ["Tiles_Square.mdl", "Tiles_Square.mdl",
                                                             "Tiles_Mosaic.mdl", "Tiles_Square.mdl"]


def test_search_off_use_and_missing():
    # paint is for walls: on the floor it ranks below a floor material of the same color
    assert _ids("white", target="floor")[0] != "vMaterials_2/Paint/Paint_Eggshell:Paint_Eggshell_White"
    res = materials.search("tatami", ["woven straw mat"], "floor")
    assert all(r["match"]["weak"] for r in res)


def test_surface_and_lookup():
    r = materials.by_id("Base/Wood/Oak")
    s = materials.surface(r)
    assert s["mdl"] == materials.S3_ROOT + f"{M}/Base/Wood/Oak.mdl" and s["mdl_name"] == "Oak"
    assert s["static_friction"] == materials.CATEGORY["Wood"]["static_friction"]
    assert all(0 <= c <= 1 for c in s["color"]) and s["color"][0] > s["color"][2]  # linear, warm
    assert materials.by_mdl(s["mdl"], "Oak") is r
    assert materials.by_mdl("/elsewhere/Oak.mdl") is None


def _scene(tmp_path):
    d = tmp_path / "s"
    service.create_scene({"name": "m", "room": {"width": 4, "depth": 3}, "objects": []}, d)
    return d


def test_set_room_words(tmp_path):
    d = _scene(tmp_path)
    st = service.set_room(d, floor="light wood", floor_alt_queries=["pale oak planks"], walls="white tile", width=5)
    assert st["room"]["width"] == 5
    assert st["room"]["floor"]["mdl"].endswith("/Base/Wood/Oak.mdl")
    assert st["room"]["walls"]["mdl_name"] == "Tiles_Square_White"
    c = st["surface_candidates"]
    assert c["floor"]["candidates"][0]["id"] == "Base/Wood/Oak" and c["floor"]["selected"] == 0
    assert json.loads((d / "surfaces.json").read_text())["walls"]["query"] == "white tile"

    st = service.select_surface(d, "floor", 1)
    assert st["room"]["floor"]["mdl"].endswith("/Base/Wood/Walnut.mdl")
    assert json.loads((d / "surfaces.json").read_text())["floor"]["selected"] == 1
    with pytest.raises(IndexError):
        service.select_surface(d, "floor", 99)

    st = service.set_room(d, walls="mdl:Base/Masonry/Brick_Wall_Red")
    assert st["room"]["walls"]["mdl_name"] == "Brick_Wall_Red"
    st = service.set_room(d, floor="tatami")  # a preset is still a preset
    assert st["room"]["floor"] == "tatami"
    st = service.set_room(d, walls="zebra stripes")  # nothing: the walls stay
    assert st["room"]["walls"]["mdl_name"] == "Brick_Wall_Red" and st["surface_candidates"]["walls"]["candidates"] == []


def test_surface_thumbnails(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    r = materials.by_id("Base/Wood/Oak")
    (root / r["thumb"].lstrip("/")).parent.mkdir(parents=True)
    (root / r["thumb"].lstrip("/")).write_bytes(b"\x89PNG fake")
    monkeypatch.setenv("ISAAC_ASSETS_ROOT", str(root))
    d = _scene(tmp_path)
    service.set_room(d, floor="wood")
    out = service.surface_thumbnails(d, "floor")
    assert {s["id"]: img for s, img in out}["Base/Wood/Oak"] == b"\x89PNG fake"
    with pytest.raises(KeyError):
        service.surface_thumbnails(d, "walls")


def test_license_report_lists_materials(tmp_path):
    d = _scene(tmp_path)
    service.set_room(d, floor="light wood", walls="white tile")
    r = service.license_report(d)
    by = {m["surface"]: m for m in r["materials"]}
    assert by["floor"]["license"] == "NVIDIA-Omniverse-Materials" and by["walls"]["license"] == "NVIDIA-vMaterials"
    md = (d / "LICENSES.md").read_text()
    assert "## Materials (floor / walls / ceiling: MDL, CC0 PBR textures)" in md and "NVIDIA vMaterials license" in md
    service.set_room(d, walls={"mdl": "/my/own.mdl"})
    assert any("own.mdl" in w for w in service.license_report(d)["warnings"])


def test_tuning_patterned_wood_tiles():
    wt = materials.S3_ROOT + "/Materials/vMaterials_2/Wood/Wood_Tiles_{}.mdl"
    ash = materials.tuning(wt.format("Ash"), "Wood_Tiles_Ash_Herringbone")
    assert ash == {"tiles_base_roughness": 0.3, "wood_brightness_variation": 0.4, "texture_scale": [1.6, 1.6]}
    oak = materials.tuning(wt.format("Oak_Mountain"), "Wood_Tiles_Oak_Mountain_Herringbone")
    assert oak == {"tiles_base_roughness": 0.3, "texture_scale": [1.6, 1.6]}
    # the plain ash material (no pattern suffix) keeps its brightness and scale; a query string is ignored
    assert materials.tuning(wt.format("Ash") + "?x=1") == {"tiles_base_roughness": 0.3}
    assert materials.tuning(wt.format("Ash"), "Wood_Tiles_Ash_Versailles")["wood_brightness_variation"] == 0.4
    assert materials.tuning(f"{M}/Base/Wood/Oak.mdl", "Oak") == {} and materials.tuning(None) == {}


def test_ceiling_by_words(tmp_path):
    d = _scene(tmp_path)
    st = service.set_room(d, ceiling="white paint")  # wall finishes suit a ceiling
    assert st["room"]["ceiling"]["mdl_name"] == "Paint_Eggshell_White"
    assert json.loads((d / "surfaces.json").read_text())["ceiling"]["query"] == "white paint"
    by = {m["surface"]: m for m in service.license_report(d)["materials"]}
    assert "ceiling" in by
    service.select_surface(d, "ceiling", 0)


def test_floor_area_words(tmp_path):
    """A floor area's floor by words: searched as target floor:<area id>, select_surface picks another."""
    d = _scene(tmp_path)
    st = service.set_room(d, floor_areas=[{"id": "ent", "rect": [0, 0, 1, 1], "floor": "light wood"}],
                          zone_floor_alt_queries={"ent": ["pale oak planks"]})
    area = st["room"]["floor_areas"][0]
    assert area["floor"]["mdl"].endswith("/Base/Wood/Oak.mdl")
    assert "floor:ent" in st["surface_candidates"]
    st = service.select_surface(d, "floor:ent", 1)
    assert st["room"]["floor_areas"][0]["floor"]["mdl"].endswith("/Base/Wood/Walnut.mdl")
    assert not st["room"].get("zone_floors")
    st = service.set_room(d, floor_areas=[{"id": "ent", "rect": [0, 0, 1, 1], "floor": "zebra stripes"}])
    assert st["room"]["floor_areas"][0]["floor"] is not None  # nothing matched: a floor stays


def test_create_scene_takes_words(tmp_path):
    """create_scene searches words for the floor / walls / ceiling like set_room."""
    d = tmp_path / "w"
    st = service.create_scene({"name": "w", "room": {"width": 4, "depth": 3, "floor": "light wood",
                                                       "walls": {"*": "paint_white", "N": "white tile"},
                                                       "ceiling": "zebra stripes"}, "objects": []}, d)
    assert st["room"]["floor"]["mdl"].endswith("/Base/Wood/Oak.mdl")
    assert st["room"]["walls"]["*"] == "paint_white" and st["room"]["walls"]["N"]["mdl_name"] == "Tiles_Square_White"
    assert st["room"]["ceiling"] is True  # nothing matched: still a ceiling, by place
    c = st["surface_candidates"]
    assert set(c) == {"floor", "walls:N", "ceiling"} and c["ceiling"]["candidates"] == []
    assert json.loads((d / "surfaces.json").read_text())["floor"]["query"] == "light wood"
    st = service.create_scene({"name": "p", "room": {"width": 4, "depth": 3, "floor": "tatami"}, "objects": []},
                              tmp_path / "p")
    assert st["room"]["floor"] == "tatami" and "surface_candidates" not in st
    with pytest.raises(ValueError):  # a broken surface object still fails
        service.create_scene({"name": "x", "room": {"width": 4, "depth": 3, "floor": {"preset": "nope"}},
                              "objects": []}, tmp_path / "x")
