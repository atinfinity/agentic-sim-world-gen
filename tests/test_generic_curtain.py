"""Pleated window curtains built in compose when no curtain asset fits. No network."""

from dataclasses import replace

import pytest
from test_layout import CATALOG, fake, objs, rec  # noqa: F401  (fixture: fake asset search)

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets import generic, generic_curtain

ROOM = {"width": 3.64, "depth": 2.73, "height": 2.4, "wall_thickness": 0.12, "place": "home",
        "openings": [{"id": "win", "kind": "window", "wall": "north", "offset": 1.82, "width": 1.6, "height": 1.1,
                      "sill": 0.9},
                     {"id": "patio", "kind": "window", "wall": "south", "offset": 1.82, "width": 1.6, "height": 2.0,
                      "sill": 0.0}]}
WINDOWS = {"sliding window": [rec("sliding_window_waist", "window", 1.69, 0.076, 1.17)],
           "floor-length sliding window": [rec("sliding_window_floor", "window", 1.69, 0.076, 2.03)]}
# what SimReady Central has for "curtain": a hospital cubicle curtain and a soundproofing panel
CUBICLE = replace(rec("cubicle_curtain", "curtain", 2.4, 0.05, 2.1), caption="Hospital cubicle privacy curtain",
                  tags=["curtain", "hospital"])
PANEL = replace(rec("acoustic_panel", "panel", 1.9, 0.1, 1.4), caption="Soundproofing panel")


@pytest.fixture
def catalog():
    keep = dict(CATALOG)
    CATALOG.update(WINDOWS)
    for q in ("waist window curtain", "floor-length curtain", "curtain", "curtains"):
        CATALOG[q] = [CUBICLE, PANEL]
    yield CATALOG
    CATALOG.clear()
    CATALOG.update(keep)


def test_uri_and_drop():
    u = generic_curtain.uri_for(1.8, 1.55, "open_sheer", "#7A8B9C")
    p = generic.parse(u)  # the generic mechanism reads it too
    assert p == generic_curtain.parse(u)
    assert p["kind"] == "curtain" and p["style"] == "open_sheer" and p["color"] == "#7a8b9c"
    assert p["sheer_color"] == generic_curtain.SHEER_COLOR
    assert generic_curtain.parse(generic_curtain.uri_for(2, 2, "sheer", "#eeeeee"))["sheer_color"] == "#eeeeee"
    assert generic_curtain.parse("generic:curtain?style=draped") is None
    assert generic_curtain.parse("generic:curtainx") is None
    # waist window 0.9-2.0 m: rail 0.1 m above the head, 0.15 m below the sill
    assert generic_curtain.drop(0.9, 2.0, 2.4) == (0.75, 1.35)
    # floor-length under a low ceiling: rail at the ceiling, 1 cm off the floor
    assert generic_curtain.drop(0.0, 2.35, 2.4) == (0.01, 2.39)
    assert generic_curtain.asks_for("floor-length curtain") and generic_curtain.asks_for("sheer drapes")
    assert not generic_curtain.asks_for("shower curtain") and not generic_curtain.asks_for("roller blind")
    with pytest.raises(ValueError, match="style"):
        generic_curtain.check_style("half", None)
    with pytest.raises(ValueError, match="color"):
        generic_curtain.check_style("open", "beige")


def test_panels_by_style():
    w = 1.8
    names = {s: [n for n, *_ in generic_curtain.panels({"w": w, "style": s})] for s in generic_curtain.STYLES}
    assert names == {"open": ["Panel_L", "Panel_R"], "closed": ["Panel_L", "Panel_R"], "sheer": ["Sheer"],
                     "open_sheer": ["Sheer", "Panel_L", "Panel_R"]}
    (_, y0, y1, _, kind), _ = generic_curtain.panels({"w": w, "style": "open"})
    # Panel_L is the -Y one: the left seen from the room (it looks toward -X)
    assert kind == "gathered" and y0 == pytest.approx(-w / 2) and y1 - y0 == pytest.approx(max(0.18 * w, 0.22))
    pts = generic_curtain.wave(-0.9, 0.9, 0.02, "drawn")
    xs = [x for x, _, _ in pts]
    assert pts[0][1] == -0.9 and pts[-1][1] == pytest.approx(0.9)
    assert max(xs) == pytest.approx(0.02 + 0.022, abs=2e-3) and min(xs) == pytest.approx(0.02 - 0.022, abs=2e-3)
    # inside the record's depth
    assert max(xs) < generic_curtain.DEPTH / 2 and min(x for x, *_ in generic_curtain.wave(-1, 1, generic_curtain.X_SHEER, "sheer")) > -generic_curtain.DEPTH / 2


