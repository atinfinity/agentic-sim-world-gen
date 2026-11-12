"""Assumptions that cannot all hold are found before the scene is built (no network)."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene import conflicts
from tests.test_layout import rec

CATALOG = {
    "dining table": [rec("table8", "table", 2.4, 1.1, 0.74)],
    "dining chair": [rec("chair", "chair", 0.45, 0.5, 0.85)],
    "sofa": [rec("sofa", "sofa", 1.9, 0.9, 0.8)],
    "sideboard": [rec("board", "sideboard", 1.8, 0.45, 0.8)],
    "desk": [rec("desk", "desk", 1.4, 0.7, 0.74)],
    "office chair": [rec("ochair", "office chair", 0.55, 0.55, 0.85)],
    "shelf": [rec("gondola", "shelf", 0.9, 0.45, 1.35)],
    "pallet rack": [rec("rack", "rack", 2.7, 1.1, 3.6)],
    "conference table": [rec("conf", "table", 6.0, 1.6, 0.74)],
    "rug": [rec("rug", "rug", 3.0, 2.0, 0.02)],
}
SIX_JO = {"width": 3.64, "depth": 2.73, "height": 2.4, "place": "home"}


@pytest.fixture(autouse=True)
def fake(tmp_path, monkeypatch):
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    monkeypatch.setenv("ISAACSIM_PATH", str(tmp_path / "nope"))
    monkeypatch.delenv("ISAACSIM_PYTHON", raising=False)
    monkeypatch.setattr(service, "live_available", lambda timeout_s=0.5: False)
    yield
    service.set_provider(None)


def start(tmp_path, name, room, objects, assumptions=(), **kw):
    spec = {"name": name, "room": room, "objects": objects, "assumptions": list(assumptions)}
    # no draft unless asked: composing would try to fetch the fake assets
    return service.start_scene(spec, tmp_path / name, browser=False, **{"skip": ["preview", "draft"], **kw})


def kinds(st):
    return [c["kind"] for c in (st.get("conflicts") or {}).get("found", [])]


def obj(i, q, **kw):
    return {"id": i, "label": i, "query": q, **kw}


def test_five_requests_that_cannot_hold(tmp_path):
    # 1. 6 畳に 8 人掛けの机と椅子 8 脚、ソファ、サイドボード
    st = start(tmp_path, "a", SIX_JO, [
        obj("table", "dining table", layout={"center": True}),
        obj("chairs", "dining chair", array={"count": 4, "rows": 2, "aisle": 1.2}, layout={"next_to": "table"}),
        obj("sofa", "sofa", layout={"against_wall": "north"}),
        obj("board", "sideboard", layout={"against_wall": "west"})], skip=["preview"])
    assert kinds(st) == ["floor"]
    c = st["conflicts"]
    f = c["found"][0]
    assert f["numbers"]["share"] > conflicts.FLOOR_FULL and f["fix"]["room"]["width"] > 3.64
    assert "部屋を広げる" in f["options"][0] and c["ask"]["options"][-1] == "おまかせ"
    assert c["ask"]["question"].endswith("どちらを優先しますか？")
    # no pictures of a scene that will be redone
    assert st["started"]["draft"]["stopped"] == "conflicts"

    # 2. 部屋より大きい机（幅 6 m の会議机を 3.64 m の部屋に）
    st = start(tmp_path, "b", SIX_JO, [obj("conf", "conference table", layout={"center": True})])
    assert kinds(st) == ["floor"] and "入りません" in st["conflicts"]["found"][0]["text"]
    assert st["conflicts"]["found"][0]["fix"]["room"]["width"] >= 7.0

    # 3. AMR（半径 0.3 m → 通路 1.0 m）なのに棚の列の間が 0.75 m
    shop = {"width": 8.0, "depth": 6.0, "height": 2.7, "place": "convenience_store"}
    st = start(tmp_path, "c", shop, [obj("gondola", "shelf", array={"count": 4, "rows": 3, "aisle": 0.75},
                                         layout={"center": True})], purpose="amr", purpose_given=True)
    assert kinds(st) == ["aisle"]
    f = st["conflicts"]["found"][0]
    assert f["numbers"] == {"need_m": 1.0, "narrowest_m": 0.75} and f["objects"] == ["gondola"]
    assert f["options"] == ["通路を 1 m に広げる", "ロボットを小さくする（半径 0.17 m 以下）"]

    # 4. 通路幅をユーザーが言った（仮定の行）場合も同じ
    st = start(tmp_path, "d", shop, [obj("gondola", "shelf", layout={"against_wall": "north"})],
               [{"item": "通路幅", "value": "0.75 m", "given": True}], purpose="amr", purpose_given=True)
    assert kinds(st) == ["aisle"] and "仮定「通路幅」（0.75 m）" in st["conflicts"]["found"][0]["text"]

    # 5. 8 人で使うのに椅子が 4 脚
    office = {"width": 8.0, "depth": 6.0, "height": 2.6, "place": "office"}
    st = start(tmp_path, "e", office, [
        obj("desk", "desk", array={"count": 2, "rows": 2, "aisle": 1.5}, layout={"center": True}),
        obj("chair", "office chair", array={"count": 2, "rows": 2, "aisle": 1.5}, layout={"against_wall": "south"})],
        [{"item": "people", "value": "8人", "given": True}])
    assert kinds(st) == ["seats"]
    assert st["conflicts"]["found"][0]["numbers"] == {"people": 8, "seats": 4}

    # 6. 天井 2.4 m に高さ 3.6 m のラック
    st = start(tmp_path, "f", {"width": 10.0, "depth": 8.0, "height": 2.4, "place": "warehouse"},
               [obj("rack", "pallet rack", layout={"against_wall": "north"})])
    assert kinds(st) == ["height"]
    assert st["conflicts"]["found"][0]["fix"] == {"room": {"height": 3.7}}

    # 7. どのアセットも合わない大きさの指定（幅 4 m 以上の机、高さ 3 m 以上のラック）でも、指定の寸法で分かる
    st = start(tmp_path, "h", SIX_JO, [obj("big", "desk", min_size=[4.0, 1.0, 0.6], layout={"center": True}),
                                       obj("high", "shelf", min_size=[0.9, 0.4, 3.0], layout={"against_wall": "north"})])
    assert kinds(st) == ["floor", "height"]
    assert "big（4.00 × 1.00 m 以上という指定）が部屋（3.64 × 2.73 m）に入りません" == st["conflicts"]["found"][0]["text"]
    assert "high（3.00 m 以上という指定）" in st["conflicts"]["found"][1]["text"]


def test_nothing_to_ask_when_they_hold(tmp_path):
    # a desk and a chair in 6 畳, a rug under them (walked on: not floor taken), one person
    st = start(tmp_path, "ok", SIX_JO, [
        obj("desk", "desk", layout={"against_wall": "north"}),
        obj("chair", "office chair", layout={"in_front_of": "desk"}),
        obj("rug", "rug", layout={"center": True})], [{"item": "people", "value": "1人", "given": True}])
    assert "conflicts" not in st
    # a shop has no seats: 人数 is not about chairs; a sofa seats one per 0.6 m
    shop = {"width": 8.0, "depth": 6.0, "height": 2.7, "place": "convenience_store"}
    st = start(tmp_path, "shop", shop, [obj("gondola", "shelf", array={"count": 4, "rows": 2, "aisle": 1.2},
                                            layout={"center": True})], [{"item": "people", "value": "3人"}])
    assert "conflicts" not in st
    st = start(tmp_path, "sofa", {**SIX_JO, "width": 5.0, "depth": 4.0}, [obj("sofa", "sofa", layout={"against_wall": "north"})],
               [{"item": "people", "value": "３人"}])
    assert "conflicts" not in st and conflicts.seats(service.load(tmp_path / "sofa")) == 3


def test_the_first_question_is_the_room_and_it_goes_away_when_fixed(tmp_path):
    # too small and too low at once: the room size is asked, both are listed
    st = start(tmp_path, "g", SIX_JO, [obj("rack", "pallet rack", array={"count": 1, "rows": 2, "aisle": 1.2},
                                           layout={"center": True})])
    assert kinds(st) == ["floor", "height"]
    assert "部屋" in st["conflicts"]["ask"]["question"] and "left_to_us" in st["conflicts"]["next"]
    d = tmp_path / "g"
    fix = {}
    for c in st["conflicts"]["found"]:
        fix.update(c["fix"]["room"])
    assert fix["height"] == 3.7 and fix["width"] > 3.64
    assert "conflicts" not in service.set_room(d, **fix)


def test_a_wrong_asset_is_not_a_conflict(tmp_path):
    """With real assets 「wall poster」 came back as a 6.0 x 3.6 m light panel and a sofa search can list a 5 m
    one: sizes nobody asked for. They are assets to swap, not a room to enlarge."""
    CATALOG["wall poster"] = [rec("light_panel", "frame", 6.0, 3.6, 1.46)]  # no typical size for the kind
    CATALOG["corner sofa"] = [rec("huge", "sofa", 5.2, 3.0, 0.8)]  # off the typical size of a sofa
    try:
        st = start(tmp_path, "poster", {"width": 8, "depth": 6, "place": "convenience_store"},
                   [obj("poster", "wall poster"), obj("shelf", "shelf", array={"count": 3})])
        assert kinds(st) == []
        st = start(tmp_path, "huge", SIX_JO, [obj("sofa", "corner sofa", position=[1.8, 1.4])])
        assert kinds(st) == []
        # asked for by size, the same sofa is the request: it does not go into the room
        st = start(tmp_path, "asked", SIX_JO, [obj("sofa", "corner sofa", position=[1.8, 1.4], min_size=[5, 2.5, 0.5])])
        assert kinds(st) == ["floor"]
    finally:
        del CATALOG["wall poster"], CATALOG["corner sofa"]
