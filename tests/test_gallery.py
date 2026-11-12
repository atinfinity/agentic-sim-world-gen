"""Examples and past scenes as a gallery to start from."""

import json
import urllib.error
import urllib.request

import pytest

from agentic_sim_world_gen import gallery, service
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.preview import server
from agentic_sim_world_gen.scene.questions import plan_questions


def rec(name, w, d, h):
    return AssetRecord(id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
                       bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
                       semantic_class=name, license="CC0")


CLINIC = {"name": "clinic", "room": {"name": "診察室", "place": "hospital", "width": 5, "depth": 4, "floor": "wood_light"},
          "objects": [{"id": "desk", "label": "診察机", "query": "desk", "layout": {"against_wall": "north"}},
                      {"id": "chair", "label": "丸椅子", "query": "stool", "layout": {"in_front_of": "desk"}},
                      {"id": "bed", "label": "診察台", "query": "bed", "layout": {"against_wall": "west"}}]}
CAFE = {"name": "cafe", "room": {"name": "カフェ", "place": "cafe", "width": 6, "depth": 5},
        "objects": [{"id": "table", "label": "テーブル", "query": "table", "position": [3, 2.5]}]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    ex = tmp_path / "examples"
    ex.mkdir()
    for s in (CLINIC, CAFE):
        (ex / f"{s['name']}.json").write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    (ex / "broken.json").write_text("{", encoding="utf-8")
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_EXAMPLES", str(ex))
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_PREVIEW", "127.0.0.1:0")
    sizes = {"desk": (1.2, 0.6, 0.72), "stool": (0.4, 0.4, 0.45), "bed": (1.9, 0.7, 0.6), "table": (0.8, 0.8, 0.72)}
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None:
                                        [rec(q, *sizes[q])] if q in sizes else []})())
    yield tmp_path / "out"
    service.set_provider(None)


def test_entries_and_rank(world):
    rows = gallery.entries(world)
    assert [r["id"] for r in rows] == ["example:cafe", "example:clinic"]  # the broken file is left out
    clinic = rows[1]
    assert clinic["title"] == "診察室" and clinic["size"][:2] == [5, 4] and clinic["fixtures"] == ["診察机", "丸椅子", "診察台"]
    ranked = gallery.rank(rows, "hospital", "病院の診察室を作って")
    assert ranked[0]["id"] == "example:clinic" and "同じ種類の場所" in ranked[0]["near"] and "診察室" in ranked[0]["near"]
    assert ranked[1]["near"] is None
    # the same family of places: a school has no example, the clinic (office-like rooms) is the nearest
    assert gallery.rank(rows, "school")[0]["near"] == "近い種類の場所"


def test_show_gallery_page_and_pick(world):
    got = service.show_gallery("病院の診察室を作って", browser=False)
    assert got["place"] == "hospital" and got["near"][0]["id"] == "example:clinic" and got["picked"] is None
    with urllib.request.urlopen(got["url"]) as r:
        html = r.read().decode()
    assert "診察室" in html and "これに近い" in html and f"content='{server.TOKEN}'" in html
    assert server.TOKEN not in (world / gallery.PAGE).read_text()

    def post(body, token=server.TOKEN):
        req = urllib.request.Request(got["url"].rsplit("/", 1)[0] + "/api/gallery/pick", json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Agentic-Sim-World-Gen-Token": token})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    assert post({"id": "example:clinic"}, token="x") == 403
    assert post({"id": "example:nope"}) == 400
    assert post({"id": "example:clinic"}) == 200
    assert service.show_gallery(browser=False)["picked"]["id"] == "example:clinic"


def test_start_from_example_records_what_came_along(world):
    st = service.start_from_example("example:clinic", room={"width": 6}, drop=["desk"], skip=["draft", "preview"])
    assert st["scene_dir"].endswith("near_clinic")
    s = service.load(st["scene_dir"])
    assert s.spec.room.width == 6 and [o.id for o in s.spec.objects] == ["bed"]  # the stool went with its desk
    rows = {a.item: a for a in s.spec.assumptions if not a.auto}
    assert "size" not in rows  # overridden: not the example's
    assert rows["place"].value == "病院" and rows["floor"].value == "wood_light" and "walls" not in rows and rows["什器"].value == "診察台"
    assert all(not rows[k].given and rows[k].basis == "作例「診察室」から" for k in ("place", "floor", "什器"))
    assert st["started"]["from"]["dropped"] == ["chair", "desk"] and st["started"]["from"]["changed"] == ["width"]
    # a second start from the same example gets its own folder; a past scene is an entry too
    again = service.start_from_example("clinic", skip=["draft", "preview"])
    assert again["scene_dir"].endswith("near_clinic_2")
    assert "scene:near_clinic" in {r["id"] for r in gallery.entries(world)}
    assert "size" in {a.item for a in service.load(again["scene_dir"]).spec.assumptions if not a.auto}


def test_start_from_the_picked_one(world):
    with pytest.raises(ValueError, match="nothing picked"):
        service.start_from_example(skip=["draft", "preview"])
    world.mkdir(exist_ok=True)
    gallery.pick(world, "example:cafe")
    st = service.start_from_example(skip=["draft", "preview"])
    assert st["scene_dir"].endswith("near_cafe") and gallery.picked(world) is None
    with pytest.raises(ValueError, match="no such example"):
        service.start_from_example("example:nope")
    with pytest.raises(ValueError, match="not a gallery id"):
        service.start_from_example("example:../x")


def test_plan_questions_points_to_the_gallery():
    assert "gallery" in plan_questions("病院の待合室を作って")  # no question table of its own
    assert "gallery" not in plan_questions("オフィスを作って")
    assert "gallery" not in plan_questions("ワールドを作って")  # the place is asked first
    assert "gallery" not in plan_questions("病院の待合室。おまかせで")