def test_fill_windows_builds_the_curtain(tmp_path, catalog):
    """SimReady Central's cubicle curtain (hospital) and a soundproofing panel left curtain_win in not_found."""
    service.create_scene({"name": "s", "room": ROOM, "objects": [
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}}]}, tmp_path / "s")
    st = service.fill_windows(tmp_path / "s", curtain_color="#8a9a7b")
    assert sorted(st["added"]) == ["curtain_patio", "curtain_win", "win_patio", "win_win"]
    assert "not_found" not in st and sorted(st["generated"]) == ["curtain_patio", "curtain_win"]
    assert "generated" in st["generated_hint"]
    s = service.load(tmp_path / "s")
    by = {p.object_id: p for p in s.placements}
    a = by["curtain_win"].asset
    assert a.source == "generic" and a.license == "CC0-1.0" and not by["curtain_win"].substitute
    assert generic_curtain.parse(a.uri) == {"kind": "curtain", "w": 1.8, "h": 1.35, "style": "open",
                                            "color": "#8a9a7b", "sheer_color": generic_curtain.SHEER_COLOR}
    assert [c["name"] for c in by["curtain_win"].candidates[1:]] == ["cubicle_curtain"]  # a good match, but a hospital one
    o = objs(st)
    c, f = o["curtain_win"], o["curtain_patio"]
    assert c["z"] == pytest.approx(0.75) and c["fit_scale"] == pytest.approx((1.0, 1.0, 1.0))
    assert c["position"][1] == pytest.approx(2.73 - 0.01 - generic_curtain.DEPTH / 2)
    assert f["z"] == pytest.approx(0.01)  # floor-length: rail at 2.1 m, 1 cm off the floor
    assert st["issues"] == []  # the desk under the window does not clash with the curtain
    assert service.fill_windows(tmp_path / "s")["added"] == []


def test_a_real_window_curtain_stays_first(tmp_path, catalog):
    curtain = replace(rec("curtain_waist_window", "curtain", 1.9, 0.12, 1.35), caption="Window curtain")
    catalog["waist window curtain"] = [curtain]
    service.create_scene({"name": "s", "room": {**ROOM, "openings": ROOM["openings"][:1]}, "objects": []},
                         tmp_path / "s")
    st = service.fill_windows(tmp_path / "s")
    assert "generated" not in st
    p = {p.object_id: p for p in service.load(tmp_path / "s").placements}["curtain_win"]
    assert p.asset.name == "curtain_waist_window" and p.candidates[-1]["match"]["generic"]


def test_search_and_style_errors(tmp_path, catalog):
    service.create_scene({"name": "s", "room": ROOM, "objects": [
        {"id": "c", "query": "curtain", "covers": "win"},
        {"id": "shower", "query": "shower curtain", "layout": {"against_wall": "east"}}]}, tmp_path / "s")
    by = {p.object_id: p for p in service.load(tmp_path / "s").placements}
    assert by["c"].asset.source == "generic"  # any curtain over a window, not only fill_windows'
    assert not any((c.get("match") or {}).get("generic") for c in by["shower"].candidates)
    with pytest.raises(ValueError, match="curtain style"):
        service.fill_windows(tmp_path / "s", curtain_style="half")


@pytest.mark.parametrize("style", generic_curtain.STYLES)
def test_compose_builds_the_curtain(tmp_path, catalog, style):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom, UsdPhysics

    service.create_scene({"name": "s", "room": {**ROOM, "openings": ROOM["openings"][:1]}, "objects": []},
                         tmp_path / "s")
    service.fill_windows(tmp_path / "s", curtain_style=style)
    out = service.compose_scene(tmp_path / "s")
    stage = Usd.Stage.Open(out["usd"])
    prim = stage.GetPrimAtPath("/World/Objects/curtain_win")
    assert prim.GetCustomDataByKey("agentic_sim_world_gen:source") == "generic"
    assert prim.GetCustomDataByKey("agentic_sim_world_gen:license") == "CC0-1.0"
    assert prim.GetCustomDataByKey("agentic_sim_world_gen:generic")["style"] == style
    asset = stage.GetPrimAtPath("/World/Objects/curtain_win/Asset")
    parts = {c.GetName(): c for c in asset.GetChildren()}
    assert parts["Rail"].HasAPI(UsdPhysics.CollisionAPI)  # the thin static collider
    fabric = [c for n, c in parts.items() if n.startswith(("Panel", "Sheer"))]
    assert fabric and not any(c.HasAPI(UsdPhysics.CollisionAPI) for c in fabric)
    for c in fabric:
        shadow = UsdGeom.PrimvarsAPI(c).GetPrimvar("doNotCastShadows")
        assert bool(shadow and shadow.Get()) == (c.GetName() == "Sheer")
    if "sheer" in style:
        assert "Sheer" in parts
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(prim).ComputeAlignedRange()
    lo, hi = box.GetMin(), box.GetMax()
    assert hi[0] - lo[0] == pytest.approx(1.8, abs=1e-3)  # window + COVER_WIDER
    assert hi[2] == pytest.approx(2.1, abs=1e-3)  # the rail 0.1 m above the head
    assert lo[2] == pytest.approx(0.75, abs=1e-3)  # 0.15 m below the sill
    assert hi[1] <= 2.73 - 0.01 + 1e-3  # off the wall face
    from agentic_sim_world_gen.license import report

    assert not report(service.load(tmp_path / "s"))["warnings"]


