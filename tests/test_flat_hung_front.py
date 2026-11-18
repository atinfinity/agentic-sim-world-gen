"""A thing hung on a wall with no recorded front faces out along its thin side (no network)."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.layout import frame
from agentic_sim_world_gen.layout.frame import half_extent
from agentic_sim_world_gen.scene.spec import ObjectSpec


def rec(name, w, d, h, front_deg=None):
    return AssetRecord(
        id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
        semantic_class="picture", license="CC-BY 4.0", front_deg=front_deg,
    )


POSTER = rec("poster", 0.61, 0.062, 0.79)  # a framed picture thin along Y, no front record
CATALOG = {"poster": [POSTER], "panel x": [rec("panelx", 0.05, 0.6, 0.8)],
           "poster front": [rec("posterf", 0.61, 0.062, 0.79, front_deg=90.0)], "box": [rec("box", 0.6, 0.5, 0.4)]}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def placed(tmp_path, objects):
    st = service.create_scene({"name": "t", "room": {"width": 4, "depth": 3, "height": 2.6}, "objects": objects},
                              tmp_path / "t")
    return {o["id"]: o for o in st["objects"]}, st


@pytest.mark.parametrize("wall,normal", [("north", 90.0), ("east", 0.0), ("south", 90.0), ("west", 0.0)])
def test_thin_poster_lies_flat_on_the_wall(tmp_path, wall, normal):
    objs, st = placed(tmp_path, [{"id": "p", "query": "poster", "layout": {"against_wall": wall, "wall_height": 1.5}}])
    p = objs["p"]
    # the thin side runs along the wall's normal: the poster sticks out only ~6 cm
    assert half_extent(POSTER, p["yaw_deg"], normal) == pytest.approx(0.031, abs=1e-3)
    x, y = p["position"]
    gap = {"north": 3 - y, "south": y, "east": 4 - x, "west": x}[wall]
    assert gap == pytest.approx(0.031 + 0.005, abs=2e-3)
    assert not [i for i in st["issues"] if i["kind"] != "info"]


def test_only_hung_thin_and_unrecorded(tmp_path):
    hung = ObjectSpec(id="p", query="poster", layout={"against_wall": "north", "wall_height": 1.5})
    floor = ObjectSpec(id="p", query="poster", layout={"against_wall": "north"})
    assert frame.front_local_deg(POSTER, hung) == frame.FLAT_FRONT_DEG
    assert frame.front_local_deg(POSTER, floor) == frame.DEFAULT_FRONT_DEG  # not hung: as before
    assert frame.front_local_deg(CATALOG["poster front"][0], hung) == 90.0  # recorded front wins
    assert frame.front_local_deg(CATALOG["panel x"][0], hung) == 0.0  # thin along X: +X is already the thin side
    assert frame.front_local_deg(CATALOG["box"][0], hung) == 0.0  # not thin
    turned = ObjectSpec(id="p", query="poster", front_offset_deg=180,
                        layout={"against_wall": "north", "wall_height": 1.5})
    assert frame.front_local_deg(POSTER, turned) == frame.FLAT_FRONT_DEG + 180
    assert frame.asset_front_deg(POSTER) == frame.DEFAULT_FRONT_DEG  # the asset's own front is unchanged


def test_moved_by_hand_keeps_its_front(tmp_path):
    """move_object drops the layout: a poster left on the wall keeps the thin side as its front."""
    objs, _ = placed(tmp_path, [{"id": "p", "query": "poster", "layout": {"against_wall": "north", "wall_height": 1.5}}])
    before = objs["p"]
    st = service.move_object(tmp_path / "t", "p", position=(2.0, 2.9))
    after = {o["id"]: o for o in st["objects"]}["p"]
    assert after["yaw_deg"] == pytest.approx(before["yaw_deg"])
    assert after["facing_deg"] == pytest.approx(before["facing_deg"])
    st = service.move_object(tmp_path / "t", "p", position=(2.0, 2.9), facing_deg=before["facing_deg"])
    after = {o["id"]: o for o in st["objects"]}["p"]
    assert half_extent(POSTER, after["yaw_deg"], 90.0) == pytest.approx(0.031, abs=1e-3)  # still flat on the wall
    low = ObjectSpec(id="p", query="poster", layout={"against_wall": "north", "wall_height": 0})
    assert frame.front_local_deg(POSTER, low) == frame.DEFAULT_FRONT_DEG  # wall_height 0 stands on the floor
