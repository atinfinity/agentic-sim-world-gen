"""Re-ranking by metadata match and multi-query merge (no network)."""

import asyncio

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.assets.rerank import rerank, score, words


def rec(name, cls=None, tags=(), caption="", size=(1.2, 0.6, 0.75), thumb=None):
    w, d, h = size
    return AssetRecord(
        id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
        semantic_class=cls, tags=list(tags), caption=caption, license="CC-BY 4.0", thumbnail_url=thumb,
    )


SOFA = rec("sm_sofa", "couch", ["sofa", "living room"], "A grey fabric sofa.")
PALLET = rec("sm_pallet", "pallet", ["wooden pallet"], "A wooden pallet.")
DESK = rec("sm_desk_a01", "desk", ["desk", "office desk", "standing desk"], "A white office desk.")
TABLE = rec("sm_table", "table", ["table"], "A table that could serve as a desk.")
SHELF = rec("sm_cabinet_open", "cabinet", ["bookcase", "open shelves"], "An open shelving unit.")


def test_words_singularize():
    assert words("Bookshelves, Shelves & tables") == ["bookshelf", "shelf", "table"]


def test_rerank_puts_desk_first():
    ranked = rerank([SOFA, PALLET, TABLE, DESK], "office desk")
    assert ranked[0][0].name == "sm_desk_a01"
    m = dict((r.name, m) for r, m in ranked)
    assert m["sm_desk_a01"].score >= 3 and not m["sm_desk_a01"].weak
    assert m["sm_sofa"].weak and m["sm_pallet"].weak
    assert m["sm_table"].weak  # "a table that could serve as a desk" is a table


def test_alt_queries_rescue_synonym():
    assert score(SHELF, "bookshelf").weak
    assert not score(SHELF, "bookshelf", ["bookcase"]).weak


DESK_LAMP = rec("sm_lamp_desk", "desk lamp", ["desk lamp", "office"], "Black articulated desk lamp with round base.")
DESK_CHAIR = rec("sm_chair_office", "office chair", ["desk chair", "swivel"], "Black office chair for a desk.")
WHITE_DESK = rec("sm_desk_b", None, [], "White height-adjustable desk with gray metal T-legs")


def test_modifier_is_not_the_object():
    # "desk" in "desk lamp" / "desk chair" is a modifier: those are not desks
    assert score(DESK_LAMP, "office desk").weak
    assert score(DESK_CHAIR, "office desk", ["writing desk"]).weak
    assert not score(WHITE_DESK, "office desk").weak  # "desk with ..." names a desk
    assert not score(DESK_LAMP, "desk lamp").weak
    assert not score(rec("r", "rack with shelves"), "shelf").weak
    assert score(rec("u", "waste container", ["under desk"]), "desk").weak  # where it goes, not what it is


def test_other_kind_of_head_is_weak():
    coffee = rec("c", "coffee table")
    assert score(coffee, "dining table").weak
    assert not score(coffee, "table").weak
    assert not score(rec("t", "table"), "dining table").weak  # plain table: no conflicting modifier
    assert not score(rec("d", "dining table"), "dining table").weak
    assert score(rec("m", "recovery machine", ["machine"]), "vending machine").weak


class MultiProvider:
    name = "fake"

    def __init__(self):
        self.queries = []

    def search(self, query, *, limit=10, size=None):
        self.queries.append(query)
        return {"bookshelf": [SOFA, DESK], "bookcase": [SHELF, SOFA]}.get(query, [])[:limit]


def test_find_candidates_merges_queries(tmp_path):
    p = MultiProvider()
    service.set_provider(p)
    try:
        st = service.create_scene(
            {"name": "s", "room": {"width": 3, "depth": 3},
             "objects": [{"id": "shelf", "query": "bookshelf", "alt_queries": ["bookcase"], "position": [1, 1]}]},
            tmp_path / "s",
        )
        cands = st["objects"][0]["candidates"]
        assert p.queries == ["bookshelf", "bookcase"]
        # deduped; sofa and desk are weak and dropped because a good match exists
        assert [c["name"] for c in cands] == ["sm_cabinet_open"]
        assert cands[0]["match"]["weak"] is False and "substitute" not in st["objects"][0]
        # the stored "match" key must not break loading the selected asset
        assert service.load(tmp_path / "s").placements[0].asset.name == "sm_cabinet_open"
    finally:
        service.set_provider(None)


