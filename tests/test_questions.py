"""Questions worth asking per place."""

from __future__ import annotations

import pytest

from agentic_sim_world_gen.scene.questions import TABLE, guess_place, plan_questions
from agentic_sim_world_gen.scene.spec import PLACES, STORE_PLACES


def keys(out):
    """The place's own questions (the purpose question, comes on top of them)."""
    return [q["key"] for q in out["questions"] if q["key"] != "purpose"]


def test_every_place_has_questions_with_options():
    for place in PLACES:
        qs = TABLE[place]
        assert 2 <= len(qs) and all(2 <= len(q.options) <= 4 for q in qs), place
        assert len({q.key for q in qs}) == len(qs)


@pytest.mark.parametrize("text,place", [
    ("6畳の書斎を作って", "home"), ("コンビニを作って", "convenience_store"), ("８人のオフィス", "office"),
    ("物流倉庫", "warehouse"), ("ホームセンターの売り場", "home_center"), ("サーバールーム", "datacenter"),
    ("駅前の歩道", "station"), ("ベーカリーを開く", "bakery"), ("パン屋のイートイン", "bakery"), ("何か", None),
    ("イチゴの植物工場", "plant_factory"), ("自動車の組立工場", "factory"), ("a vertical farm", "plant_factory"),
])
def test_guess_place(text, place):
    assert guess_place(text) == place


def test_drops_what_the_request_says():
    out = plan_questions("コンビニ 10x8m、入口は南。レジは入口の横")
    assert out["place"] == "convenience_store" and keys(out) == ["cold_cases"]
    assert {s["key"] for s in out["skipped"]} == {"size", "entrance", "register"}
    out = plan_questions("コンビニを作って")
    assert keys(out) == ["size", "entrance", "cold_cases"]  # limit 3: three walls to choose from before two
    assert out["questions"][0]["default"].startswith("標準")


def test_bakery_asks_about_the_centre_and_eat_in():
    out = plan_questions("8m×6m くらいのパン屋を作って")
    assert out["place"] == "bakery" and keys(out) == ["entrance", "eat_in", "island"]
    out = plan_questions("パン屋を作って。中央に平台、窓際にイートイン")
    assert "island" not in keys(out) and "eat_in" not in keys(out)


def test_home_study():
    out = plan_questions("６畳の書斎を作って")  # full-width digit
    assert keys(out) == ["window"] and [x["item"] for x in out["later"]] == ["style"]  # the look is fixed later
    assert any(s["reason"].startswith("obvious") for s in out["skipped"] if s["key"] == "people")
    assert keys(plan_questions("リビングを作って")) == ["window", "people"]  # the size follows the activity


def test_known_place_limit_and_skip():
    out = plan_questions("事務所", known=["people", "desk layout"], limit=1)
    assert keys(out) == ["activity"]  # in place of the size
    out = plan_questions("事務所", known=["people", "desk layout", "activity"], limit=1)
    assert keys(out) == ["meeting"]
    out = plan_questions("倉庫を作って。おまかせで")
    assert out["questions"] == [] and "no_questions" in out and out["defaults"][0]["item"] == "racks"
    assert keys(plan_questions("作って", place="supermarket"))[0] == "size"
    assert "cold_cases" not in [q.key for q in TABLE["bookstore"]]
    with pytest.raises(ValueError):
        plan_questions("x", place="moon")


def test_plant_factory():
    out = plan_questions("イチゴ栽培の植物工場を作って。栽培棚は4列")
    assert out["place"] == "plant_factory" and keys(out) == ["size", "aisle"]


def test_draft_first():
    """The questions wait for the draft; only a request with no kind of place asks the place first."""
    out = plan_questions("リビングを作って")
    assert "draft first" in out["hint"] and "ask_now" not in out
    assert [d["item"] for d in out["defaults"]] == ["window", "people", "purpose"]  # + purpose; size and style come later
    assert out["defaults"][0]["value"] == out["defaults"][0]["options"][0]
    out = plan_questions("ワールドを作って")
    assert out["ask_now"][0]["key"] == "place" and "ask only" in out["hint"]
    assert "ask_now" not in plan_questions("ワールドを作って", known=["place"])
    out = plan_questions("リビングを作って", ask_first=True)
    assert "ask these in ONE message" in out["hint"] and "defaults" not in out


