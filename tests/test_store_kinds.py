"""More kinds of shop: florist, greengrocer, toy store, pet shop, garden center, shoe store, optician,
liquor store. Each is a shop with its own words, sizes, goods and look."""

from __future__ import annotations

import pytest

from agentic_sim_world_gen.layout import usability
from agentic_sim_world_gen.scene import decor
from agentic_sim_world_gen.scene.looks import ceiling_surface
from agentic_sim_world_gen.scene.questions import STORE_SIZES, TABLE, guess_place, plan_questions
from agentic_sim_world_gen.scene.spec import STORE_PLACES, ObjectSpec, Room

NEW = ("florist", "greengrocer", "toy_store", "pet_shop", "garden_center", "shoe_store", "optician", "liquor_store")


@pytest.mark.parametrize("text,place", [
    ("駅前の花屋を作って", "florist"), ("八百屋の店先", "greengrocer"), ("おもちゃ屋を作って", "toy_store"),
    ("ペットショップ", "pet_shop"), ("園芸店の売り場", "garden_center"), ("靴屋を作って", "shoe_store"),
    ("眼鏡店を作って", "optician"), ("町の酒屋", "liquor_store"), ("居酒屋を作って", "restaurant"),
    ("ホームセンターの園芸用品売り場", "home_center"), ("a small flower shop", "florist"),
])
def test_guess_place(text, place):
    assert guess_place(text) == place


def test_places_have_sizes_questions_and_goods():
    for p in NEW:
        assert p in STORE_PLACES and Room(place=p, width=6, depth=5).place == p
        assert p in STORE_SIZES and TABLE[p][0].key == "size", p
        assert decor.PRODUCTS[p], p
    out = plan_questions("酒屋を作って", limit=4)
    assert "cold_cases" in [q["key"] for q in out["questions"]]
    assert "cold_cases" not in [q.key for q in TABLE["shoe_store"]]


def test_goods_by_place():
    shelf = ObjectSpec(id="g1", query="gondola")
    cooler = ObjectSpec(id="c1", query="reach-in cooler")
    assert decor._products("liquor_store", shelf, {"gondola"})[0][0] == "sake bottle"
    assert decor._products("liquor_store", cooler, {"cooler"})[0][0] == "beer can"  # not soft drinks
    assert decor._products("convenience_store", cooler, {"cooler"}) == decor.DRINK
    assert decor._products("greengrocer", shelf, {"gondola"})[0][0] == "apple"
    # nothing hangs in a florist; a toy store's hooks hold toys first
    assert decor.HANG["florist"] == () and decor.HANG["toy_store"][0][0] == "toy blister pack"


def test_look_aisles_and_domains():
    from agentic_sim_world_gen.assets.domain import FOREIGN

    assert ceiling_surface(True, "office", "florist")["name"] == "ceiling_board"
    assert ceiling_surface(True, "office", "garden_center")["name"] == "metal_deck"
    assert usability.AISLE_WIDTH["garden_center"] == 1.2 and usability.AISLE_WIDTH["optician"] == 1.0
    # plants and growing gear are not pushed back in a garden center or florist
    assert "agriculture" not in FOREIGN["garden_center"] and "agriculture" not in FOREIGN["florist"]
    assert "agriculture" in FOREIGN["shoe_store"]


# bicycle shop, wagashi shop, record store, sporting goods, cosmetics, furniture store, asian grocery
NEW2 = ("bicycle_shop", "wagashi_shop", "record_store", "sporting_goods_store", "cosmetics_store", "furniture_store",
        "asian_grocery")


@pytest.mark.parametrize("text,place", [
    ("駅前の自転車店", "bicycle_shop"), ("老舗の和菓子屋を作って", "wagashi_shop"), ("中古レコード店", "record_store"),
    ("スポーツ用品店を作って", "sporting_goods_store"), ("化粧品店", "cosmetics_store"),
    ("家具店のショールーム", "furniture_store"), ("アジア食材店", "asian_grocery"), ("アジアンスーパー", "asian_grocery"),
    ("ドラッグストアの化粧品売り場", "drugstore"), ("店の前に自転車を並べた花屋", "florist"),
    ("a small record shop", "record_store"), ("bike shop", "bicycle_shop"),
])
def test_guess_more_places(text, place):
    assert guess_place(text) == place


def test_more_places_have_sizes_and_goods():
    for p in NEW2:
        assert p in STORE_PLACES and Room(place=p, width=6, depth=5).place == p
        assert p in STORE_SIZES and TABLE[p][0].key == "size", p
        assert decor.PRODUCTS[p], p
    assert "cold_cases" in [q.key for q in TABLE["asian_grocery"]]
    assert "cold_cases" not in [q.key for q in TABLE["record_store"]]