def test_view_candidates(tmp_path, monkeypatch):
    import requests

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 16

    class Resp:
        ok = True
        headers = {"content-type": "image/png"}
        content = png

    monkeypatch.setattr(requests, "get", lambda url, timeout, headers=None: Resp())
    DESK.thumbnail_url = "https://example.com/desk.png"
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: [DESK, WHITE_DESK]})())
    try:
        d = tmp_path / "v"
        service.create_scene({"name": "v", "room": {"width": 3, "depth": 3},
                              "objects": [{"id": "desk", "query": "desk", "position": [1, 1]}]}, d)
        from agentic_sim_world_gen.mcp_server import mcp

        res = asyncio.run(mcp.call_tool("view_candidates", {"scene_dir": str(d), "object_id": "desk"}))
        text = str(res)
        assert "image" in text and "sm_desk_a01" in text and "(no thumbnail)" in text
    finally:
        DESK.thumbnail_url = None
        service.set_provider(None)


def test_class_head_beats_compound():
    jack = rec("sm_palletJack_a01", "pallet jack", ["pallet jack", "pallet truck"])
    ranked = [r.name for r, _ in rerank([jack, PALLET], "pallet")]
    assert ranked[0] == "sm_pallet"


def test_name_camel_case_and_folder_phrase():
    klt = rec("small_KLT", None, ["props", "klt bin"])
    bin_ = rec("sm_recycling_bin", "recycling bin", ["recycling bin", "waste bin"])
    assert rerank([bin_, klt], "KLT bin")[0][0].name == "small_KLT"


def test_compound_tail_beats_bare_head():
    # local signage has a generic class; its tag "digital signage" names the query's compound noun
    retail = rec("digital_signage_ceiling_3x", "fixture", ["fixture", "digital signage", "display"])
    board = rec("sm_sign_hospital_board", "signage", ["sign"])
    m = score(retail, "ceiling hung digital signage")
    assert m.score > score(board, "ceiling hung digital signage").score and not m.weak
    assert rerank([board, retail], "ceiling hung digital signage")[0][0].name == retail.name


class SofaOnly:
    name = "fake"

    def search(self, query, *, limit=10, size=None):
        return [SOFA, PALLET][:limit]


def test_all_weak_is_substitute(tmp_path):
    from agentic_sim_world_gen.preview.candidates import render_html

    service.set_provider(SofaOnly())
    try:
        st = service.create_scene(
            {"name": "s", "room": {"width": 3, "depth": 3},
             "objects": [{"id": "shelf", "query": "bookshelf", "position": [1, 1]}]},
            tmp_path / "s",
        )
        o = st["objects"][0]
        assert [c["name"] for c in o["candidates"]] == ["sm_sofa", "sm_pallet"]  # kept as substitutes
        assert "substitute" in o
        assert "代用" in render_html(service.load(tmp_path / "s"))
        assert "該当なし" in (tmp_path / "s" / "preview.html").read_text(encoding="utf-8")
    finally:
        service.set_provider(None)


SEAT = rec("sm_car_seat", "seat", ["car seat", "seat"], "A car seat.")
PACK = rec("sm_backpack", "pack", ["pack", "backpack"], "A hiking pack.")
BOTTLE = rec("sm_water_bottle", "bottle", ["water bottle", "bottle"], "A plastic water bottle.", size=(0.07, 0.07, 0.22))


def test_count_and_unit_is_not_the_head_noun():
    # "3 seat" / "3 pack" say how many, not what it is: the sofa and the bottle must come first
    for q in ("sofa 3 seat", "sofa 3-seater", "3 seat sofa", "sofa three seater", "sofa x3"):
        ranked = rerank([SEAT, PACK, SOFA], q)
        assert ranked[0][0].name == "sm_sofa", q
        assert not ranked[0][1].weak and ranked[1][1].weak, q
    for q in ("water bottle 3 pack", "water bottle pack of 6", "water bottle 6pk", "water bottle set of 4"):
        ranked = rerank([PACK, SEAT, BOTTLE], q)
        assert ranked[0][0].name == "sm_water_bottle", q
        assert ranked[0][1].score >= 3, q  # "water bottle" still matches as a whole
    assert rerank([PALLET, SEAT], "2 seats")[0][0].name == "sm_car_seat"  # a count alone keeps its noun


