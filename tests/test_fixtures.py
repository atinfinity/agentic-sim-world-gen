"""Ceiling fixtures with a body, beams / soffits and posters hung on a wall: offline."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene.fixtures import HEADROOM, beams, ceiling_at, ceiling_glow, ceiling_tint, density, kelvin_tint, plan
from agentic_sim_world_gen.scene.spec import Room
from tests.test_layout import rec

CATALOG = {
    "shelf": [rec("shelf", "shelf", 1.2, 0.45, 2.2)],
    "poster": [rec("poster", "poster", 0.55, 0.014, 0.76)],
    "table": [rec("table", "table", 1.0, 0.6, 0.72)],
}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def room(**kw):
    return Room.model_validate({"width": 6, "depth": 4, "height": 2.7, "ceiling": True, "lighting": "office", **kw})


def test_kinds_keep_the_light_per_floor_area():
    for kind in ("downlight", "pendant", "line", "panel"):
        r = room(fixtures=kind)
        fx = plan(r)
        assert fx and all(f["kind"] == kind for f in fx)
        # like the preset's lights: each fixture gives `density` per spacing^2 of floor whatever its size
        from agentic_sim_world_gen.scene.fixtures import _area, KINDS

        k = KINDS[kind]
        area = _area(kind, k["size"] if kind != "line" else (min(k["size"][0], k["spacing"] - 0.2), k["size"][1]))
        per_m2 = fx[0]["light"]["intensity"] * area / k["spacing"] ** 2
        assert per_m2 == pytest.approx(density(r), rel=1e-3), kind
    assert room(fixtures="downlight").fixtures.kind == "downlight"
    assert room(fixtures=False).fixtures is None


@pytest.mark.no_auto_fix
def test_pendant_keeps_headroom_and_is_an_obstacle(tmp_path):
    fx = plan(room(fixtures="pendant"))
    assert all(f["lowest"] >= HEADROOM - 1e-6 and not f["flush"] for f in fx)
    assert all(f["flush"] for f in plan(room(fixtures="downlight")))
    # a tall shelf right under a pendant runs into it
    x, y = fx[0]["x"], fx[0]["y"]
    st = service.create_scene({"name": "p", "room": {"width": 6, "depth": 4, "height": 2.7, "ceiling": True,
                                                     "fixtures": "pendant"},
                               "objects": [{"id": "shelf", "query": "shelf", "position": [x, y]}]}, tmp_path / "p")
    assert "hits_fixture" in {i["kind"] for i in st["issues"]}


def test_beams_and_soffit():
    r = room(ceiling_features=[{"kind": "beam", "axis": "y", "spacing": 2.0, "width": 0.3, "drop": 0.4},
                               {"kind": "soffit", "wall": "north", "width": 0.6, "drop": 0.3}])
    bs = {b["id"]: b for b in beams(r)}
    assert sorted(bs) == ["beam0_0", "beam0_1", "soffit1"]
    # two beams centered in the 6 m room, running along y
    xs = sorted((b["rect"][0] + b["rect"][2]) / 2 for k, b in bs.items() if k.startswith("beam"))
    assert xs == pytest.approx([2.0, 4.0])
    assert bs["beam0_0"]["rect"][1] == 0 and bs["beam0_0"]["rect"][3] == 4
    assert bs["soffit1"]["rect"] == pytest.approx((0, 3.4, 6, 4)) and bs["soffit1"]["z"] == pytest.approx((2.4, 2.7))
    assert ceiling_at(r, 2.0, 1.0) == pytest.approx(2.3)
    assert ceiling_at(r, 1.0, 1.0) == pytest.approx(2.7)
    with pytest.raises(ValueError):
        room(ceiling_features=[{"kind": "soffit"}])
    with pytest.raises(ValueError):
        room(ceiling_features=[{"kind": "beam", "axis": "x"}])


def test_tall_object_under_a_beam(tmp_path):
    st = service.create_scene({"name": "b", "room": {"width": 6, "depth": 4, "height": 2.4, "ceiling": True,
                                                     "ceiling_features": [{"kind": "beam", "axis": "y", "at": 3.0}]},
                               "objects": [{"id": "shelf", "query": "shelf", "position": [3.0, 2.0]},
                                           {"id": "table", "query": "table", "position": [1.0, 2.0]}]}, tmp_path / "b")
    kinds = [(i["kind"], i["objects"]) for i in st["issues"]]
    assert ("hits_beam", ["shelf"]) in kinds and not any("table" in o for _, o in kinds)


def test_poster_on_the_wall_above_the_wainscot(tmp_path):
    st = service.create_scene({"name": "w", "room": {"width": 4, "depth": 3, "height": 2.6, "wainscot": True},
                               "objects": [{"id": "poster", "query": "poster",
                                            "layout": {"against_wall": "north", "wall_height": 1.2}}]}, tmp_path / "w")
    o = next(o for o in st["objects"] if o["id"] == "poster")
    assert o["z"] == pytest.approx(1.2)
    # hung above the 0.9 m wainscot: flat on the wall, not pushed out by it
    assert 3.0 - o["position"][1] == pytest.approx(0.007 + 0.005, abs=2e-3)
    assert st["issues"] == []
    from agentic_sim_world_gen.scene.spec import Layout

    with pytest.raises(ValueError):
        Layout(on="table", wall_height=1.0)


def test_set_room_and_usd(tmp_path):
    pxr = pytest.importorskip("pxr")
    d = service.create_scene({"name": "u", "room": {"width": 6, "depth": 4, "height": 2.7, "ceiling": True},
                              "objects": []}, tmp_path / "u")["scene_dir"]
    st = service.set_room(d, fixtures={"kind": "line", "color": "#202020"},
                          ceiling_features=[{"kind": "beam", "axis": "x", "at": 2.0, "color": "#c06020"}])
    assert st["issues"] == []
    import json

    hist = json.loads((tmp_path / "u" / "preview.json").read_text(encoding="utf-8"))["history"]
    changes = [c for e in hist for c in e.get("changes") or []]
    assert "照明器具：なし → ライン照明 2 m 間隔・#202020" in changes and "梁・下がり壁：なし → 梁 1" in changes
    service.compose_scene(d)
    stage = pxr.Usd.Stage.Open(str(tmp_path / "u" / "scene.usda"))
    lights = [p for p in stage.GetPrimAtPath("/World/Lights").GetChildren() if p.GetName().startswith("Fixture_")]
    assert lights and all(p.GetTypeName() == "RectLight" for p in lights)
    bodies = stage.GetPrimAtPath("/World/Room/Fixtures").GetChildren()
    assert len([b for b in bodies if b.GetName().startswith("Fixture_")]) == len(lights)
    beam = stage.GetPrimAtPath("/World/Room/CeilingFeatures/beam0")
    assert beam and beam.GetCustomDataByKey("agentic_sim_world_gen:ceiling_feature")
    # back to the preset's lights
    service.set_room(d, fixtures=False, ceiling_features=[])
    r = service.load(d).spec.room
    assert r.fixtures is None and r.ceiling_features == []


def test_uplight_only_on_hanging_fixtures():
    with pytest.raises(ValueError):
        room(fixtures={"kind": "downlight", "uplight": 0.3})
    fx = plan(room(fixtures={"kind": "line", "drop": 0.3, "uplight": 0.25}))
    L = fx[0]["light"]
    assert L["up"]["intensity"] == pytest.approx(L["intensity"] * 0.25, rel=1e-3)
    assert L["up"]["z"] > L["z"] and L["up"]["z"] < 2.7 - 0.3 + 0.01  # on top of the housing, under the ceiling
    p = plan(room(fixtures={"kind": "pendant", "uplight": 0.1}))[0]["light"]
    assert p["up"]["z"] > p["z"]
    assert "up" not in plan(room(fixtures="pendant"))[0]["light"]
    pxr = pytest.importorskip("pxr")
    from agentic_sim_world_gen.usd.look import build_fixtures

    st = pxr.Usd.Stage.CreateInMemory()
    paths = build_fixtures(st, room(fixtures={"kind": "line", "drop": 0.3, "uplight": 0.25}))
    up = st.GetPrimAtPath(next(p for p in paths if p.endswith("_Up")))
    rot = pxr.UsdGeom.XformCommonAPI(up).GetXformVectors(pxr.Usd.TimeCode.Default())[1]
    assert rot[0] == pytest.approx(180.0)


def test_ceiling_glows_under_downward_fixtures(tmp_path):
    """The ceiling's material glows a little under fixtures that only shine down (the floor's bounce)."""
    g = ceiling_glow(room(fixtures="downlight"))
    assert g > 0
    assert ceiling_glow(room(fixtures={"kind": "downlight", "ceiling_fill": 0.6})) == pytest.approx(2 * g, rel=1e-2)
    assert ceiling_glow(room(fixtures="downlight", lighting_scale=0.5)) == pytest.approx(g / 2, rel=1e-2)
    assert ceiling_glow(room(fixtures={"kind": "downlight", "ceiling_fill": 0})) == 0
    assert ceiling_glow(room(fixtures="downlight", ceiling=False)) == 0
    assert ceiling_glow(room(fixtures={"kind": "pendant", "uplight": 0.2})) == 0
    assert ceiling_glow(room()) == 0  # no fixtures: the lighting preset as before
    warm, white = kelvin_tint(2700), kelvin_tint(6600)
    assert warm[0] > warm[1] > warm[2] and white == pytest.approx((1, 1, 1), abs=0.05)
    pxr = pytest.importorskip("pxr")
    d = service.create_scene({"name": "glow", "room": {"width": 6, "depth": 4, "height": 2.7, "ceiling": True,
                                                       "lighting": "office", "fixtures": "downlight"},
                              "objects": []}, tmp_path / "g")["scene_dir"]
    service.compose_scene(d)
    st = pxr.Usd.Stage.Open(str(tmp_path / "g" / "scene.usda"))
    mat = st.GetPrimAtPath("/World/Room/Looks/Ceiling")
    assert mat.GetCustomDataByKey("agentic_sim_world_gen:glow") == pytest.approx(g)
    ps = pxr.UsdShade.Shader(st.GetPrimAtPath("/World/Room/Looks/Ceiling/PreviewSurface"))
    src = ps.GetInput("emissiveColor").GetConnectedSources()[0][0]
    tex = pxr.UsdShade.Shader(src.source.GetPrim())
    tint = ceiling_tint(service.load(d).spec.room)
    assert tex.GetInput("scale").Get()[0] == pytest.approx(g * tint[0]) and tex.GetInput("file").Get() is not None
    assert not st.GetPrimAtPath("/World/Room/Looks/Floor/PreviewSurface").GetAttribute("inputs:emissiveColor")
