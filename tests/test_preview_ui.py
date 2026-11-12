"""Preview fixes from the retail trials: steady port, grouped check problems, choices, grips, aisle naming."""

import pytest

import json

from agentic_sim_world_gen import service
from agentic_sim_world_gen.layout import usability
from agentic_sim_world_gen.preview import page, server
from agentic_sim_world_gen.preview.layout2d import render_svg
from agentic_sim_world_gen.scene.spec import Assumption

from test_preview_page import scene  # noqa: F401  (fixture: a study with a desk and a chair)
from test_usability import DOOR, detail, fake, make  # noqa: F401


def test_port_is_remembered(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "out"
    assert server._last_port(root) is None
    server._remember_port(root, 8770)
    server._remember_port(tmp_path / "other", 8771)
    assert server._last_port(root) == 8770 and server._last_port(tmp_path / "other") == 8771
    assert json.loads((tmp_path / "cache" / "preview_ports.json").read_text())[str(root)] == 8770


def test_check_problems_grouped_by_object():
    labels = {"shelf": "棚", "desk": "机"}
    names = [f"shelf/shelf_u0_{i}_0_0" for i in range(168)] + ["desk"]
    s = page.prim_summary(labels, names)
    assert s.startswith("棚 ×168") and "机" in s and s.count("・") == 1
    many = page.prim_summary({}, [f"o{i}" for i in range(15)], limit=12)
    assert many.endswith("ほか 3 物体") and many.count("・") == 12


def test_object_rows_get_choices(scene):  # noqa: F811
    s = service.load(scene)
    assert "desk_b にする" in page._object_options(s, Assumption(item="アセット", value="desk_a", object="desk"))
    assert page._object_options(s, Assumption(item="位置", value="北の壁", object="desk"))[0] == "北の壁"
    assert "東向き" in page._object_options(s, Assumption(item="向き", value="南向き", object="chair"))
    assert page._object_options(s, Assumption(item="色", value="白", object="desk")) == []


def test_small_things_get_a_grip(scene):  # noqa: F811
    service.add_object(scene, {"id": "mug", "query": "office chair", "position": [1.0, 1.0],
                               "min_size": [0.0, 0.0, 0.0]})
    s = service.load(scene)
    svg = render_svg(s, px_per_m=20)  # 0.55 m chair at 20 px/m = 11 px: smaller than the grip
    assert svg.count('class="wg-hit"') >= 1
    assert 'class="wg-hit"' not in render_svg(s, px_per_m=200)


@pytest.mark.no_auto_fix
def test_narrow_aisle_names_the_fixture_across(tmp_path):
    objects = [
        {"id": "a", "query": "shelf", "position": (1.5, 1.5), "yaw_deg": 180},
        {"id": "b", "query": "shelf", "position": (1.5, 2.8), "yaw_deg": 0},
    ]
    st = make(tmp_path, objects, openings=DOOR, place="convenience_store")
    d = detail(st, "narrow_aisle", "a")
    assert "to b in front" in d and "people's aisle for place convenience_store" in d


def test_amr_asks_for_a_fresh_nav_map(tmp_path):
    st = make(tmp_path, [{"id": "a", "query": "shelf", "position": (1.5, 1.5)}], openings=DOOR)
    d = st["scene_dir"]
    service.set_purpose(d, "amr")
    service.nav_map(d)
    assert not any("changed since the last nav map" in n for n in service.status(d)["purpose_next"])
    service.move_object(d, "a", position=[2.5, 2.0])
    assert any("changed since the last nav map" in n for n in service.status(d)["purpose_next"])
