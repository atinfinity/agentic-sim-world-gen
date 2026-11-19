"""Plain chalkboards / whiteboards built in compose, and board searches that no longer take cutting boards
for chalkboards (no network)."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets import generic, kinds
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.assets.rerank import score


def rec(name, cls, tags=(), caption="", size=(0.4, 0.3, 0.02)):
    w, d, h = size
    return AssetRecord(id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
                       bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), semantic_class=cls,
                       tags=list(tags), caption=caption, license="CC-BY-4.0")


CUTTING = rec("sm_cutting_board", "cutting board", ["cutting board", "kitchen"], "A wooden cutting board.")
CLIP = rec("clipboard", "clipboard", ["clipboard", "board", "stationery"], "A clipboard.")
STANDING = rec("standing_chalkboard_01", "standing chalkboard", ["board", "sign"], "An A-frame chalkboard sign.",
               size=(0.6, 0.5, 1.0))
DISPLAY = rec("sm_wall_display", "display", ["wall display", "monitor"], "A wall mounted display.", (1.6, 0.06, 0.9))
CATALOG = {"chalkboard": [STANDING], "board": [CUTTING, CLIP], "blackboard": [], "wall display": [DISPLAY],
           "whiteboard": [], "white board": [CUTTING], "marker board": [CLIP]}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def test_kind_of_and_size():
    assert generic.kind_of("green chalkboard") == "chalkboard"
    assert generic.kind_of("classroom blackboard") == "chalkboard"
    assert generic.kind_of("white board") == generic.kind_of("dry-erase board") == "whiteboard"
    assert generic.kind_of("board", ["blackboard"]) == "chalkboard"
    assert generic.kind_of("cutting board") is None and generic.kind_of("keyboard") is None
    assert generic.board_size("chalkboard") == (3.6, 1.2, True)
    assert generic.board_size("whiteboard", (2.4, 0.0, 1.2), None) == (2.4, 1.2, True)
    assert generic.board_size("chalkboard", None, (3.0, 0.05, 1.0)) == (3.0, 1.0, False)  # too thin for a tray
    r = generic.record("whiteboard")
    assert r.size_m == pytest.approx((0.08, 1.8, 0.9)) and r.front_deg == 0.0 and r.source == "generic"
    assert generic.parse(r.uri) == {"kind": "whiteboard", "w": 1.8, "h": 0.9, "tray": True}
    assert generic.parse("generic:sofa?w=1") is None and generic.parse("generic:whiteboard?w=99") is None


def test_bare_board_synonym_is_no_chalkboard():
    """The agent's alt_queries ["blackboard", "board"] made a cutting board a good chalkboard match (2.25)."""
    assert score(CUTTING, "chalkboard", ["blackboard", "board"]).weak
    assert not score(CUTTING, "board").weak  # a board search still finds it
    # "white board" read apart was any white board: a cutting board, a clipboard tagged "board"
    assert score(CUTTING, "whiteboard", ["white board", "marker board"]).weak
    assert score(CLIP, "whiteboard", ["white board", "marker board"]).weak
    assert score(STANDING, "whiteboard", ["marker board"]).weak
    wb = rec("wb", "whiteboard", ["whiteboard"], "A whiteboard.")
    assert not score(wb, "white board").weak and not score(rec("w2", "white board"), "whiteboard", ["white board"]).weak


def test_kind_counts_stricter():
    table = {"version": 1, "built": "x", "sources": {"t": {"records": 9}}, "listed": [],
             "kinds": {"board": {"t": 80}, "chalkboard": {"t": 1}}, "plain": {"board": {"t": 4}}}
    prov = type("T", (), {"name": "t"})()
    a = kinds.availability(["chalkboard", "blackboard", "board"], prov, table)
    assert a["counts"] == {"t": 1}
    a = kinds.availability(["whiteboard", "white board", "marker board"], prov, table)
    assert a["counts"] == {"t": 0} and a["absent"] and a["kind"] == "whiteboard"
    assert kinds.kind("white board") == "whiteboard"


def test_kind_availability_offers_the_generic_board():
    a = service.kind_availability(["whiteboard", "white board", "marker board"])
    assert a["generic"] == "whiteboard" and "plain whiteboard" in a["generic_hint"]
    assert "generic" not in (service.kind_availability(["sofa"]) or {})


