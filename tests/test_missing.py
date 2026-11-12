"""What the asset sources did not have, added up over the scenes of this machine."""

import json

from test_service import CATALOG, SPEC, fake, rec  # noqa: F401

from agentic_sim_world_gen import cli, missing, service
from agentic_sim_world_gen.scene.spec import DecorSkip


import pytest

LAMP = [rec("uv_robot", 0.4, 0.4, 1.5, "A UV disinfection robot")]


@pytest.fixture(autouse=True)
def _good_desk(monkeypatch):
    """The plain catalog's records have no caption (all weak): here the desk is a real match."""
    monkeypatch.setitem(CATALOG, "office desk", [rec("desk_b", 1.4, 0.7, 0.74, "An office desk")])
    monkeypatch.setitem(CATALOG, "floor lamp", LAMP)
    monkeypatch.setitem(CATALOG, "Floor  Lamp", LAMP)


def _spec(*objects):
    return {**SPEC, "objects": [SPEC["objects"][0], *objects]}


PLAIN = _spec()


def test_missing_is_recorded_per_scene_and_summed(fake, tmp_path, monkeypatch, capsys):
    lamp = {"id": "lamp", "query": "Floor  Lamp", "label": "フロアランプ", "position": [2.5, 0.5]}
    rug = {"id": "rug", "query": "rug", "position": [1.8, 1.0]}  # nothing in the catalog at all

    assert missing.summary()["rows"] == []
    a, b = tmp_path / "a", tmp_path / "b"
    st = service.create_scene(_spec(lamp, rug), a)
    assert next(o for o in st["objects"] if o["id"] == "lamp")["substitute"]
    service.create_scene(_spec({**lamp, "id": "lamp1"}, {**lamp, "id": "lamp2"}), b)
    service.create_scene(PLAIN, tmp_path / "c")  # nothing missing: no file
    assert len(list(missing.folder().glob("*.json"))) == 2

    s = missing.summary()
    assert s["scenes"] == 2 and [(r["query"], r["scenes"], r["objects"]) for r in s["rows"]] == [
        ("floor lamp", 2, 3), ("rug", 1, 1)]
    row = s["rows"][0]
    assert row["by"] == {"substitute": 3} and row["labels"] == ["フロアランプ"] and row["used"] == ["uv_robot"]
    assert s["rows"][1]["by"] == {"none": 1}
    assert missing.summary(why="none")["rows"][0]["query"] == "rug" and missing.summary(top=1)["queries"] == 2

    service.move_object(a, "desk", position=(1.1, 2.3))  # saving again does not count the scene twice
    assert missing.summary()["rows"][0]["objects"] == 3
    service.remove_object(a, "lamp")  # no longer wanted there
    service.remove_object(a, "rug")
    s = missing.summary()
    assert [(r["query"], r["scenes"]) for r in s["rows"]] == [("floor lamp", 1)]

    assert cli.main(["missing"]) == 0
    out = capsys.readouterr().out
    assert "floor lamp  [代用 2]" in out and "→ uv_robot" in out
    assert cli.main(["missing", "--json", "--top", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["query"] == "floor lamp"


def test_decor_and_windows(fake, tmp_path):
    d = tmp_path / "s"
    service.create_scene(PLAIN, d)
    s = service.load(d)
    s.spec.decor_skipped += [DecorSkip(label="和菓子（case_1）", host="case_1", reason="no_asset"),
                             DecorSkip(label="陳列（g1）", host="g1", reason="no_spot")]
    st = service.status(d)
    missing.record(d, s, st, windows=[{"id": "win_w1", "query": "window", "nearest": "car_door", "opening": "w1"}])
    rows = {r["query"]: r for r in missing.summary()["rows"]}
    assert set(rows) == {"和菓子", "window"}  # no_spot is not the catalog's fault
    assert rows["window"]["by"] == {"window": 1} and rows["window"]["used"] == ["car_door"]
    missing.record(d, s, st)  # another save keeps the windows
    assert "window" in {r["query"] for r in missing.summary()["rows"]}
    assert "不足しているアセット：2 種" in missing.text(missing.summary())


def test_off_and_unwritable(fake, tmp_path, monkeypatch):
    lamp = {"id": "lamp", "query": "floor lamp", "position": [2.5, 0.5]}
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_MISSING", "0")
    service.create_scene(_spec(lamp), tmp_path / "a")
    assert not missing.folder().exists() and "ありません" in missing.text(missing.summary())
    monkeypatch.delenv("AGENTIC_SIM_WORLD_GEN_MISSING")
    (tmp_path / "file").write_text("")
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_CACHE", str(tmp_path / "file"))  # cannot make a folder there: the save still works
    assert service.move_object(tmp_path / "a", "desk", position=(1.1, 2.3))["objects"]
