"""A shop of no kind: its kind from the scene's name, a reason when nothing says what fixtures hold,
hooks hold only the shop's own goods, and a next step for empty shelves / floor."""

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene import decor

from test_density import decor_ids, make, provider  # noqa: F401  (provider: the fake catalog, autouse there)

GONDOLAS = [{"id": "g1", "query": "gondola", "layout": {"against_wall": "north"}},
            {"id": "g2", "query": "gondola", "layout": {"against_wall": "south"}}]


def test_plain_shop_says_why_its_fixtures_are_empty(tmp_path):
    st = make(tmp_path, GONDOLAS, "normal", place="shop", w=5.0, d=4.0)
    assert decor_ids(st) == []
    skipped = {k["host"]: k["reason"] for k in st["decor_skipped"]}
    assert skipped == {"g1": "no_kind", "g2": "no_kind"}
    assert "set_room(place=" in st["decor_kind_hint"]
    assert "業種が分からない" in (tmp_path / "s" / "preview.json").read_text()
    assert any("empty_shelves" in h for h in st["usability_hint"])
    # naming the kind stocks them and the reason goes away
    st = service.set_room(tmp_path / "s", place="convenience_store")
    assert decor_ids(st) and "decor_kind_hint" not in st


def test_plain_shop_kind_from_its_name(tmp_path):
    st = make(tmp_path, GONDOLAS, "normal", place="shop", w=5.0, d=4.0, name="corner_convenience_store")
    s = service.load(tmp_path / "corner_convenience_store")
    assert decor.shop_kind(s) == "convenience_store"
    assert {s.obj(i).query for i in decor_ids(st)} >= {"potato chips bag"}
    assert all(k["reason"] != "no_kind" for k in st.get("decor_skipped", []))  # cracker box: no_asset


def test_hooks_hold_only_the_shops_own_goods():
    assert decor.HANG["convenience_store"] == decor.DEFAULT_HANG
    for place in ("shop", "pet_shop", "bookstore", "bakery", "variety_store"):
        assert not any("usb cable" in q for q, _ in decor.HANG.get(place, ())), place


def test_hooks_in_a_shop_that_hangs_nothing():
    """A pegboard in a bakery: no packs for its hooks, and no division by zero."""
    from agentic_sim_world_gen.assets.record import AssetRecord
    from agentic_sim_world_gen.assets.shelves import hooks_from_boxes
    from agentic_sim_world_gen.scene.spec import ObjectSpec

    from test_fill import PEG_BOXES

    peg = AssetRecord(id="peg", source="fake", uri="https://example.com/peg.usd", name="peg",
                      bbox_min=(-0.45, -0.3, 0.0), bbox_max=(0.45, 0.3, 1.8), up_axis="Z", meters_per_unit=1.0,
                      semantic_class="pegboard", license="CC0-1.0", front_deg=270.0, shelves=[],
                      hooks=hooks_from_boxes(PEG_BOXES))
    host = ObjectSpec(id="p", query="pegboard")
    assert decor._hang_stock(host, peg, "bakery", 0) == []
    assert decor._hang_stock(host, peg, "home_center", 0)


def test_pet_shop_stocks_only_pet_goods(tmp_path, monkeypatch):
    """A plain "toy" match is a marble and "food can" canned beans: not a pet shop's stock."""
    from test_density import CATALOG, rec

    monkeypatch.setitem(CATALOG, "pet food bag", [rec("dog_food_bag", "dog food bag", 0.3, 0.12, 0.4)])
    monkeypatch.setitem(CATALOG, "pet food can", [rec("beans", "food can", 0.08, 0.08, 0.11)])
    monkeypatch.setitem(CATALOG, "pet toy", [rec("marble", "toy", 0.02, 0.02, 0.02)])
    st = make(tmp_path, GONDOLAS[:1], "normal", place="pet_shop", w=5.0, d=4.0)
    s = service.load(tmp_path / "s")
    uris = {s.placements[[p.object_id for p in s.placements].index(i)].asset.name for i in decor_ids(st)}
    assert uris == {"dog_food_bag"}
    assert {k["label"] for k in st["decor_skipped"] if k["reason"] == "no_asset"}
    assert decor.fits_kind("convenience_store", rec("beans", "food can", 0.08, 0.08, 0.11))