@pytest.mark.parametrize("text", ["おもちゃ屋を作って", "花屋", "八百屋の売り場", "酒屋を作って", "靴屋"])
def test_shops_without_a_table_are_shops(text):
    """These fell through to the generic table, which asks how many people use the place."""
    out = plan_questions(text)
    # A shop of some kind (shops have their own tables; either way no 人数)
    assert out["place"] in ("shop", *STORE_PLACES) and "people" not in [q["key"] for q in out["questions"]]


def test_robot_without_a_job_asks_the_purpose():
    """「ロボット」 alone was taken as amr."""
    out = plan_questions("ロボット用の八百屋を作って")
    assert "purpose" not in out and "purpose_unclear" in out
    assert [q["key"] for q in out["ask_now"]] == ["purpose"]
    assert "purpose" not in [d["item"] for d in out["defaults"]]
    # even when the rest is left to Claude
    out = plan_questions("ロボット用の部屋。おまかせで")
    assert [q["key"] for q in out["ask_now"]] == ["purpose"] and out["questions"] == []
    # a robot that drives is a robot test
    assert plan_questions("配膳ロボットが走るカフェ")["purpose"] == "amr"
    assert plan_questions("ロボットが走れる店")["purpose"] == "amr"
    assert "purpose_unclear" not in plan_questions("ロボットの展示をする店")


def test_paths_do_not_name_the_place_and_any_ya_is_a_shop():
    # The photo path "/home/..." said home, and お土産屋さん matched nothing
    req = "写真 /home/user/wt/retail5_out/photos/souvenir.jpg のような、ログハウスのお土産屋さんを作りたい。"
    assert guess_place(req) == "variety_store"
    assert guess_place("たこ焼き屋さんを作って") == "shop"
    assert guess_place("6畳の部屋") == "home"
    assert guess_place("屋外の駐車場") == "outdoor"
    assert guess_place("小屋の中") is None
    assert guess_place("https://example.com/home/a.jpg のような書斎") == "home"


def test_order_is_impact_times_uncertainty():
    """What rebuilds the room comes first; a default that follows another answer is rarely wrong."""
    from agentic_sim_world_gen.scene.questions import OFFICE, rework_of, score

    assert keys(plan_questions("事務所を作って", limit=9))[:2] == ["activity", "desk_layout"]  # activity stands for people and size
    out = plan_questions("事務所を作って", limit=9, known=["activity"])
    assert keys(out)[:2] == ["people", "desk_layout"] and keys(out)[-1] == "size"  # 「人数に合わせる」
    scores = [q["score"] for q in out["questions"][:-1]]  # the purpose is added after `limit`
    assert scores == sorted(scores, reverse=True)
    assert out["questions"][0]["rework"] == "部屋ごと作り直し"
    size = next(q for q in OFFICE if q.key == "size")
    assert rework_of(size) == 3 and score(size) == 0.9
    assert keys(plan_questions("倉庫を作って"))[:2] == ["racks", "size"]


def test_what_can_be_fixed_later_is_not_asked():
    out = plan_questions("リビングを作って", limit=9)
    assert "style" not in keys(out) and out["later"][0]["value"] == "ナチュラル（木目）"
    assert any(s["key"] == "style" and s["reason"].startswith("later") for s in out["skipped"])
    assert "`later`" in out["hint"]
    assert "later" not in plan_questions("モダンなリビングを作って")  # said in the request


def test_remembered_preferences_are_not_asked(tmp_path):
    """A floor the user chose in two scenes answers the style question."""
    from agentic_sim_world_gen import preferences as pf

    for i in (1, 2):
        pf.observe("floor", pf.room_target("home"), tmp_path / f"s{i}", {"preset": "wood_light"}, {"preset": "wood_dark"})
    out = plan_questions("リビングを作って", limit=9)
    assert "later" not in out and out["preferred"][0]["key"] == "style"
    assert out["preferred"][0]["remembered"][0]["item"] == "floor"
    assert any(s["reason"].startswith("preference: floor") for s in out["skipped"])
    assert "preferred" not in plan_questions("オフィスを作って")  # per place
    for i in (1, 2):
        pf.observe("aisle", "rack", tmp_path / f"w{i}", 3.0, 2.0)
    out = plan_questions("倉庫を作って", limit=9)
    assert "aisle" not in keys(out) and out["preferred"][0]["key"] == "aisle"