def test_kind_availability_puts_a_local_board_first(monkeypatch):
    """A real board in a local: folder is the one to use; the plain board built in compose is only the fallback."""
    monkeypatch.setattr(kinds, "availability", lambda queries, provider, table=None: {
        "kind": "chalkboard", "counts": {"local:boards": 2, "simready-central": 0}, "absent": False})
    a = service.kind_availability(["chalkboard"])
    assert a["generic"] == "chalkboard" and a["generic_hint"].startswith("a local: folder has 2 chalkboard asset(s)")
    assert "only the fallback" in a["generic_hint"]


def make(tmp_path, objects):
    return service.create_scene({"name": "s", "room": {"width": 8, "depth": 7, "height": 3.0},
                                 "objects": objects}, tmp_path / "s")


def test_board_search_puts_the_generic_board_first(tmp_path):
    hung = {"against_wall": "north", "wall_height": 0.9}
    st = make(tmp_path, [
        {"id": "chalkboard", "query": "chalkboard", "alt_queries": ["blackboard", "board"], "layout": hung},
        {"id": "wb", "query": "whiteboard", "alt_queries": ["white board", "marker board"],
         "layout": {"against_wall": "east", "wall_height": 0.8}, "min_size": [2.4, 0, 1.0]},
        {"id": "sign", "query": "chalkboard", "layout": {"against_wall": "south"}},
    ])
    assert st["issues"] == []
    s = service.load(tmp_path / "s")
    cb, wb, sign = (s.placements[i] for i in range(3))
    # hung on the wall: the A-frame sign is a real chalkboard but stands on the floor
    assert cb.asset.source == "generic" and cb.candidates[0]["match"]["generic"] and not cb.substitute
    assert [c["name"] for c in cb.candidates[1:]] == ["standing_chalkboard_01"]  # no cutting board
    assert wb.asset.uri == "generic:whiteboard?w=2.4&h=1" and wb.candidates[1:] == []
    assert sign.asset.name == "standing_chalkboard_01" and sign.candidates[-1]["source"] == "generic"
    o = s.obj("chalkboard")
    assert o.z == pytest.approx(0.9) and o.position[1] == pytest.approx(7 - 0.005 - 0.04, abs=1e-3)


def test_research_turns_a_wall_display_into_the_board(tmp_path):
    """The lead's recipe: an existing classroom chalkboard that holds a wall display."""
    make(tmp_path, [{"id": "chalkboard", "query": "wall display",
                     "layout": {"against_wall": "north", "wall_height": 0.9}}])
    st = service.research_object(tmp_path / "s", "chalkboard", query="chalkboard", alt_queries=["blackboard"])
    assert st["research"]["kept_current"] is False
    assert service.load(tmp_path / "s").placements[0].asset.source == "generic"


