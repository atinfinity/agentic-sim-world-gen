"""The kind table: does any source have this kind of object at all."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets import kinds
from agentic_sim_world_gen.assets.providers.composite import CompositeProvider
from agentic_sim_world_gen.assets.record import AssetRecord


def rec(name, cls, caption="", tags=(), size=(0.4, 0.4, 0.5)):
    w, d, h = size
    return AssetRecord(id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
                       bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), semantic_class=cls,
                       caption=caption, tags=list(tags), license="CC-BY-4.0")


DESK_LAMP = rec("desk_lamp", "desk lamp", "Silver desk lamp with a flexible arm", ["desk lamp", "task light"])
LAMP = rec("lamp_a", "lamp", "Black metal lamp", ["lamp", "lighting"])
COFFEE_TABLE = rec("coffee_table", "coffee table", "Low wooden coffee table", ["coffee table", "living room"])
BEER = rec("beer_6", "beer can", "Six pack of beer cans", ["beer can 6 pack", "beverage"])
CHAIR = rec("chair", "chair", "Plastic stacking chair", ["stacking chair", "chair"], (0.5, 0.5, 0.8))


def test_kind_words():
    assert kinds.kind("Floor Lamps") == "floor lamp"
    assert kinds.kind("sofa 3 seat") == "sofa"
    assert kinds.kind("beer can pack of 6") == "beer can"


def test_candidate_kinds():
    heads, comps = kinds.candidate_kinds(DESK_LAMP)
    assert {"desk", "lamp", "light"} <= heads and {"desk lamp", "task light"} <= comps
    assert "flexible arm" not in comps  # captions add nouns, not compounds


@pytest.fixture
def table():
    return kinds.build({"simready-central": [DESK_LAMP, LAMP, COFFEE_TABLE, BEER]},
                       ["floor lamp", "whiteboard", "dining table"], built="2026-11-01")


def test_build(table):
    t = table
    assert t["version"] == kinds.VERSION and t["built"] == "2026-11-01"
    assert t["sources"]["simready-central"]["records"] == 4
    assert t["kinds"]["lamp"] == {"simready-central": 2}
    assert t["kinds"]["desk lamp"] == {"simready-central": 2}  # the bare "lamp" class names any lamp
    assert t["plain"]["lamp"] == {"simready-central": 1}  # ... but a desk lamp is not a floor lamp
    assert t["kinds"]["floor lamp"] == {"simready-central": 1}  # listed: scored exactly
    assert t["kinds"]["whiteboard"] == {} and t["kinds"]["dining table"] == {}  # listed and absent
    assert t["kinds"]["beer can"] == {"simready-central": 1} and "6pack" not in " ".join(t["kinds"])


def test_lookup(table):
    assert kinds.lookup("whiteboard", table) == ("whiteboard", {})
    assert kinds.lookup("magnetic whiteboard", table) == ("magnetic whiteboard", {})
    assert kinds.lookup("black floor lamp", table) == ("floor lamp", {"simready-central": 1})
    assert kinds.lookup("reading lamp", table) == ("reading lamp", {"simready-central": 1})  # not listed: plain
    assert kinds.lookup("side table", table) == ("side table", {})  # only a coffee table: another kind
    assert kinds.lookup("6 pack beer can", table) == ("beer can", {"simready-central": 1})
    assert kinds.lookup("zebra", table) == ("zebra", {})
    assert kinds.other_kinds("dining table", table) == [{"kind": "coffee table", "assets": 1}]
    assert kinds.other_kinds("floor lamp", table) == [{"kind": "desk lamp", "assets": 2}]


def test_write_and_load(table, tmp_path):
    p = tmp_path / "k.json.gz"
    kinds.write(table, p)
    assert kinds.load(p)["kinds"]["lamp"] == table["kinds"]["lamp"]
    assert kinds.load(tmp_path / "missing.json.gz") is None


class Remote:  # a source known only through the table
    name = "simready-central"

    def search(self, query, *, limit=10, size=None):
        return []


class Local:  # a source with its records at hand (Isaac index, local: folder)
    def __init__(self, name, recs):
        self.name, self.recs = name, recs

    def records(self):
        return self.recs

    def search(self, query, *, limit=10, size=None):
        return self.recs[:limit]


def Both(*ps):
    return CompositeProvider(list(ps))


def test_availability(table):
    both = Both(Remote(), Local("local:mine", [CHAIR]))
    a = kinds.availability(["whiteboard", "dry erase board"], both, table)
    assert a["absent"] and a["counts"] == {"simready-central": 0, "local:mine": 0}
    assert a["kind"] == "whiteboard" and a["table"]["built"] == "2026-11-01"
    a = kinds.availability(["stool", "chair"], both, table)  # any query of the object counts
    assert not a["absent"] and a["counts"]["local:mine"] == 1
    a = kinds.availability(["floor lamp"], Remote(), table)
    assert a["counts"] == {"simready-central": 1} and a["other_kinds"][0]["kind"] == "desk lamp"
    # a source neither in the table nor with local records: never claim absent
    unknown = Both(Remote(), type("Fake", (), {"name": "fake"})())
    a = kinds.availability(["whiteboard"], unknown, table)
    assert not a["absent"] and a["unknown_sources"] == ["fake"]
    assert kinds.availability(["whiteboard"], type("Fake", (), {"name": "fake"})(), table) is None
    assert kinds.availability([""], Remote(), table) is None


def test_shipped_table():
    t = kinds.load()
    assert t is not None and t["built"] and {"simready-central", "isaac"} <= set(t["sources"])
    assert t["sources"]["simready-central"]["records"] > 3000
    assert not t["kinds"]["wheelchair"]  # absent from both catalogs
    assert t["kinds"]["pallet"]["simready-central"] > 10


# --- the first status says so -----------------------------------------------------------------------------


@pytest.fixture
def chairs_only(tmp_path, table, monkeypatch):
    service.set_provider(Both(Remote(), Local("local:mine", [CHAIR])))
    monkeypatch.setattr(kinds, "load", lambda path=None: table)
    yield tmp_path
    service.set_provider(None)


def _obj(st, oid):
    return next(o for o in st["objects"] if o["id"] == oid)


def test_status_not_in_catalog(chairs_only):
    st = service.create_scene({"name": "t", "room": {"width": 4, "depth": 3, "height": 2.4}, "objects": [
        {"id": "board", "query": "whiteboard", "alt_queries": ["dry erase board"]},
        {"id": "lamp", "query": "floor lamp"},
        {"id": "chair", "query": "chair"},
    ]}, chairs_only / "s")
    sub = _obj(st, "board")["substitute"]
    assert sub["not_in_catalog"] and len(sub["choices"]) == 3
    assert "Do not search again" in sub["hint"] and "2026-11-01" in sub["hint"]
    assert sub["catalog"]["kind"] == "whiteboard"
    # the table has floor lamps that this search did not find: one more try, not a dead end
    lamp = _obj(st, "lamp")["substitute"]
    assert "not_in_catalog" not in lamp and "simready-central 1" in lamp["hint"]
    assert "substitute" not in _obj(st, "chair")
    r = service.research_object(chairs_only / "s", "board", query="marker board")
    assert "Do not search again" in r["research"]["not_in_catalog"]
    assert "substitute" in service.brief(r)["objects"][0]  # kept in the brief result


def test_status_unknown_source_keeps_old_hint(tmp_path, table, monkeypatch):
    class Fake:
        name = "fake"

        def search(self, query, *, limit=10, size=None):
            return [CHAIR]

    service.set_provider(Fake())
    monkeypatch.setattr(kinds, "load", lambda path=None: table)
    try:
        st = service.create_scene({"name": "t", "room": {"width": 4, "depth": 3, "height": 2.4},
                                   "objects": [{"id": "board", "query": "whiteboard"}]}, tmp_path / "s")
    finally:
        service.set_provider(None)
    sub = _obj(st, "board")["substitute"]
    assert "not_in_catalog" not in sub and "Try other synonyms" in sub["hint"] and "catalog" not in sub