def test_toy_store_does_not_stock_beer(tmp_path, monkeypatch):
    """"Toy box" found a six-pack of beer by its tag "box": a toy store stocks toys."""
    from test_density import CATALOG, rec

    beer = rec("beer_can_6pack", "box", 0.2, 0.14, 0.12)
    monkeypatch.setitem(CATALOG, "toy box", [beer])
    monkeypatch.setitem(CATALOG, "toy car", [rec("toy_car_box", "toy car", 0.2, 0.08, 0.1)])
    st = make(tmp_path, GONDOLAS[:1], "normal", place="toy_store", w=5.0, d=4.0)
    s = service.load(tmp_path / "s")
    names = {s.placements[[p.object_id for p in s.placements].index(i)].asset.name for i in decor_ids(st)}
    assert names == {"toy_car_box"}
    assert not decor.fits_kind("toy_store", beer) and decor.fits_kind("liquor_store", rec("b", "beer can", 0.1, 0.1, 0.1))


def test_every_product_names_its_shop_kind():
    """Each product a shop looks for (shelves, cold cases, hooks) says the shop's kind in its query or a synonym,
    so an asset tagged like the query is never left out by KIND_WORDS."""
    from agentic_sim_world_gen.layout.usability import _words

    for kind, need in decor.KIND_WORDS.items():
        by_fixture = [p for _, ps in decor.KIND_BY_FIXTURE.get(kind, ()) for p in ps]
        for q, alt in (*decor.PRODUCTS.get(kind, ()), *decor.COLD_PRODUCTS.get(kind, ()), *decor.HANG.get(kind, ()),
                       *by_fixture):
            assert need & {w for t in (q, *alt) for w in _words(t)}, (kind, q)


def test_shop_kind_fixtures_and_goods(tmp_path):
    """A liquor store's wine rack and crate hold bottles (not cans), a garden center's nursery bench holds
    seedling trays, an electronics store's plain display table gets an open laptop (not the one with phones)."""
    from test_density import CATALOG, SHELVES, rec

    extra = {
        "wine rack": [rec("wine_rack", "wine rack", 0.9, 0.6, 1.4, shelves=SHELVES)],
        "wine crate": [rec("wine_crate", "crate", 0.5, 0.34, 0.3, shelves=SHELVES)],
        "nursery bench": [rec("bench", "bench", 1.8, 0.9, 0.75, shelves=SHELVES)],
        "display table": [rec("dtable", "display table", 1.6, 0.8, 0.9)],
        "device display table": [rec("devtable", "display table", 1.6, 0.8, 1.04)],
        "shoe bench": [rec("sbench", "bench", 1.2, 0.4, 0.45, shelves=SHELVES)],
    }
    cat = {**CATALOG, **extra}
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: cat.get(q, [])})())

    def items(objs, place):
        make(tmp_path, objs, None, place=place, w=7.0, d=5.0, name=place)
        s = service.load(tmp_path / place)
        out: dict[str, set[str]] = {}
        for i in decor.plan(s, "normal"):
            out.setdefault(i.host, set()).update(t.query for t in i.tries)
        return out

    got = items([{"id": "rack", "query": "wine rack", "layout": {"against_wall": "north"}},
                 {"id": "crate", "query": "wine crate", "layout": {"center": True}}], "liquor_store")
    assert got["rack"] == got["crate"] == {"wine bottle", "sake bottle", "beer bottle"}
    assert ("beer bottle", ("bottled beer",)) in decor.PRODUCTS["liquor_store"]
    got = items([{"id": "bench", "query": "nursery bench", "layout": {"center": True}}], "garden_center")
    assert got["bench"] == {"seedling tray", "potted plant"}
    got = items([{"id": "bench", "query": "shoe bench", "layout": {"center": True}}], "shoe_store")
    assert "bench" not in got  # a seat
    got = items([{"id": "t1", "query": "display table", "layout": {"against_wall": "north"}},
                 {"id": "t2", "query": "device display table", "layout": {"against_wall": "south"}}],
                "electronics_store")
    assert got.get("t1") == {"open laptop"} and "t2" not in got
    assert "product box" not in {q for q, _ in decor.PRODUCTS["electronics_store"]}
    drinks = rec("drink_cardboard_case_24", "cardboard box", 0.4, 0.27, 0.24)
    assert not decor.fits_kind("electronics_store", drinks)
    head = rec("headphones_box", "box", 0.2, 0.08, 0.22)
    head.tags = ["headphones box", "electronics"]
    assert decor.fits_kind("electronics_store", head)


