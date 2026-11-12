"""The robot's size and a plan / photo of a place that exists: asked only when their conditions hold."""
import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene import purpose as pp
from agentic_sim_world_gen.scene.questions import options_for, plan_questions
from agentic_sim_world_gen.scene.spec import SceneSpec


def keys(out, where="ask_now"):
    return [q["key"] for q in out.get(where, [])]


def test_robot_size_is_asked_for_a_robot_purpose():
    out = plan_questions("AMRの走行テスト用にコンビニを作って")
    assert out["purpose"] == "amr" and keys(out) == ["robot"]
    q = out["ask_now"][0]
    assert q["default"] == "中型（幅 0.6 m 前後）" and q["radius"][q["default"]] == 0.3
    assert q["radius"]["わからない（幅 0.6 m として進める）"] is None
    assert "robot_radius" in out["hint"]
    assert keys(plan_questions("アームでピッキングする倉庫"))[-1] == "robot"  # manipulation too
    # the draft's questions stay as they were
    assert keys(out, "questions") == keys(plan_questions("デモ用にコンビニを作って"), "questions")


@pytest.mark.parametrize("request_, radius", [
    ("TurtleBot4 が走行するリビングを作って", 0.17), ("TurtleBot3 のナビゲーション用の部屋", 0.155),
    ("幅45cmのロボットが走行する倉庫", 0.225), ("AMR（機体の直径 0.5 m）の走行テスト用の倉庫", 0.25),
    ("搬送ロボットは半径30cm。倉庫を作って", 0.3)])
def test_robot_size_in_the_request_is_not_asked(request_, radius):
    out = plan_questions(request_)
    assert "robot" not in keys(out) and out["robot"]["radius"] == pytest.approx(radius)
    assert any(s["key"] == "robot" and s["reason"].startswith("given") for s in out["skipped"])


def test_robot_size_is_not_asked_without_a_robot():
    for r in ("デモ用にコンビニを作って", "合成データ用のオフィス", "幅 5 m の倉庫を作って。デモ用"):
        out = plan_questions(r)
        assert "robot" not in keys(out) and "robot" not in out and "after_purpose" not in out
    # the room's width is not the robot's
    assert pp.robot_from("幅 5 m の倉庫でロボットが走る") is None


def test_robot_size_follows_the_purpose_question():
    out = plan_questions("コンビニを作って")  # the purpose is asked with the draft: the size goes with its answer
    assert "robot" not in keys(out) and out["after_purpose"]["when"] == ["amr", "manipulation"]
    assert out["after_purpose"]["ask"]["key"] == "robot"
    again = plan_questions("コンビニを作って", purpose="AMR・移動ロボットの走行テスト")
    assert again["purpose"] == "amr" and keys(again) == ["robot"]
    assert "robot" not in keys(plan_questions("コンビニを作って", purpose="demo"))
    assert "robot" not in keys(plan_questions("コンビニを作って", purpose="amr", known=["robot"]))


def test_left_to_us_asks_nothing_and_ask_first_keeps_the_cap():
    out = plan_questions("AMR用の倉庫。おまかせで")
    assert "ask_now" not in out and {"item": "robot", "value": "中型（幅 0.6 m 前後）"} in out["defaults"]
    first = plan_questions("うちの店をAMRの走行テスト用に作って", ask_first=True)
    assert keys(first, "questions")[:2] == ["source", "robot"] and len(first["questions"]) == 4


def test_plan_or_photo_is_asked_for_a_place_that_exists():
    out = plan_questions("うちの店を再現して")
    assert keys(out) == ["source"] and out["existing_place"] == "うちの"
    assert out["ask_now"][0]["default"] == "ない（文章から作る）"
    for r in ("実際の倉庫と同じレイアウトにしたい", "この店のデジタルツインを作りたい", "弊社のオフィスを作って"):
        assert "source" in keys(plan_questions(r)), r


def test_plan_or_photo_is_not_asked_when_there_is_one_or_the_place_is_made_up():
    for r in ("うちの店を写真から再現して", "うちの店 ~/shop/front.jpg", "実際の倉庫の図面 plan.dxf から作って",
              "うちの店の動画があります"):
        assert "source" not in keys(plan_questions(r)), r
    for r in ("6畳の書斎を作って", "コンビニを作って", "昭和の喫茶店を再現して", "私の理想の書斎"):
        out = plan_questions(r)
        assert "source" not in keys(out) and "existing_place" not in out, r
    assert "source" not in keys(plan_questions("うちの店を再現して", known=["source"]))
    assert "ask_now" not in plan_questions("うちの店を再現して。おまかせで")


def test_answers_become_a_radius():
    assert pp.robot_radius_of("小型（幅 0.4 m 前後）") == 0.2 and pp.robot_radius_of("大型") == 0.45
    assert pp.robot_radius_of("わからない（幅 0.6 m として進める）") is None
    assert pp.robot_radius_of("幅 50cm") == 0.25 and pp.robot_radius_of("Jackal です") == 0.255
    assert pp.robot_radius_of("まだ決めていない") is None
    assert options_for("warehouse", "robot")[0] == "中型（幅 0.6 m 前後）"


def test_a_radius_the_user_said_is_recorded_as_given(tmp_path):
    spec = SceneSpec(name="t", room={"width": 6, "depth": 5}, objects=[])
    d = service.create_scene(spec, tmp_path / "t", candidates=0)["scene_dir"]

    def robot():
        return next(a for a in service.load(d).spec.assumptions if a.item == "robot")

    service.set_purpose(d, "amr")
    assert not robot().given and robot().value.startswith("半径 0.3 m") and robot().options
    service.set_purpose(d, "amr", robot_radius=0.45)
    assert robot().given and robot().basis is None and "通路 1.3 m" in robot().value
    assert service.load(d).spec.purpose.aisle == 1.3
    service.set_purpose(d, "manipulation")  # the size stays the user's
    assert robot().given and service.load(d).spec.purpose.robot_radius == 0.45
    service.set_purpose(d, "demo")
    assert not [a for a in service.load(d).spec.assumptions if a.item == "robot"]
