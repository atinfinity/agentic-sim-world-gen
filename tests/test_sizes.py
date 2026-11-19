"""Without min/max_size, assets far from the kind's typical size go after the others."""

import re
from pathlib import Path

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets import sizes
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.preview import page
from agentic_sim_world_gen.scene.spec import ObjectSpec


def rec(name, w, d, h, cls="cardboard box"):
    return AssetRecord(id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
                       bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), semantic_class=cls,
                       tags=[cls], license="CC-BY-4.0")


PASTA = rec("pasta_box", 0.10, 0.12, 0.16)  # "cardboard pasta box"
SHIPPING = rec("shipping_box", 0.41, 0.31, 0.42)
UNKNOWN = rec("no_bbox", 0, 0, 0)


def test_kind_of_takes_the_longest_known_tail():
    assert sizes.kind_of("white office desk") == "office desk"
    assert sizes.kind_of("writing desk") == "desk"
    assert sizes.kind_of("desk lamp") == "desk lamp"  # a lamp, not a desk
    assert sizes.kind_of("lamp") is None  # ceiling, desk or floor: no one size
    assert sizes.kind_of("office chairs") == "office chair"
    assert sizes.kind_of("sofa 3 seat") == "sofa"
    assert sizes.kind_of("Bookshelves") == "bookshelf"
    assert sizes.kind_of("thing", ["flat screen tv"]) == "tv"  # synonyms when the query names no known kind
    assert sizes.kind_of("drum barrel") == "barrel"


def test_off_size_widens_the_typical_range():
    assert sizes.off_size(PASTA, "cardboard box") == {"long": "0.12 m under 0.15 m"}
    assert sizes.off_size(SHIPPING, "cardboard box") is None
    assert sizes.off_size(UNKNOWN, "cardboard box") is None  # no size: no verdict
    assert sizes.off_size(PASTA, None) is None
    lo, hi = sizes.default_range("sofa")
    assert lo == (0.84, 0.42, 0.36) and hi == (3.6, 1.65, 1.5)
    assert sizes.off_size(rec("four_seat", 3.01, 0.7, 0.73, "sofa"), "sofa") is None  # 25 % past the max is fine


def test_shape():
    tall = rec("floor_lamp", 0.4, 0.4, 1.7, "floor lamp")
    squat = rec("lantern", 0.6, 0.6, 1.2, "floor lamp")
    assert sizes.off_size(tall, "floor lamp") is None
    assert set(sizes.off_size(squat, "floor lamp")) == {"height/long"}
    assert sizes.off_size(rec("mat", 2.0, 1.4, 0.01, "rug"), "rug") is None
    assert "height/long" in sizes.off_size(rec("pouf", 0.6, 0.6, 0.04, "rug"), "rug")
    lying = rec("cylinder", 0.47, 0.22, 0.30, "fire extinguisher")
    assert set(sizes.off_size(lying, "fire extinguisher")) == {"height/long"}


