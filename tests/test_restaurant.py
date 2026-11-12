"""Restaurants : the questions and the tabletop set added by density."""

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene.questions import plan_questions

import test_density
from test_density import SHELVES, decor_ids, make, rec

test_density.CATALOG.update({
    "family table": [rec("ftable", "table", 1.2, 0.75, 0.7)],
    "steel work table": [rec("wtable", "work table", 1.2, 0.6, 0.8)],
    "kitchen rack": [rec("krack", "rack", 0.94, 0.44, 1.4, shelves=SHELVES)],
    "condiment set": [rec("condiments", "condiment set", 0.2, 0.1, 0.16)],
    "call bell": [rec("bell", "call bell", 0.08, 0.06, 0.05)],
    "napkin holder": [rec("napkins", "napkin holder", 0.12, 0.08, 0.13)],
    "menu stand": [rec("menu", "menu stand", 0.24, 0.12, 0.3)],
    "water glass": [rec("glass", "glass", 0.075, 0.075, 0.11)],
    "dinner plate": [rec("plate", "plate", 0.26, 0.26, 0.03)],
})
from test_density import provider  # noqa: E402,F401  (fixture: fake asset search)

DINING = [{"id": "t1", "label": "テーブル", "query": "family table", "layout": {"against_wall": "north"}},
          {"id": "wt", "label": "作業台", "query": "steel work table", "layout": {"against_wall": "south"}},
          {"id": "rack", "label": "厨房の棚", "query": "kitchen rack", "layout": {"against_wall": "east"}}]


def test_tables_get_the_tabletop_set(tmp_path):
    st = make(tmp_path, DINING, "normal", place="restaurant", w=6.0, d=4.0)
    ids = set(decor_ids(st))
    assert {"decor_condiments_t1", "decor_call_bell_t1", "decor_napkins_t1", "decor_dishes_rack"} <= ids
    assert not [i for i in ids if i.endswith("_wt")]  # not on the kitchen work table
    assert "decor_menu_t1" not in ids and "decor_magazine_t1" not in ids  # lived_in only / homes only
    assert st["issues"] == []
    s = service.load(tmp_path / "s")
    assert s.obj("decor_call_bell_t1").z == 0.7
    lived = make(tmp_path, DINING, "lived_in", place="restaurant", w=6.0, d=4.0, name="l")
    assert {"decor_menu_t1", "decor_water_t1", "decor_water_t1_2"} <= set(decor_ids(lived))


def test_restaurant_questions():
    q = plan_questions("ファミレスを作って", None, {})
    assert q["place"] == "restaurant" and [x["key"] for x in q["questions"] if x["key"] != "purpose"] == ["seats", "kitchen", "entrance"]
    q = plan_questions("15 × 10 m の定食屋、厨房付きで40席", None, {})
    assert q["place"] == "restaurant" and [x["key"] for x in q["questions"] if x["key"] != "purpose"] == ["entrance"]
    assert plan_questions("居酒屋", None, {})["place"] == "restaurant"
    assert plan_questions("カフェ", None, {})["questions"][0]["key"] == "seats"  # cafes keep their questions