def test_layout_preview_draws_the_pleats(tmp_path, catalog):
    from agentic_sim_world_gen.preview.layout2d import render_svg

    service.create_scene({"name": "s", "room": {**ROOM, "openings": ROOM["openings"][:1]}, "objects": []},
                         tmp_path / "s")
    service.fill_windows(tmp_path / "s", curtain_style="open_sheer")
    svg = render_svg(service.load(tmp_path / "s"))
    assert svg.count('class="wg-pleats"') == 3 and 'data-panel="Sheer"' in svg


def test_research_keeps_the_style_and_window_size(tmp_path, catalog):
    """Review: research_object reset a closed green curtain to an open beige one of the default size."""
    service.create_scene({"name": "s", "room": {**ROOM, "openings": ROOM["openings"][:1]}, "objects": []},
                         tmp_path / "s")
    service.fill_windows(tmp_path / "s", curtain_style="closed", curtain_color="#8a9a7b")
    st = service.research_object(tmp_path / "s", "curtain_win", query="blackout curtain")
    p = {p.object_id: p for p in service.load(tmp_path / "s").placements}["curtain_win"]
    got = generic_curtain.parse(p.asset.uri)
    assert (got["style"], got["color"], got["w"], got["h"]) == ("closed", "#8a9a7b", 1.8, 1.35)
    assert objs(st)["curtain_win"]["fit_scale"] == pytest.approx((1.0, 1.0, 1.0))


def test_add_object_curtain_is_sized_to_its_window(tmp_path, catalog):
    """Review: a curtain added with covers got fit_scale (1, 0.9, 0.61): an elliptic rail collider."""
    service.create_scene({"name": "s", "room": {**ROOM, "openings": ROOM["openings"][:1]}, "objects": []},
                         tmp_path / "s")
    st = service.add_object(tmp_path / "s", {"id": "c", "query": "curtain panels", "covers": "win"})
    o = objs(st)["c"]
    assert o["fit_scale"] == pytest.approx((1.0, 1.0, 1.0), abs=1e-3) and o["z"] == pytest.approx(0.75)


def test_real_window_curtain_words():
    def cand(name, cls="curtain", tags=(), caption=""):
        return {"name": name, "semantic_class": cls, "tags": list(tags), "caption": caption,
                "match": {"score": 2.0, "weak": False}}

    # caption words say nothing about the kind
    assert generic_curtain.real_curtain(cand("blackout_curtain", caption="Blackout window curtain for bedroom privacy"))
    assert generic_curtain.real_curtain(cand("lace_curtain", caption="Lace window curtain, shade of ivory"))
    assert generic_curtain.real_curtain(cand("curtain_panels"))
    assert not generic_curtain.real_curtain(cand("cubicle_curtain", tags=["hospital"]))
    assert not generic_curtain.real_curtain(cand("curtain", tags=["shower curtain"]))
    assert not generic_curtain.real_curtain(cand("roller_blind", cls="blind"))
    assert generic_curtain.asks_for("curtain panels") and generic_curtain.asks_for("drapery")
    assert not generic_curtain.asks_for("door curtain")

    class O:
        covers, query, alt_queries = "win", "window cover", ["drapes"]

    assert generic_curtain.wanted(O())


def test_no_curtain_over_a_huge_window(tmp_path, catalog):
    """A window wider than MAX_SIZE gets no generic curtain (compose would refuse its uri)."""
    room = {**ROOM, "width": 30.0, "openings": [{**ROOM["openings"][0], "offset": 15.0, "width": 25.0}]}
    service.create_scene({"name": "s", "room": room, "objects": []}, tmp_path / "s")
    st = service.fill_windows(tmp_path / "s")
    assert "generated" not in st and "curtain_win" in [n["id"] for n in st.get("not_found", [])]


def test_style_ignored_for_a_real_curtain(tmp_path, catalog):
    catalog["waist window curtain"] = [rec("curtain_waist_window", "curtain", 1.9, 0.12, 1.35)]
    service.create_scene({"name": "s", "room": {**ROOM, "openings": ROOM["openings"][:1]}, "objects": []},
                         tmp_path / "s")
    st = service.fill_windows(tmp_path / "s", curtain_style="sheer")
    assert "generated" not in st and "curtain_win" in st["curtain_style_ignored"]