def test_shop_goods_and_foreign_kinds():
    from agentic_sim_world_gen.assets.domain import FOREIGN
    from agentic_sim_world_gen.assets.record import AssetRecord

    case = ObjectSpec(id="s1", query="wagashi showcase")
    assert decor._products("wagashi_shop", case, {"showcase"})[0][0] == "dango"
    assert decor._products("record_store", ObjectSpec(id="b1", query="record bin"), {"bin"})[0][0] == "lp record"
    assert decor._products("sporting_goods_store", ObjectSpec(id="g1", query="gondola"), {"gondola"})[0][0] == "soccer ball"

    def rec(name, caption):
        return AssetRecord(id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name, caption=caption,
                           bbox_min=(0, 0, 0), bbox_max=(0.2, 0.2, 0.2))

    # no bare "ball" or "oil": pie weights and motor oil are not a sports shop's or an asian grocery's stock
    assert decor.fits_kind("sporting_goods_store", rec("soccer_ball", "soccer ball"))
    assert not decor.fits_kind("sporting_goods_store", rec("pie_weights", "ceramic baking ball"))
    assert not decor.fits_kind("asian_grocery", rec("motor_oil", "motor oil bottle"))
    assert ceiling_surface(True, "office", "wagashi_shop")["name"] == "ceiling_board"
    assert usability.AISLE_WIDTH["furniture_store"] == 1.2
    assert "automotive" not in FOREIGN["bicycle_shop"]


# dispensing pharmacy (調剤薬局): waiting room at the entrance, dispensary at the back
@pytest.mark.parametrize("text,place", [
    ("調剤薬局を作って", "dispensing_pharmacy"), ("処方せんを受け付ける薬局", "dispensing_pharmacy"),
    ("門前の保険薬局", "dispensing_pharmacy"), ("a small dispensing pharmacy", "dispensing_pharmacy"),
    ("ドラッグストアを作って", "drugstore"), ("駅前の薬局", "drugstore"), ("pharmacy", "drugstore"),
])
def test_guess_place_dispensing_pharmacy(text, place):
    assert guess_place(text) == place


def test_dispensing_pharmacy():
    from agentic_sim_world_gen.assets.domain import FOREIGN
    from agentic_sim_world_gen.assets.record import AssetRecord
    from agentic_sim_world_gen.scene.fixtures import PLACE_KINDS

    p = "dispensing_pharmacy"
    assert p in STORE_PLACES and Room(place=p, width=8, depth=6).place == p
    assert STORE_SIZES[p] == ("標準（8 × 6 m）", "小さめ（6 × 5 m）", "大きめ（12 × 8 m）")
    assert TABLE[p][0].key == "size" and "cold_cases" not in [q.key for q in TABLE[p]]
    assert PLACE_KINDS[p] == "line" and usability.AISLE_WIDTH[p] == 1.0
    assert "medical" not in FOREIGN[p] and "industrial" in FOREIGN[p]
    assert decor.HANG[p] == ()

    def first(query, heads):
        return decor._products(p, ObjectSpec(id="f1", query=query), heads)[0][0]

    assert first("picking shelf rack", {"rack"}) == "ptp medicine box"
    assert first("powder dispensing table", {"table"}) == "powder medicine bottle"
    assert first("dispensing sink", {"sink"}) == "syrup dosing bottle"
    assert first("medicine cart", {"cart"}) == "prescription medicine bag"
    assert first("pamphlet rack", {"rack"}) == "medication notebook"
    assert first("medicine refrigerator", {"refrigerator"}) == "ptp medicine box"  # not soft drinks
    assert {"table", "sink", "cart"} <= decor.fixture_heads(p)

    def rec(name, caption, tags=()):
        return AssetRecord(id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name, caption=caption,
                           tags=list(tags), bbox_min=(0, 0, 0), bbox_max=(0.2, 0.2, 0.2))

    assert decor.fits_kind(p, rec("ptp_medicine_box", "tablet box", ("pharmacy", "prescription drug")))
    assert decor.fits_kind(p, rec("syrup_dosing_bottle", "dosing bottle", ("pharmacy supplies",)))
    assert not decor.fits_kind(p, rec("shampoo_bottle", "shampoo pump bottle"))
    assert not decor.fits_kind(p, rec("baby_powder", "baby powder bottle"))


def test_pharmacy_work_places_are_not_display_shelves(tmp_path):
    """A packaging machine and a counseling counter have shelf boards but are work places: no empty_shelves."""
    from test_density import SHELVES, rec

    from agentic_sim_world_gen import service
    from agentic_sim_world_gen.layout.usability import shelf_fixtures

    cat = {q: [rec(q.replace(" ", "_"), q, 1.0, 0.6, 1.2, shelves=SHELVES)]
           for q in ("tablet packaging machine", "counseling counter", "picking shelf rack")}
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: cat.get(q, [])})())
    try:
        objects = [{"id": "packager", "query": "tablet packaging machine", "layout": {"against_wall": "north", "along": 0.0}},
                   {"id": "counter", "query": "counseling counter", "position": [2.0, 1.5]},
                   {"id": "shelf", "query": "picking shelf rack", "layout": {"against_wall": "north", "along": 1.0}}]
        service.create_scene({"name": "rx", "room": {"width": 4.0, "depth": 3.0, "place": "dispensing_pharmacy"},
                              "objects": objects}, tmp_path / "rx")
        assert shelf_fixtures(service.load(tmp_path / "rx")) == {"shelf"}
    finally:
        service.set_provider(None)
