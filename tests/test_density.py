"""Room density: small things added for a lived-in look, kept only where they add no problem."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.assets.shelves import surfaces_from_boxes
from agentic_sim_world_gen.layout.array import instances
from agentic_sim_world_gen.scene import decor

from test_fill import BOXES

SHELVES = surfaces_from_boxes(BOXES)


def rec(name, cls, w, d, h, shelves=None, polygons=None):
    return AssetRecord(
        id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
        semantic_class=cls, license="CC0-1.0", front_deg=270.0, shelves=shelves, polygons=polygons,
    )


CATALOG = {
    "desk": [rec("desk", "desk", 1.4, 0.7, 0.74)],
    "office chair": [rec("chair", "office chair", 0.55, 0.55, 0.85)],
    "stack of books": [rec("books", "book", 0.25, 0.18, 0.12)],
    "pen holder": [rec("pens", "pen holder", 0.08, 0.08, 0.12)],
    "potted plant": [rec("plant", "plant", 0.4, 0.4, 0.9)],
    "sofa": [rec("sofa", "sofa", 1.8, 0.9, 0.8)],
    "coffee table": [rec("ctable", "coffee table", 1.0, 0.5, 0.4)],
    "throw pillow": [rec("pillow", "pillow", 0.4, 0.15, 0.4)],
    "magazine": [rec("mag", "magazine", 0.3, 0.22, 0.01)],
    "rug": [rec("rug", "rug", 2.0, 1.4, 0.01)],
    "computer monitor": [rec("monitor", "monitor", 0.6, 0.2, 0.45)],
    "document tray": [rec("tray", "tray", 0.35, 0.26, 0.08)],
    "coffee mug": [rec("mug", "mug", 0.1, 0.1, 0.1)],
    "gondola": [rec("gondola", "gondola", 0.94, 0.44, 1.4, shelves=SHELVES)],
    "snack bag": [rec("snack", "snack", 0.15, 0.06, 0.2)],
    "potato chips bag": [rec("chips", "potato chips bag", 0.15, 0.06, 0.2)],
    "cup noodle": [rec("noodle", "cup noodle", 0.1, 0.1, 0.12)],
    "candy bag": [rec("candy", "candy bag", 0.12, 0.05, 0.18)],
    "remote control": [rec("sofa_cushion_lookalike", "sofa", 0.2, 0.05, 0.03)],  # only a substitute
}


@pytest.fixture(autouse=True)
def provider():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


STUDY = [{"id": "desk", "label": "机", "query": "desk", "layout": {"against_wall": "north"}},
         {"id": "chair", "label": "椅子", "query": "office chair", "layout": {"in_front_of": "desk"}}]
LIVING = [{"id": "sofa", "label": "ソファ", "query": "sofa", "layout": {"against_wall": "south"}},
          {"id": "table", "label": "ローテーブル", "query": "coffee table", "layout": {"in_front_of": "sofa", "gap": 0.4}}]


def make(tmp_path, objects, density=None, place="home", w=4.0, d=3.0, name="s"):
    room = {"width": w, "depth": d, "place": place}
    if density:
        room["density"] = density
    return service.create_scene({"name": name, "room": room, "objects": objects}, tmp_path / name)


def decor_ids(st):
    return sorted(o["id"] for o in st["objects"] if o["id"].startswith("decor_"))


def test_sparse_adds_nothing(tmp_path):
    st = make(tmp_path, STUDY, "sparse")
    assert decor_ids(st) == [] and st["issues"] == []
    assert decor_ids(make(tmp_path, STUDY, name="none")) == []  # no density: as before


def test_study_normal(tmp_path):
    st = make(tmp_path, STUDY, "normal")
    ids = decor_ids(st)
    assert {"decor_books_desk", "decor_pen_holder_desk", "decor_plant_desk"} <= set(ids)
    assert "decor_mug_desk" not in ids  # lived_in only
    assert st["issues"] == []
    s = service.load(tmp_path / "s")
    books = s.obj("decor_books_desk")
    assert books.decor and books.layout.on == "desk" and books.z == pytest.approx(0.74)
    rows = {a.object: a for a in s.spec.assumptions}
    assert rows["decor_books_desk"].basis == decor.BASIS and not rows["decor_books_desk"].given


def test_lived_in_adds_more_without_new_problems(tmp_path):
    sparse = make(tmp_path, STUDY + LIVING, "sparse", w=5.0, d=4.0, name="a")
    lived = make(tmp_path, STUDY + LIVING, "lived_in", w=5.0, d=4.0, name="b")
    ids = set(decor_ids(lived))
    assert {"decor_mug_desk", "decor_cushion_sofa", "decor_cushion_sofa_2", "decor_magazine_table"} <= ids
    assert "decor_rug_sofa" in ids  # flat: the coffee table may stand on it
    assert "decor_remote_table" not in ids  # only a substitute asset: left out
    assert lived["issues"] == sparse["issues"] == []
    key = {(u["kind"], tuple(u["objects"])) for u in sparse["usability"]}
    assert {(u["kind"], tuple(u["objects"])) for u in lived["usability"]} <= key
    s = service.load(tmp_path / "b")
    c1, c2 = s.obj("decor_cushion_sofa"), s.obj("decor_cushion_sofa_2")
    assert c1.position != c2.position and c1.z == pytest.approx(0.8 * 0.55)


def test_density_change_remakes_decor(tmp_path):
    make(tmp_path, STUDY, "lived_in")
    d = tmp_path / "s"
    st = service.set_room(d, density="sparse")
    assert decor_ids(st) == []
    s = service.load(d)
    assert not any(a.basis == decor.BASIS for a in s.spec.assumptions)
    st = service.set_room(d, density="normal")
    assert "decor_books_desk" in decor_ids(st) and "decor_mug_desk" not in decor_ids(st)


def test_remove_host_takes_its_decor(tmp_path):
    make(tmp_path, STUDY, "normal")
    d = tmp_path / "s"
    st = service.remove_object(d, "desk")
    assert decor_ids(st) == []  # books / pens on it, plant beside it
    make(tmp_path, STUDY, "normal", name="t")
    st = service.remove_object(tmp_path / "t", "decor_books_desk")  # one taken out by hand
    assert "decor_books_desk" not in decor_ids(st) and "decor_pen_holder_desk" in decor_ids(st)


def test_existing_kind_is_not_doubled(tmp_path):
    objs = STUDY + [{"id": "my_plant", "query": "potted plant", "layout": {"against_wall": "east"}}]
    st = make(tmp_path, objs, "normal")
    assert "decor_plant_desk" not in decor_ids(st)


def test_office_desk_array_gets_one_monitor_each(tmp_path):
    objs = [{"id": "desks", "label": "机", "query": "desk", "array": {"count": 3, "rows": 2, "aisle": 1.2},
             "layout": {"center": True}}]
    st = make(tmp_path, objs, "normal", place="office", w=8.0, d=6.0)
    assert "decor_monitor_desks" in decor_ids(st) and st["issues"] == []
    s = service.load(tmp_path / "s")
    m, h = s.obj("decor_monitor_desks"), s.obj("desks")
    arec = {p.object_id: p.asset for p in s.placements}
    mon = instances(m, arec[m.id])
    dk = instances(h, arec[h.id])
    assert len(mon) == len(dk) == 6
    for a, b in zip(mon, dk):  # same unit order, each monitor 0.25 x depth toward the desk's back
        assert abs(a.position[0] - b.position[0]) < 1e-6
        assert abs(a.position[1] - b.position[1]) == pytest.approx(0.25 * 0.7, abs=1e-3)


def test_shop_stocks_every_empty_fixture(tmp_path):
    objs = [{"id": "g1", "query": "gondola", "layout": {"against_wall": "north"}},
            {"id": "g2", "query": "gondola", "layout": {"against_wall": "south"}},
            {"id": "own", "query": "snack bag", "fill": {"on": "g2", "levels": [0]}}]
    st = make(tmp_path, objs, "normal", place="convenience_store", w=5.0, d=4.0)
    ids = decor_ids(st)
    # G2 is stocked already; g1 gets three products, one per level
    assert ids == ["decor_stock_g1", "decor_stock_g1_2", "decor_stock_g1_3"] and st["issues"] == []
    s = service.load(tmp_path / "s")
    got = {}
    for i in ids:
        f = s.obj(i).fill
        assert f.physics == "front" and f.slots
        got[s.obj(i).query] = {int(n.split("_l")[1][0]) for n, *_ in f.slots}
        rows = {n.rsplit("_", 1)[1] for n, *_ in f.slots}
        assert len(rows) > 1  # as deep as fits, only row 0 simulated
    assert got == {"potato chips bag": {0}, "cup noodle": {1}, "candy bag": {2}}
    # the empty_shelves warning is gone for g1, g2 has one level of three stocked
    kinds = {(u["kind"], tuple(u["objects"])) for u in st["usability"]}
    assert ("empty_shelves", ("g2",)) in kinds and ("empty_shelves", ("g1",)) not in kinds
    # only the front row counts toward the body budget
    sim = sum(1 for i in ids for n, *_ in s.obj(i).fill.slots if n.endswith("_0"))
    assert service._bodies(s) == 2 + len(s.obj("own").fill.slots) + sim


def test_shop_stock_fills_levels_left_empty(tmp_path, monkeypatch):
    monkeypatch.delitem(CATALOG, "cup noodle")
    objs = [{"id": "g1", "query": "gondola", "layout": {"against_wall": "north"}}]
    st = make(tmp_path, objs, "normal", place="convenience_store", w=5.0, d=4.0)
    s = service.load(tmp_path / "s")
    levels = {}
    for i in decor_ids(st):
        for n, *_ in s.obj(i).fill.slots:
            levels.setdefault(int(n.split("_l")[1][0]), set()).add(s.obj(i).query)
    assert sorted(levels) == [0, 1, 2]  # level 1 (no cup noodle) taken by the first product
    assert levels[1] == {"potato chips bag"}
    assert not [u for u in st["usability"] if u["kind"] == "empty_shelves"]


def test_shop_fixture_boards_found_before_stocking(tmp_path, monkeypatch):
    """Real fixtures have no boards until the USD is read: they are read for every shop fixture, so the
    stock is split by level and the empty_shelves warning it fixes does not count as a new problem."""
    import agentic_sim_world_gen.assets.shelves as shelves_mod

    monkeypatch.setitem(CATALOG, "gondola", [rec("gondola", "gondola", 0.94, 0.44, 1.4)])
    monkeypatch.setattr(shelves_mod, "find_shelves", lambda uri, cache: SHELVES)
    objs = [{"id": "g1", "query": "gondola", "layout": {"against_wall": "north"}}]
    st = make(tmp_path, objs, "normal", place="convenience_store", w=5.0, d=4.0)
    assert decor_ids(st) == ["decor_stock_g1", "decor_stock_g1_2", "decor_stock_g1_3"]
    assert not [u for u in st["usability"] if u["kind"] == "empty_shelves"]
    # a bare fixture is reported without any stock on the shop's shelves
    st = make(tmp_path, objs, place="convenience_store", w=5.0, d=4.0, name="bare")
    assert [u["objects"] for u in st["usability"] if u["kind"] == "empty_shelves"] == [["g1"]]


def test_plan_is_pure_and_skips_outdoor(tmp_path):
    make(tmp_path, STUDY, name="p")
    s = service.load(tmp_path / "p")
    assert decor.plan(s, "sparse") == [] and decor.plan(s, None) == []
    items = decor.plan(s, "normal")
    assert {i.decor.key for i in items} >= {"books", "pen_holder", "plant"}
    assert all(t.decor for i in items for t in i.tries)
    s.spec.room.outdoor = True
    assert decor.plan(s, "lived_in") == []


def test_tiny_look_alike_is_left_out(tmp_path):
    """An "air cushion" face mask matches "cushion" by tag: min_size keeps it off the sofa."""
    CATALOG["throw pillow"] = [rec("mask", "mask", 0.13, 0.09, 0.05)]
    try:
        st = make(tmp_path, LIVING, "normal")
    finally:
        CATALOG["throw pillow"] = [rec("pillow", "pillow", 0.4, 0.15, 0.4)]
    assert not any(i.startswith("decor_cushion") for i in decor_ids(st))


def test_shop_stock_keeps_the_body_count_down(tmp_path, monkeypatch):
    objs = [{"id": "g1", "query": "gondola", "layout": {"against_wall": "north"}}]
    st = make(tmp_path, objs, "normal", place="convenience_store", w=5.0, d=4.0)
    s = service.load(tmp_path / "s")
    f = s.obj("decor_stock_g1").fill
    assert f.rows is None and f.physics == "front"  # as deep as fits, the rows behind static
    assert any(not f.simulated(n) for n, *_ in f.slots)
    monkeypatch.setattr(decor, "BODY_BUDGET", 3)  # too many products for the scene: left out, and said so
    st = make(tmp_path, objs, "normal", place="convenience_store", w=5.0, d=4.0, name="t")
    assert decor_ids(st) == []
    hist = (tmp_path / "t" / "preview.json").read_text()
    assert "物が多すぎる" in hist


SCAN = rec("scan_chips", "potato chips bag", 0.15, 0.06, 0.2, polygons=860_000)  # a scanned asset


def test_scanned_stock_goes_to_a_lighter_candidate(tmp_path, monkeypatch):
    """Thousands of 0.86 M-face scans on shelves ran Isaac Sim out of memory: stock takes the first light one."""
    objs = [{"id": "g1", "query": "gondola", "layout": {"against_wall": "north"}}]
    monkeypatch.setitem(CATALOG, "potato chips bag", [SCAN, rec("chips", "potato chips bag", 0.15, 0.06, 0.2,
                                                                polygons=2000)])
    make(tmp_path, objs, "normal", place="convenience_store", w=5.0, d=4.0)
    s = service.load(tmp_path / "s")
    chips = [p.asset.name for p in s.placements if p.asset and p.asset.semantic_class == "potato chips bag"]
    assert chips == ["chips"]
    monkeypatch.setitem(CATALOG, "potato chips bag", [SCAN])  # only the scan: left out, and said so
    make(tmp_path, objs, "normal", place="convenience_store", w=5.0, d=4.0, name="t")
    s = service.load(tmp_path / "t")
    assert not any(p.asset and p.asset.name == "scan_chips" for p in s.placements)
    assert "heavy" in {k.reason for k in s.spec.decor_skipped}
    assert "重い素材しかない" in (tmp_path / "t" / "preview.json").read_text()


def test_too_many_faces_left_out_and_not_opened(tmp_path, monkeypatch):
    objs = [{"id": "g1", "query": "gondola", "layout": {"against_wall": "north"}}]
    make(tmp_path, objs, "normal", place="convenience_store", w=5.0, d=4.0, name="full")
    full = sum(len(o.fill.slots) for o in service.load(tmp_path / "full").spec.objects if o.fill)
    monkeypatch.setattr(decor, "DRAW_BUDGET", 50_000)  # 10 products of 5000 faces (unknown) at most
    st = make(tmp_path, objs, "normal", place="convenience_store", w=5.0, d=4.0)
    n = sum(len(o.fill.slots) for o in service.load(tmp_path / "s").spec.objects if o.fill)
    assert full > 10 and 0 < n <= 10 and "draw_load" not in st  # a front row only (STOCK_TRIES), not the shelf
    objs.append({"id": "noodle", "query": "cup noodle", "fill": {"on": "g1"}})  # stocked by hand: warned
    st = make(tmp_path, objs, place="convenience_store", w=5.0, d=4.0, name="t")
    assert st["draw_load"]["faces"] > 50_000 and st["draw_load"]["heaviest"][0]["object"] == "noodle"
    assert "draw_hint" in st
    r = service.check_in_isaac(tmp_path / "t")
    assert r["ok"] is False and r["verdict"] == "failed" and "too heavy" in r["error"]


def test_bakery_is_a_store_with_bread():
    from agentic_sim_world_gen.scene import decor
    from agentic_sim_world_gen.scene.spec import STORE_PLACES

    assert "bakery" in STORE_PLACES and decor.PRODUCTS["bakery"][0][0] == "bread"


def test_variety_store_goods_and_no_guess_in_a_plain_shop(tmp_path):
    """A variety store (雑貨店) stocks mugs, towels by the fixture's words; a shop of no kind stocks only what
    the fixture names (a generic product box was a case of soft drinks)."""
    from agentic_sim_world_gen.scene.questions import guess_place
    from agentic_sim_world_gen.scene.spec import STORE_PLACES
    from agentic_sim_world_gen.scene.looks import ceiling_surface

    assert "variety_store" in STORE_PLACES and guess_place("小さな雑貨店を作って") == "variety_store"
    assert guess_place("100円ショップの雑貨売り場") == "hundred_yen_shop"
    assert ceiling_surface(True, "office", "variety_store")["name"] == "ceiling_board"
    objs = [{"id": "g1", "query": "gondola", "layout": {"against_wall": "north"}},
            {"id": "towel_shelf", "query": "gondola", "layout": {"against_wall": "south"}}]
    st = make(tmp_path, objs, "normal", place="variety_store", w=5.0, d=4.0)
    ids = decor_ids(st)  # mugs (bowls and vases are not in the catalog; mugs fill their levels too)
    assert ids and all(i.startswith("decor_stock_g1") for i in ids), ids  # no towels in the catalog: left out
    s = service.load(tmp_path / "s")
    assert {s.obj(i).query for i in ids} == {"coffee mug"}
    items = {i.host: i.tries[0].query for i in decor.plan(s, "normal")}
    assert items == {"towel_shelf": "folded towel"}
    # a plain shop: only the magazine rack (named) gets stock
    objs = [{"id": "g1", "query": "gondola", "layout": {"against_wall": "south"}},
            {"id": "magazine_rack", "query": "gondola", "layout": {"against_wall": "north"}}]
    st = make(tmp_path, objs, "normal", place="shop", w=5.0, d=4.0, name="plain")
    ids = decor_ids(st)
    assert ids and all(i.startswith("decor_stock_magazine_rack") for i in ids), ids


def test_skipped_decor_is_kept_and_tried_again_once_the_host_is_fixed(tmp_path):
    """A gondola overlapping another at create_scene gets no stock; once moved, it is stocked."""
    objs = [{"id": "g1", "query": "gondola", "position": [1.0, 2.0], "yaw_deg": 0},
            {"id": "g2", "query": "gondola", "position": [1.5, 2.0], "yaw_deg": 0}]
    st = make(tmp_path, objs, "normal", place="convenience_store", w=5.0, d=4.0)
    assert decor_ids(st) == [] and any(i["kind"] == "overlap" for i in st["issues"])
    skipped = {k["host"]: k for k in st["decor_skipped"]}
    assert skipped["g1"]["reason"] == "no_spot" and "overlap" in skipped["g1"]["blockers"][0]
    assert "decor_hint" in st
    hist = (tmp_path / "s" / "preview.json").read_text()
    assert "置き場所なし：overlap" in hist
    st = service.move_object(tmp_path / "s", "g2", position=[3.5, 2.0])
    assert st["issues"] == []
    ids = decor_ids(st)  # each gondola stocked (up to three products)
    assert {"decor_stock_g1", "decor_stock_g2"} <= set(ids) and all(i.startswith(("decor_stock_g1", "decor_stock_g2")) for i in ids)
    assert all(k["reason"] == "no_asset" for k in st.get("decor_skipped", []))  # the fake catalog runs out
    assert f"小物を追加し直し：{len(ids)} 点" in (tmp_path / "s" / "preview.json").read_text()


def test_decor_removed_by_hand_is_not_added_back(tmp_path):
    objs = [{"id": "g1", "query": "gondola", "layout": {"against_wall": "north"}},
            {"id": "g2", "query": "gondola", "position": [1.5, 1.0], "yaw_deg": 0},
            {"id": "g3", "query": "gondola", "position": [2.0, 1.0], "yaw_deg": 0}]
    make(tmp_path, objs, "normal", place="convenience_store", w=5.0, d=4.0)
    d = tmp_path / "s"
    service.remove_object(d, "decor_stock_g1")
    st = service.move_object(d, "g3", position=[4.0, 1.0])
    assert "decor_stock_g1" not in decor_ids(st)  # not blocked, taken out by hand: stays out
    assert {"decor_stock_g2", "decor_stock_g3"} <= set(decor_ids(st))


def test_shop_pegboard_gets_blister_packs(tmp_path, monkeypatch):
    """A home center's pegboard has hooks and no boards: blister packs hang on its free hooks, one product per
    hook row, and its empty_shelves warning goes away."""
    from agentic_sim_world_gen.assets.shelves import hooks_from_boxes

    from test_fill import PEG_BOXES

    peg = rec("peg", "pegboard", 0.9, 0.6, 1.8, shelves=[])
    peg.hooks = hooks_from_boxes(PEG_BOXES)
    monkeypatch.setitem(CATALOG, "pegboard", [peg])
    for q in ("screwdriver", "pliers", "tape measure", "screw"):
        monkeypatch.setitem(CATALOG, f"{q} blister pack", [rec(q.replace(" ", "_"), "blister pack", 0.08, 0.03, 0.2)])
    objs = [{"id": "p", "query": "pegboard", "layout": {"against_wall": "north"}}]
    bare = make(tmp_path, objs, place="home_center", w=4.0, d=3.0, name="bare")
    assert [u["objects"] for u in bare["usability"] if u["kind"] == "empty_shelves"] == [["p"]]
    st = make(tmp_path, objs, "normal", place="home_center", w=4.0, d=3.0)
    ids = decor_ids(st)
    assert len(ids) == 3 and all(i.startswith("decor_hang_p") for i in ids), ids
    s = service.load(tmp_path / "s")
    rows = [{n.split("_")[1] for n, *_ in s.obj(i).fill.slots} for i in ids]
    assert sorted(r for rs in rows for r in rs) == ["h0", "h1", "h2"]  # one product per hook row
    assert all(s.obj(i).fill.hang for i in ids)
    assert st["issues"] == [] and not [u for u in st["usability"] if u["kind"] == "empty_shelves"]


def test_hooks_are_filled_when_the_boards_are_stocked(tmp_path, monkeypatch):
    """A pegboard gondola whose boards the agent stocked still gets packs on its empty hooks."""
    from agentic_sim_world_gen.assets.shelves import hooks_from_boxes

    from test_fill import PEG_BOXES

    peg = rec("peg", "pegboard", 0.9, 0.6, 1.8, shelves=[b for b in SHELVES if b["z"] < 0.6])  # boards below the hooks
    peg.hooks = hooks_from_boxes(PEG_BOXES)
    monkeypatch.setitem(CATALOG, "pegboard", [peg])
    monkeypatch.setitem(CATALOG, "screwdriver blister pack", [rec("sd", "blister pack", 0.08, 0.03, 0.2)])
    objs = [{"id": "p", "query": "pegboard", "layout": {"against_wall": "north"}},
            {"id": "snacks", "query": "snack bag", "fill": {"on": "p"}}]
    st = make(tmp_path, objs, "normal", place="home_center", w=4.0, d=3.0)
    ids = decor_ids(st)
    assert ids and all(i.startswith("decor_hang_p") for i in ids), ids
    assert st["issues"] == [] and not [u for u in st["usability"] if u["kind"] == "empty_shelves"]


def test_a_shelf_added_again_or_swapped_gets_its_stock(tmp_path):
    """A shelf removed and added again, or re-searched to one that holds products, is stocked too
    (the shoe store's east shelf stayed empty and its old skip records stayed)."""
    from agentic_sim_world_gen.scene.spec import DecorSkip

    objs = [{"id": "g1", "query": "gondola", "layout": {"against_wall": "north"}},
            {"id": "g2", "query": "desk", "label": "台", "layout": {"against_wall": "south"}}]
    st = make(tmp_path, objs, "normal", place="convenience_store", w=5.0, d=4.0)
    assert any(i.startswith("decor_stock_g1") for i in decor_ids(st))
    s = service.load(tmp_path / "s")  # an old skip record of g1 goes with it
    s.spec.decor_skipped.append(DecorSkip(label="陳列（g1）", host="g1", reason="no_spot"))
    s.save(tmp_path / "s" / "scene.resolved.json")
    st = service.remove_object(tmp_path / "s", "g1")
    assert decor_ids(st) == [] and not st.get("decor_skipped")
    st = service.add_object(tmp_path / "s", objs[0])
    assert any(i.startswith("decor_stock_g1") for i in decor_ids(st))
    assert "小物を追加" in (tmp_path / "s" / "preview.json").read_text()
    st = service.research_object(tmp_path / "s", "g2", query="gondola", select=True)
    assert any(i.startswith("decor_stock_g2") for i in decor_ids(st))


def test_no_decor_for_added_objects_without_density(tmp_path):
    st = make(tmp_path, [{"id": "g1", "query": "gondola", "layout": {"against_wall": "north"}}], None,
              place="convenience_store", w=5.0, d=4.0)
    st = service.add_object(tmp_path / "s", {"id": "g2", "query": "gondola", "layout": {"against_wall": "south"}})
    assert decor_ids(st) == []