def test_remote_finds_room_beside_the_magazine_and_fruit_bowl():
    """The remote had two spots, both taken on a low table holding a magazine and a fruit bowl."""
    remote = next(d for d in decor.TABLE if d.key == "remote")
    assert len(remote.spots) >= 4


def test_goods_stock_a_shop_of_another_kind(tmp_path, monkeypatch):
    """A sporting goods store is not in the place list; Room.goods says what its fixtures hold, and only
    assets naming those words are stocked (a box of beer tagged "box" is not a racket)."""
    from test_density import CATALOG, rec

    monkeypatch.setitem(CATALOG, "soccer ball", [rec("soccer_ball", "soccer ball", 0.22, 0.22, 0.22)])
    monkeypatch.setitem(CATALOG, "tennis racket box", [rec("beer_box", "box", 0.2, 0.14, 0.12)])
    st = make(tmp_path, GONDOLAS, "normal", place="shop", w=5.0, d=4.0)
    assert {k["reason"] for k in st["decor_skipped"]} == {"no_kind"} and "goods=" in st["decor_kind_hint"]
    st = service.set_room(tmp_path / "s", goods=["soccer ball", "tennis racket box"], goods_words=["sports"])
    s = service.load(tmp_path / "s")
    names = {s.placements[[p.object_id for p in s.placements].index(i)].asset.name for i in decor_ids(st)}
    assert names == {"soccer_ball"}
    assert "decor_kind_hint" not in st and all(k["reason"] != "no_kind" for k in st["decor_skipped"])
    assert "tennis racket box" in st["decor_goods_hint"] and "soccer ball," not in st["decor_goods_hint"]
    assert {"soccer", "ball", "racket", "sport"} <= decor.goods_words(s.spec.room)
    assert not {"box", "store"} & decor.goods_words(s.spec.room)
    # removing them: back to a shop of no kind
    st = service.set_room(tmp_path / "s", goods=[])
    assert decor_ids(st) == [] and "decor_goods_hint" not in st


def test_goods_need_their_qualifier(tmp_path, monkeypatch):
    """"Soccer ball" does not stock a pie weight tagged "ball"; a ball tagged "sports" (the kind) is stocked."""
    from dataclasses import replace

    from test_density import CATALOG, rec

    pie = replace(rec("pie_weight", "ball", 0.01, 0.01, 0.01), tags=["ball", "baking"])
    monkeypatch.setitem(CATALOG, "soccer ball", [pie])
    make(tmp_path, GONDOLAS, "sparse", place="shop", w=5.0, d=4.0)
    st = service.set_room(tmp_path / "s", goods=["soccer ball"], goods_words=["sports"], density="normal")
    assert decor_ids(st) == [] and "soccer ball" in st["decor_goods_hint"]
    ball = replace(rec("futsal_ball", "ball", 0.2, 0.2, 0.2), tags=["ball", "sports"])
    monkeypatch.setitem(CATALOG, "soccer ball", [pie, ball])
    st = service.set_room(tmp_path / "s", goods=["soccer ball", "basketball"])
    s = service.load(tmp_path / "s")
    names = {s.placements[[p.object_id for p in s.placements].index(i)].asset.name for i in decor_ids(st)}
    assert names == {"futsal_ball"}
    room = s.spec.room
    assert not decor.fits_goods("basketball", room, pie)  # "ball" of another query is not enough
    assert decor.fits_goods("basketball", room, ball)  # the kind ("sports")
    sign = replace(rec("exit_sign", "sign", 0.3, 0.02, 0.15), tags=["running", "man"])
    assert not decor.fits_goods("running shoes", room, sign)  # the qualifier without the head noun
    assert not decor.fits_goods("tennis racket box", room.model_copy(update={"goods_words": []}), pie)


def test_goods_make_a_shop():
    from agentic_sim_world_gen.scene.spec import Room

    assert Room(width=5, depth=4, goods=["dango"]).place == "shop"
    assert Room(width=5, depth=4, goods=["dango"], place="bakery").place == "bakery"
