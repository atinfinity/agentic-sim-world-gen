"""Window assets stretched to their opening (fit_scale) and curtains hung over a window (covers). No network."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene.spec import ObjectSpec

from test_layout import CATALOG, fake, objs, rec  # noqa: F401  (fixture: fake asset search)

# windows and curtains: front -Y, width along x, origin at the bottom center (as local window assets)
CATALOG.update({
    "sliding window": [rec("sliding_window_waist", "window", 1.69, 0.076, 1.17)],
    "small window": [rec("bathroom_window", "window", 0.64, 0.076, 0.57)],
    "curtain": [rec("curtain_waist_window", "curtain", 1.9, 0.12, 1.35)],
    "long curtain": [rec("curtain_floor_window", "curtain", 1.9, 0.12, 2.6)],
})

ROOM = {"width": 3.64, "depth": 2.73, "height": 2.4, "wall_thickness": 0.12,
        "openings": [{"id": "win", "kind": "window", "wall": "north", "offset": 1.82, "width": 1.6, "height": 1.1,
                      "sill": 0.9}]}


def scene(tmp_path, objects):
    return service.create_scene({"name": "w", "room": ROOM, "objects": objects}, tmp_path / "w")


def test_window_fits_its_opening(tmp_path):
    st = scene(tmp_path, [{"id": "w", "query": "sliding window", "opening": "win"}])
    o = objs(st)["w"]
    assert o["position"] == pytest.approx((1.82, 2.73 + 0.06))  # north wall centerline
    assert o["z"] == pytest.approx(0.9)
    sx, sy, sz = o["fit_scale"]
    assert sx == pytest.approx(1.6 / 1.69, abs=1e-3) and sy == 1.0 and sz == pytest.approx(1.1 / 1.17, abs=1e-3)
    assert st["issues"] == []


def test_window_far_from_its_opening_size(tmp_path):
    st = scene(tmp_path, [{"id": "w", "query": "small window", "opening": "win"}])
    assert [(i["kind"], i["objects"]) for i in st["issues"]] == [("opening_fit", ["w", "win"])]
    assert "x2.50" in st["issues"][0]["detail"]


def test_curtain_covers_the_window(tmp_path):
    st = scene(tmp_path, [
        {"id": "w", "query": "sliding window", "opening": "win"},
        {"id": "c", "query": "curtain", "covers": "win"},
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},  # under the window: no overlap
    ])
    o = objs(st)
    c = o["c"]
    face = 2.73  # inner face of the north wall
    assert c["position"][0] == pytest.approx(1.82)
    assert c["position"][1] == pytest.approx(face - 0.01 - 0.06)
    assert c["fit_scale"][0] == pytest.approx(1.8 / 1.9, abs=1e-3)
    assert c["yaw_deg"] == 0  # front -Y faces into the room
    assert c["z"] == pytest.approx(0.9 + 1.1 + 0.1 - 1.35)
    assert st["issues"] == []
    assert not [u for u in st.get("usability", []) if u["kind"] == "blocks_window"]


def test_curtain_too_long_and_bad_window(tmp_path):
    st = scene(tmp_path, [{"id": "c", "query": "long curtain", "covers": "win"},
                          {"id": "x", "query": "curtain", "covers": "nope"}])
    kinds = {(i["kind"], i["objects"][0]) for i in st["issues"]}
    assert ("opening_fit", "c") in kinds and ("bad_opening", "x") in kinds
    assert objs(st)["c"]["z"] == 0.0


def test_covers_excludes_layout():
    with pytest.raises(ValueError, match="covers places the object itself"):
        ObjectSpec(id="c", query="curtain", covers="win", layout={"against_wall": "north"})


def test_compose_writes_the_fit_scale(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    st = scene(tmp_path, [{"id": "w", "query": "sliding window", "opening": "win"}])
    service.compose_scene(tmp_path / "w")
    stage = Usd.Stage.Open(str(tmp_path / "w" / "scene.usda"))
    prim = stage.GetPrimAtPath("/World/Objects/w")
    ops = [op.GetOpName() for op in UsdGeom.Xformable(prim).GetOrderedXformOps()]
    assert ops[:3] == ["xformOp:translate", "xformOp:rotateZ", "xformOp:scale:fit"]
    assert tuple(prim.GetAttribute("xformOp:scale:fit").Get()) == pytest.approx(tuple(objs(st)["w"]["fit_scale"]))
    assert prim.GetCustomDataByKey("agentic_sim_world_gen:opening") == "win"  # check hides it with the wall
    assert list(stage.GetPrimAtPath("/World/Room/Wall_N").GetCustomDataByKey("agentic_sim_world_gen:openings")) == ["win"]


def test_fill_windows_by_shape(tmp_path):
    CATALOG.update({
        "floor-length sliding window": [rec("sliding_window_floor", "window", 1.69, 0.076, 2.03)],
        "small frosted window": CATALOG["small window"],
        "waist window curtain": CATALOG["curtain"],
        "floor-length curtain": [rec("curtain_floor_window", "curtain", 1.94, 0.12, 2.125)],
        "fixed window": [rec("fix_window", "window", 1.69, 0.07, 1.17)],
    })
    room = {**ROOM, "openings": [
        *ROOM["openings"],
        {"id": "patio", "kind": "window", "wall": "south", "offset": 1.82, "width": 1.6, "height": 2.0, "sill": 0.0},
        {"id": "wc", "kind": "window", "wall": "east", "offset": 1.0, "width": 0.6, "height": 0.6, "sill": 1.2},
        {"id": "door", "kind": "door", "wall": "west", "offset": 1.0, "width": 0.8},
    ]}
    service.create_scene({"name": "f", "room": room, "objects": []}, tmp_path / "f")
    st = service.fill_windows(tmp_path / "f")
    assert sorted(st["added"]) == ["curtain_patio", "curtain_win", "win_patio", "win_wc", "win_win"]
    o = objs(st)
    assert o["win_patio"]["query"] == "floor-length sliding window" and o["win_wc"]["query"] == "small frosted window"
    assert o["curtain_patio"]["query"] == "floor-length curtain" and o["curtain_patio"]["covers"] == "patio"
    # 2.125 m: longer than head + 0.1 m, so the rail rises (up to the ceiling) and the hem stays on the floor
    assert o["curtain_patio"]["z"] == pytest.approx(0.0) and st["issues"] == []
    assert service.fill_windows(tmp_path / "f")["added"] == []  # nothing left to fill
    service.create_scene({"name": "g", "room": {**room, "place": "office"}, "objects": []}, tmp_path / "g")
    st = service.fill_windows(tmp_path / "g")
    assert not [i for i in st["added"] if i.startswith("curtain")] and objs(st)["win_win"]["query"] == "fixed window"


def test_fill_windows_picks_a_window_of_the_opening_size(tmp_path):
    """The search words alone would take the small bathroom window for a 1.6 x 1.1 m opening."""
    CATALOG["sliding window"] = [rec("bathroom_window", "window", 0.64, 0.076, 0.57),
                                 rec("sliding_window_waist", "window", 1.69, 0.076, 1.17)]
    try:
        service.create_scene({"name": "s", "room": ROOM, "objects": []}, tmp_path / "s")
        st = service.fill_windows(tmp_path / "s", covers=None)
        w = objs(st)["win_win"]
        assert w["fit_scale"][0] == pytest.approx(1.6 / 1.69, abs=1e-3) and st["issues"] == []
    finally:
        CATALOG["sliding window"] = [rec("sliding_window_waist", "window", 1.69, 0.076, 1.17)]


def test_fill_windows_leaves_no_window_assets_out(tmp_path):
    """With no window / curtain in the sources, a car door tagged "window" and an acoustic fence are not
    put in the opening; the opening stays empty and not_found says why."""
    from dataclasses import replace

    door = replace(rec("car_door", "car door", 1.31, 0.34, 1.3), caption="Blue assembled rear car door",
                   tags=["window", "vehicle"])
    fence = replace(rec("fence", "fence", 1.9, 0.1, 1.4), caption="Paired padded acoustic barrier panels")
    keep = {k: CATALOG.get(k) for k in ("sliding window", "waist window curtain", "curtain")}
    CATALOG.update({"sliding window": [door], "waist window curtain": [fence], "curtain": [fence]})
    try:
        service.create_scene({"name": "s", "room": {**ROOM, "place": "home"}, "objects": []}, tmp_path / "s")
        st = service.fill_windows(tmp_path / "s")
        assert st["added"] == [] and "win_win" not in objs(st) and "curtain_win" not in objs(st)
        nf = {x["id"]: x for x in st["not_found"]}
        assert nf["win_win"]["nearest"] == "car_door" and nf["win_win"]["why"].startswith("made for automotive")
        assert nf["curtain_win"]["nearest"] == "fence" and nf["curtain_win"]["why"].startswith("weak match")
        assert "tell the user" in st["not_found_hint"]
    finally:
        for k, v in keep.items():
            if v is None:
                CATALOG.pop(k, None)
            else:
                CATALOG[k] = v


def test_shop_storefront_window_fits_under_the_ceiling(tmp_path):
    """A shop's wide opening asks for a shop window; a window whose top would be above the 2.7 m ceiling
    (3.1 m tall, or 2.6 m hung 0.4 m up) goes after one that fits."""
    CATALOG["shop window"] = [rec("glass_facade", "window", 2.4, 0.1, 3.1), rec("shop_fix_window", "window", 2.6, 0.07, 2.0)]
    CATALOG["storefront window"] = [rec("facade_mid", "window", 2.4, 0.1, 2.6), rec("shop_fix_window", "window", 2.6, 0.07, 2.0)]
    try:
        room = {**ROOM, "width": 6.0, "height": 2.7, "place": "shop", "openings": [
            {"id": "front", "kind": "window", "wall": "south", "offset": 3.0, "width": 2.4, "height": 1.9, "sill": 0.3}]}
        service.create_scene({"name": "s", "room": room, "objects": [
            {"id": "free", "query": "shop window", "layout": {"against_wall": "east"}},
            {"id": "floor", "query": "storefront window", "layout": {"against_wall": "west"}},
            {"id": "hung", "query": "storefront window", "layout": {"against_wall": "west", "along": 0.2,
                                                                   "wall_height": 0.4}}]}, tmp_path / "s")
        st = service.fill_windows(tmp_path / "s")
        assert objs(st)["win_front"]["query"] == "shop window"
        s = service.load(tmp_path / "s")
        by = {p.object_id: p for p in s.placements}
        assert by["win_front"].asset.name == "shop_fix_window"
        assert by["free"].asset.name == "shop_fix_window" and by["free"].candidates[-1]["match"]["too_tall"] == 3.1
        assert by["floor"].asset.name == "facade_mid"  # 2.6 m on the floor fits
        assert by["hung"].asset.name == "shop_fix_window"  # 0.4 + 2.6 m does not
        # a home's 2.4 m window is no storefront
        assert service.window_query(s.spec.room.openings[0], 2.7, "home")[0] != "shop window"
    finally:
        del CATALOG["shop window"], CATALOG["storefront window"]
