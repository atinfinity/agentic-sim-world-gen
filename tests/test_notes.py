"""Notes written at a spot of the preview: kept with what is there until the agent handles them."""

import json

import pytest

from agentic_sim_world_gen import notes as nt
from agentic_sim_world_gen import service
from agentic_sim_world_gen.preview import page

from test_preview_edit import api, post  # noqa: F401  (fixture: the preview server for the study scene)
from test_preview_page import scene  # noqa: F401  (fixture: a 4x3 m study with a desk and a chair)


def _desk(d):
    return next(o for o in service.status(d)["objects"] if o["id"] == "desk")["position"]


def test_note_on_the_plan(scene, api):  # noqa: F811
    _, edit = api
    before = service.load(scene).spec.model_dump(exclude={"notes"})
    made_from = page.scene_hash(scene)
    dx, dy = _desk(scene)
    assert post(edit, {"op": "note", "view": "plan", "position": [dx, dy], "text": " ここに ライトを "})[0] == 200
    assert post(edit, {"op": "note", "view": "plan", "position": [0.5, 0.5], "text": "この辺が狭い"})[0] == 200
    assert post(edit, {"op": "note", "view": "plan", "position": [1, 1], "text": "  "})[0] != 200
    s = service.load(scene)
    assert s.spec.model_dump(exclude={"notes"}) == before  # the scene itself is not changed
    assert page.scene_hash(scene) == made_from  # so the USD and the check do not look outdated
    st = service.status(scene)
    on, floor = st["notes"]
    assert on["id"] == "n1" and on["text"] == "ここに ライトを" and on["object"] == "desk"
    assert on["z"] == pytest.approx(0.74) and on["where"] == "机の上"
    assert "object" not in floor
    assert floor["z"] == 0 and floor["where"].startswith("床 x 0.5, y 0.5 m")
    assert "notes_hint" in st and "resolve_notes" in st["notes_hint"]
    assert "preview_edits" not in st  # a note is not an edit of the scene
    hist = json.loads((scene / page.STATE).read_text())["history"]
    assert hist[-1]["text"].startswith("プレビューで操作：書き込み 2（床 x 0.5, y 0.5 m") and hist[-1]["by"] == "preview"
    assert hist[-2]["text"] == "プレビューで操作：書き込み 1（机の上）：「ここに ライトを」"
    svg = (scene / "layout.svg").read_text()
    assert svg.count('class="wg-note"') == 2
    html = (scene / page.PAGE).read_text()
    assert "書き込み <span class='muted'>2 件" in html and "「この辺が狭い」" in html

    # the user withdraws one on the page; the agent handles the other
    assert post(edit, {"op": "note_delete", "id": "n2"})[0] == 200
    assert [n["id"] for n in service.status(scene)["notes"]] == ["n1"]
    st = service.resolve_notes(scene, ["n1"], "机にデスクライトを置きました")
    assert "notes" not in st and "notes_hint" not in st
    hist = json.loads((scene / page.STATE).read_text())["history"]
    assert hist[-2]["text"] == "プレビューで操作：書き込み 2 を取り消し"
    assert hist[-1]["text"] == "書き込み 1 に対応：机にデスクライトを置きました"
    assert 'class="wg-note"' not in (scene / "layout.svg").read_text()
    with pytest.raises(ValueError, match="no note n7"):
        service.resolve_notes(scene, ["n7"])
    # numbers go on after the last one
    assert service.add_note(scene, "棚", position=[3, 1])["notes"][0]["id"] == "n1"


def test_note_in_3d(scene):  # noqa: F811
    dx, dy = _desk(scene)
    # straight down over the desk, and one looking along +Y at a slant
    down = {"eye": [dx, dy, 5.0], "forward": [0, 0, -1], "up": [0, 1, 0], "hfov_deg": 60}
    slant = {"eye": [2.0, -2.0, 2.0], "forward": [0, 0.7071, -0.7071], "up": [0, 0.7071, 0.7071], "hfov_deg": 60}
    (scene / "check.json").write_text(json.dumps({"orbit": {"frames": ["00.jpg", "01.jpg"], "cameras": [down, slant]}}))
    st = service.add_note(scene, "ここに本", view="3d", frame=0, u=0.5, v=0.5, aspect=16 / 9)
    n = st["notes"][0]
    assert n["object"] == "desk" and n["z"] == pytest.approx(0.74) and n["position"] == pytest.approx([dx, dy], abs=1e-3)
    assert n["view"] == "3d" and n["frame"] == 0
    # the slanted view's center hits the floor 4 m ahead (y = 2) unless something is in the way
    o, d = nt.camera_ray(slant, 0.5, 0.5, 16 / 9)
    assert d == pytest.approx((0, 0.7071, -0.7071), abs=1e-3)
    st = service.add_note(scene, "床", view="3d", frame=1, u=0.05, v=0.5, aspect=16 / 9)
    m = st["notes"][1]
    assert m["position"][0] < 2.0
    with pytest.raises(ValueError, match="no camera"):
        service.add_note(scene, "x", view="3d", frame=5, u=0.5, v=0.5, aspect=1)
    with pytest.raises(ValueError, match="outside the room"):
        service.add_note(scene, "x", view="3d", frame=1, u=0.1, v=0.0, aspect=1)  # the floor past the walls, missing the desk
    with pytest.raises(ValueError, match="not on the floor"):
        service.add_note(scene, "x", view="3d", frame=1, u=0.5, v=0.0, aspect=0.5)  # above the horizon
    html = (scene / page.PAGE).read_text()
    assert "（3D で）" in html


def test_ray_box_misses_and_hits():
    from agentic_sim_world_gen.sdg import Box

    b = Box("a", "a", "c", "/p", [(0, 0), (1, 0), (1, 1), (0, 1)], (0.0, 1.0))
    assert nt._ray_box((0.5, 0.5, 3.0), (0, 0, -1), b) == pytest.approx(2.0)
    assert nt._ray_box((2.0, 0.5, 3.0), (0, 0, -1), b) is None
    assert nt._ray_box((-2.0, 0.5, 0.5), (1, 0, 0), b) == pytest.approx(2.0)


def test_records_do_not_make_the_check_stale(scene):  # noqa: F811
    """check_request and set_assumptions only record things: the USD and the check stay current."""
    made_from = page.scene_hash(scene)
    service.check_request(scene, [{"text": "机は壁際", "checks": [{"kind": "against_wall", "target": "desk"}]}])
    service.set_assumptions(scene, [{"item": "size", "value": "4×3 m", "given": True}])
    assert page.scene_hash(scene) == made_from
    service.move_object(scene, "desk", position=(1.0, 1.0))
    assert page.scene_hash(scene) != made_from
