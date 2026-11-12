"""Doors / windows in the preview's change history: moved, resized, added, removed, old snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from agentic_sim_world_gen.preview.changes import MAX_LINES, diff, snapshot
from agentic_sim_world_gen.scene.spec import ResolvedScene, Room, SceneSpec

PLAN = Path(__file__).parents[1] / "examples" / "plans" / "1ldk.json"
DOOR = {"kind": "door", "wall": "south", "offset": 0.6, "width": 0.8}
WIN = {"kind": "window", "wall": "north", "offset": 1.2, "width": 1.2}


def _of(room: Room) -> dict:
    return snapshot(ResolvedScene(spec=SceneSpec(name="o", room=room), placements=[]), [])


def _snap(openings) -> dict:
    return _of(Room.model_validate({"width": 3.6, "depth": 2.7, "height": 2.4, "openings": openings}))


def test_snapshot_records_each_opening():
    s = _snap([DOOR, WIN])
    assert s["room"]["openings"][1] == {"id": "window_1", "kind": "window", "wall": "north", "offset": 1.2,
                                        "width": 1.2, "height": 1.1, "sill": 0.9, "style": "open"}
    assert diff(s, _snap([DOOR, WIN])) == []


def test_move_and_resize():
    a = _snap([DOOR, WIN])
    b = _snap([DOOR, {**WIN, "wall": "west", "offset": 1.37, "width": 1.6, "sill": 1.1}])
    assert diff(a, b) == ["窓：北の壁 1.2 m → 西の壁 1.37 m、幅 1.2 → 1.6 m、腰高 0.9 → 1.1 m"]
    assert diff(a, _snap([DOOR, {**WIN, "offset": 1.205}])) == []  # below MOVE_M
    c = _snap([{**DOOR, "style": "auto_sliding", "height": 2.2}, WIN])
    assert diff(a, c) == ["ドア：高さ 2 → 2.2 m、開き戸 → 自動ドア"]


def test_add_and_remove():
    a = _snap([WIN])
    b = _snap([WIN, {**DOOR, "id": "d1"}])
    assert diff(a, b) == ["ドア d1 を追加（南の壁、幅 0.8 m）"]
    assert diff(b, a) == ["ドア d1 を削除（南の壁）"]
    assert diff(_snap([DOOR, WIN]), _snap([DOOR])) == ["窓を削除（北の壁）"]
    assert diff(_snap([]), _snap([DOOR])) == ["ドアを追加（南の壁、幅 0.8 m）"]


def test_default_ids_follow_the_list_order():
    # set_room replaced the list: the window was window_0 and is now window_1, still the same window
    a = _snap([WIN])
    b = _snap([DOOR, {**WIN, "wall": "east", "offset": 1.0}])
    assert diff(a, b) == ["ドアを追加（南の壁、幅 0.8 m）", "窓：北の壁 1.2 m → 東の壁 1 m"]


def test_several_of_a_kind_show_ids():
    a = _snap([WIN, {**WIN, "offset": 2.4}])
    b = _snap([WIN, {**WIN, "offset": 2.8}])
    assert diff(a, b) == ["窓 window_1：北の壁 2.4 m → 北の壁 2.8 m"]


def test_plan_openings_use_the_plan_wall_ids():
    room = Room.model_validate({"plan_file": str(PLAN)})
    a = _of(room)
    data = room.model_dump()
    data["openings"][1].update(wall="n", offset=2.0)  # the window on wall "e"
    b = _of(Room.model_validate(data))
    assert diff(a, b) == ["窓 window_1：壁 e 1.8 m → 壁 n 2 m"]


def test_old_snapshot_kept_only_the_count():
    b = _snap([DOOR, WIN])
    old = json.loads(json.dumps(b))
    old["room"]["openings"] = 2
    assert diff(old, b) == []
    old["room"]["openings"] = 1
    assert diff(old, b) == ["開口の数：1 → 2"]
    old["room"].pop("openings")
    assert diff(old, b) == []


def test_many_changes_are_folded():
    a = _snap([{**WIN, "id": f"w{i}", "offset": 0.2 + 0.2 * i, "width": 0.1} for i in range(15)])
    b = _snap([{**WIN, "id": f"w{i}", "offset": 0.2 + 0.2 * i, "width": 0.15} for i in range(15)])
    lines = diff(a, b)
    assert len(lines) == MAX_LINES and lines[-1] == f"ほか {15 - MAX_LINES + 1} 件"