def test_noun_last():
    from agentic_sim_world_gen.assets.rerank import noun_last

    assert noun_last(words("water bottle pack of 6")) == ["6", "pack", "water", "bottle"]
    assert noun_last(words("pet bottle drink 600ml")) == ["600ml", "pet", "bottle", "drink"]
    assert noun_last(words("chairs set of 4")) == ["4", "set", "chair"]
    assert noun_last(words("2 chairs")) == ["2", "chair"]
    assert noun_last(words("desk lamp")) == ["desk", "lamp"]

    # Join=True writes each quantity as one token, however it was spelled
    for t in ("beer can 6pack", "beer can 6 pack", "beer can six pack", "beer can pack of 6", "beer can 6pk"):
        assert noun_last(words(t), join=True) == ["6pack", "beer", "can"], t
    assert noun_last(words("2 chairs"), join=True) == ["2", "chair"]


def test_quantity_in_tags_matches_the_query():
    # Local tags keep the quantity last ("beer can 6pack"); the query is reordered, so the tag must be too
    six = rec("beer_can_6pack", "beverage", ["beverage", "beer can 6pack", "beer", "six pack"])
    can = rec("beer_can_350ml", "beverage", ["beverage", "beer can 350ml", "beer"])
    miso = rec("miso_cup_750g", "food", ["food", "miso cup 750g", "miso", "miso paste"])
    lid = rec("paper_cup_lid", "beverage", ["beverage", "paper cup lid", "cup"])
    for q in ("beer can 6pack", "beer can 6 pack", "beer 6 pack", "beer six pack"):
        assert rerank([can, six], q)[0][0].name == "beer_can_6pack", q
    assert rerank([can, six], "beer can 350ml")[0][0].name == "beer_can_350ml"
    assert rerank([lid, miso], "miso cup 750g")[0][0].name == "miso_cup_750g"


def test_usability_kind_skips_count():
    from agentic_sim_world_gen.layout.usability import _words
    from agentic_sim_world_gen.preferences import kind

    assert kind("sofa 3 seat") == "sofa" and kind("office chairs") == "chair"
    assert _words("pet bottle drink 600ml")[-1] == "drink"


def test_a_thing_with_a_lamp_is_no_floor_lamp():
    """Class "lamp" alone said the UV-C robot, the table lamp and the ceiling light were floor lamps."""
    robot = rec("uvc_robot", "lamp", ["uv disinfection robot", "sterilization robot"],
                "White mobile UV-C disinfection robot with vertical lamp tower")
    table = rec("bedside", "lamp", ["table lamp", "bedside lamp", "lamps"], "Glossy white bedside table lamp")
    ceiling = rec("ceiling", "lamp", ["ceiling light", "ceiling lamp"], "Square metal ceiling light with rods")
    floor = rec("floor", "lamp", ["standing lamp", "reading lamp"], "Brass standing lamp with linen shade")
    plain = rec("plain", "lamp", ["lighting"], "Black metal lamp")
    alt = ["standing lamp", "lamp"]
    by = {r.name: m for r, m in rerank([robot, table, ceiling, floor, plain], "floor lamp", alt)}
    assert by["uvc_robot"].weak and "robot with a lamp" in by["uvc_robot"].matched[0]
    assert by["bedside"].weak and by["ceiling"].weak  # the bare synonym "lamp" does not let them back in
    assert not by["floor"].weak and not by["plain"].weak  # no tag says another kind
    assert not score(table, "lamp").weak  # asked for any lamp, a table lamp is one
    six = rec("beer_6", "beer can", ["beer can 6 pack"], "Six pack of beer cans")
    assert not score(six, "can").weak  # "pack of cans" is cans, not a pack with a can


def test_material_and_color_are_no_kind():
    """A "metal locker" query lost every locker once "metal" counted as a kind of locker."""
    tagged = rec("locker", "locker", ["storage locker", "gym locker"], "Gray four-door metal locker with bench")
    cabinet = rec("cab", "storage cabinet locker", ["locker"], "Gray steel cabinet locker")
    assert not score(tagged, "metal locker").weak
    assert not score(cabinet, "metal locker").weak
    assert not score(PALLET, "slatted pallet").weak  # a "wooden pallet" is no other kind of pallet
    lamp = rec("bedside", "lamp", ["table lamp"], "White bedside table lamp")
    assert score(lamp, "white floor lamp").weak  # the kind word still counts