def test_skill_table_matches_the_code():
    """SKILL.md lists the same typical sizes the code uses (one source: assets/sizes.py)."""
    text = (Path(__file__).parents[1] / ".claude/skills/world-gen/SKILL.md").read_text()
    table = text.split("## Typical sizes", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| ([^|]+?) \| ([\d.]+), ([\d.]+), ([\d.]+) \| ([\d.]+), ([\d.]+), ([\d.]+) \|$", table, re.MULTILINE)
    assert len(rows) >= 10
    for name, *v in rows:
        kind = re.sub(r"\s*\(.*\)", "", name).strip().lower()
        lo, hi = sizes.TYPICAL[kind]
        assert (lo, hi) == (tuple(map(float, v[:3])), tuple(map(float, v[3:]))), kind


class Fake:
    name = "fake"

    def search(self, query, *, limit=10, size=None):
        return [PASTA, SHIPPING, UNKNOWN][:limit]  # _find_candidates applies min/max_size itself (fits)


@pytest.fixture
def fake():
    service.set_provider(Fake())
    yield
    service.set_provider(None)


def test_candidates_put_off_size_last(fake):
    cands = service._find_candidates(ObjectSpec(id="box", query="cardboard box"), 4)
    assert [c["name"] for c in cands] == ["shipping_box", "no_bbox", "pasta_box"]  # kept, not dropped
    assert cands[2]["match"]["off_size"] == {"kind": "cardboard box", "long": "0.12 m under 0.15 m"}
    assert "off_size" not in cands[0]["match"] and "off_size" not in cands[1]["match"]
    assert service._one_line({**cands[2], "index": 2}).endswith("off-size:long")
    # a kind without a typical size keeps the plain match order
    assert [c["name"] for c in service._find_candidates(ObjectSpec(id="box", query="box"), 4)][0] == "pasta_box"


def test_explicit_limits_keep_the_old_behaviour(fake):
    o = ObjectSpec(id="box", query="cardboard box", max_size=(0.5, 0.5, 0.5))
    cands = service._find_candidates(o, 4)
    assert [c["name"] for c in cands] == ["pasta_box", "shipping_box", "no_bbox"]  # all fit 0.5 m, plain order
    assert not any("off_size" in c["match"] for c in cands)


def test_preview_badge(fake, tmp_path):
    d = tmp_path / "scene"
    service.create_scene({"name": "s", "room": {"width": 3, "depth": 3},
                          "objects": [{"id": "box", "query": "cardboard box"}]}, d)
    html = (d / page.PAGE).read_text()
    assert html.count("寸法違い?") == 1 and "cardboard box の標準サイズ外：long 0.12 m under 0.15 m" in html


def test_wall_equipment_sizes():
    """SimReady's "air conditioner"s are rooftop units and its "air vent"s car hoods; a room's are smaller."""
    assert sizes.kind_of("wall mounted air conditioner") == "air conditioner"
    assert sizes.kind_of("ceiling cassette air conditioner") == "air conditioner"
    assert sizes.kind_of("air conditioning unit") == "air conditioning unit"
    assert sizes.kind_of("rooftop air conditioner") == "rooftop air conditioner"
    assert sizes.kind_of("precision air conditioner") == "precision air conditioner"
    assert sizes.kind_of("air vent") == sizes.kind_of("ceiling vent") == "vent"
    assert sizes.kind_of("panel radiator") == "radiator"
    rooftop = rec("CW181DCHA1SAEX", 3.103, 1.238, 3.761, "air conditioner")
    split = rec("split_ac", 0.8, 0.22, 0.29, "air conditioner")
    assert set(sizes.off_size(rooftop, "air conditioner")) == {"long", "height"}
    assert sizes.off_size(split, "air conditioner") is None
    assert sizes.off_size(split, "air conditioning unit") is None
    for big in (rooftop, rec("CW415H1TA1S9BT", 3.654, 1.58, 4.301), rec("DA265DP3AWDR16", 3.056, 2.157, 4.47),
                rec("CW084DC1A1SDQS", 2.52, 0.929, 2.768)):  # SimReady's: the big kinds keep them
        assert sizes.off_size(big, "rooftop air conditioner") is None
        assert sizes.off_size(big, "precision air conditioner") is None
    assert sizes.off_size(rec("tower_ac", 0.6, 0.35, 1.8), "floor standing air conditioner") is None
    assert sizes.off_size(rec("cassette", 0.95, 0.95, 0.3, "air conditioner"), "air conditioner") is None
    hood = rec("bodyPart_hood_c01", 1.552, 1.276, 0.314, "air vent")
    assert "long" in sizes.off_size(hood, "vent")
    assert sizes.off_size(rec("extractor_ceiling_a01", 0.945, 0.945, 0.081, "ceiling vent"), "vent") is None
    assert sizes.off_size(rec("wall_grille", 0.3, 0.03, 0.15, "air vent"), "vent") is None
    stove = rec("electric_stove", 0.648, 0.503, 0.859, "heater")  # Poly Haven's heater: not a radiator's shape
    assert "short" in sizes.off_size(stove, "radiator")
    assert sizes.off_size(rec("panel_radiator", 1.0, 0.1, 0.6, "radiator"), "radiator") is None