def test_compose_builds_the_board(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom, UsdPhysics

    make(tmp_path, [{"id": "chalkboard", "query": "chalkboard", "label": "黒板",
                     "layout": {"against_wall": "north", "wall_height": 0.9}}])
    out = service.compose_scene(tmp_path / "s")
    stage = Usd.Stage.Open(out["usd"])
    prim = stage.GetPrimAtPath("/World/Objects/chalkboard")
    assert prim.GetCustomDataByKey("agentic_sim_world_gen:source") == "generic"
    assert prim.GetCustomDataByKey("agentic_sim_world_gen:license") == "CC0-1.0"
    assert prim.GetCustomDataByKey("agentic_sim_world_gen:label") == "黒板"
    assert prim.GetCustomDataByKey("agentic_sim_world_gen:generic")["kind"] == "chalkboard"
    assert (tmp_path / "s" / "assets" / "generic" / "chalkboard_3p6x1p2.usda").is_file()
    surface = stage.GetPrimAtPath("/World/Objects/chalkboard/Asset/Surface")
    assert surface.HasAPI(UsdPhysics.CollisionAPI)
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(prim).ComputeAlignedRange()
    lo, hi = box.GetMin(), box.GetMax()
    assert hi[0] - lo[0] == pytest.approx(3.6, abs=1e-3) and lo[2] == pytest.approx(0.9) and hi[2] == pytest.approx(2.1)
    assert hi[1] == pytest.approx(7 - 0.005, abs=1e-3) and hi[1] - lo[1] == pytest.approx(0.08, abs=1e-3)
    from agentic_sim_world_gen.license import report

    rep = report(service.load(tmp_path / "s"))
    assert not rep["warnings"] and "agentic-sim-world-gen" in str(rep)


CHALK = rec("sm_stationery_chalk_stick_a05_01", "chalk", ["chalkboard", "stationery", "chalk"], "A stick of chalk.",
            (0.08, 0.01, 0.01))


def test_a_chalk_stick_tagged_chalkboard_is_no_board(tmp_path, monkeypatch):
    """The chalk stick matched by its tag (not weak, not standing) and pushed the plain
    board last, so the A-frame sign was chosen for a chalkboard hung on the wall."""
    monkeypatch.setitem(CATALOG, "chalkboard", [STANDING, CHALK])
    make(tmp_path, [{"id": "chalkboard", "query": "chalkboard", "alt_queries": ["blackboard"],
                     "layout": {"against_wall": "north", "wall_height": 0.9}}])
    p = service.load(tmp_path / "s").placements[0]
    assert p.asset.source == "generic"
    assert generic.is_board({"semantic_class": "standing chalkboard"}, "chalkboard")
    assert not generic.is_board({"semantic_class": "chalk", "name": CHALK.name, "tags": ["chalkboard"]}, "chalkboard")


def test_standing_or_mobile_boards_keep_the_wall_board_last(tmp_path, monkeypatch):
    monkeypatch.setitem(CATALOG, "standing chalkboard", [STANDING])
    make(tmp_path, [{"id": "wb", "query": "mobile whiteboard"},
                    {"id": "sign", "query": "standing chalkboard"}])
    s = service.load(tmp_path / "s")
    wb = s.placements[0]  # nothing else found: the wall board is offered as a substitute, not as a mobile one
    assert wb.candidates[-1]["source"] == "generic" and wb.candidates[-1]["match"]["weak"] and wb.substitute
    assert s.placements[1].candidates[-1]["source"] == "generic"
    assert s.placements[1].asset.name == "standing_chalkboard_01"


def test_compound_synonyms_outside_boards_still_count():
    """Review: the board rules must not touch chair / armchair, plant / houseplant, case / bookcase."""
    chair = rec("chair", "chair", ["chair"], "A chair.", (0.5, 0.5, 0.9))
    assert score(chair, "chair", ["armchair"]).score == score(chair, "chair").score > 2
    house = rec("hp", "houseplant", ["plant", "houseplant"], "A potted houseplant.", (0.4, 0.4, 0.8))
    assert score(house, "plant").score >= 1.5
    arm = rec("arm", "armchair", ["chair", "armchair"], "An armchair.", (0.8, 0.8, 0.9))
    assert score(arm, "chair").score >= 1.5
    box = rec("tb", "toolbox", ["box", "toolbox"], "A red toolbox.", (0.5, 0.2, 0.2))
    assert score(box, "box").score >= 1.5
    case = rec("bc", "case", ["case"], "A case.", (0.4, 0.3, 0.3))
    assert score(case, "case", ["bookcase"]).score == score(case, "case").score > 2
    light = rec("l", "light", ["light"], "A light.", (0.3, 0.3, 0.3))
    assert score(light, "light", ["spotlight"]).score == score(light, "light").score


WALL = rec("wall_chalkboard", "environment", ["environment", "wall chalkboard", "黒板"],
           "A wall chalkboard 3600 x 1200 with a chalk tray.", (3.6, 0.075, 1.2))


def test_a_standing_board_goes_after_the_wall_board_when_hung(tmp_path, monkeypatch):
    """With Poly Haven searched too, the A-frame sign matched by class (2.5) and beat the
    local: wall chalkboard matched by tag (1.5) for a chalkboard hung on the wall."""
    monkeypatch.setitem(CATALOG, "chalkboard", [STANDING, WALL])
    make(tmp_path, [{"id": "cb", "query": "chalkboard", "layout": {"against_wall": "north", "wall_height": 0.8}},
                    {"id": "sign", "query": "chalkboard", "layout": {"against_wall": "south"}}])
    cb, sign = service.load(tmp_path / "s").placements
    assert [c["name"] for c in cb.candidates] == ["wall_chalkboard", "generic_chalkboard_3.6x1.2",
                                                    "standing_chalkboard_01"]
    assert cb.candidates[1]["source"] == "generic" and cb.asset.name == "wall_chalkboard"
    # standing on the floor (no wall_height): the order of the search stays
    assert sign.asset.name == "standing_chalkboard_01" and sign.candidates[-1]["source"] == "generic"


def test_hung_with_only_a_standing_board_puts_the_plain_one_first(tmp_path):
    make(tmp_path, [{"id": "cb", "query": "chalkboard", "layout": {"against_wall": "north", "wall_height": 0.8}}])
    (cb,) = service.load(tmp_path / "s").placements
    assert [c["source"] for c in cb.candidates] == ["generic", "fake"]
