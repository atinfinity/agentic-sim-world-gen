"""What the scene is for: the purpose question and the defaults it sets."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.layout import usability
from agentic_sim_world_gen.scene import purpose as pp
from agentic_sim_world_gen.scene.questions import options_for, plan_questions

from test_density import CATALOG, STUDY, make, provider  # noqa: F401  (fixture: the fake catalog)


@pytest.mark.parametrize("text,name", [
    ("AMR の走行テスト用に倉庫を作って", "amr"), ("配膳ロボットを走らせるファミレス", "amr"),
    ("アームでマグカップをつかむキッチン", "manipulation"), ("物体検出の学習データを作りたい", "sdg"),
    ("展示会のデモ用にオフィス", "demo"), ("AMR のデモ", "amr"), ("6畳の書斎を作って", None),
])
def test_guess(text, name):
    g = pp.guess(text)
    assert (g[0] if g else None) == name


def test_question():
    out = plan_questions("コンビニを作って")
    assert [q["key"] for q in out["questions"]] == ["size", "entrance", "cold_cases", "purpose"]
    q = out["questions"][-1]
    assert q["default"] == "見た目の確認・デモ" and len(q["options"]) == 4 and "purpose=<the answer>" in out["hint"]
    out = plan_questions("AMR を走らせるコンビニを作って")
    assert out["purpose"] == "amr" and "purpose" not in [q["key"] for q in out["questions"]]
    assert {"key": "purpose", "item": "purpose", "reason": "given: amr"} in out["skipped"]
    out = plan_questions("事務所", known=["purpose"])
    assert "purpose" not in [q["key"] for q in out["questions"]]
    out = plan_questions("倉庫を作って。おまかせで")
    assert out["defaults"][-1] == {"item": "purpose", "value": "見た目の確認・デモ"}
    assert options_for("warehouse", "purpose")[1] == "AMR・移動ロボットの走行テスト"
    assert pp.by_label("AMR・移動ロボットの走行テスト").name == "amr" and pp.by_label("ロボットでピッキング").name == "manipulation"
    with pytest.raises(ValueError, match="unknown purpose"):
        pp.get("cooking")


def test_amr_then_sdg(tmp_path):
    d = make(tmp_path, STUDY, density="lived_in")["scene_dir"]
    assert any(o.decor for o in service.load(d).spec.objects)
    st = service.set_purpose(d, "amr", robot_radius=0.35)
    s = service.load(d)
    assert s.spec.purpose.name == "amr" and s.spec.purpose.aisle == 1.1 and s.spec.room.density == "sparse"
    assert not any(o.decor for o in s.spec.objects)
    rows = {a.item: a for a in s.spec.assumptions}
    assert rows["purpose"].given and rows["density"].basis == "用途から（AMR・移動ロボットの走行テスト）"
    assert rows["robot"].value == "半径 0.35 m、通路 1.1 m 以上" and rows["robot"].given  # a size passed is the user's
    assert any("nav_map(robot_radius=0.35)" in n for n in st["purpose_next"])
    assert usability.need_width(s) == 1.1 and usability.aisle_width("home") == 0.75

    # sdg: the robot rows go, the SDG settings come; the detector targets not in the scene are listed
    st = service.set_purpose(d, "合成データ（物体検出などの学習）", targets=["desk", "mug", "laptop"])
    s = service.load(d)
    assert s.spec.purpose.robot_radius is None and s.spec.sdg.frames == 100 and "coco" in s.spec.sdg.writers
    items = [a.item for a in s.spec.assumptions]
    assert "robot" not in items and items.count("density") == 1 and "sdg" in items and "targets" in items
    assert st["purpose_missing"] == ["laptop"] and any("laptop" in n for n in st["purpose_next"])
    # back to a demo: the SDG settings the purpose made are taken out again
    service.set_purpose(d, "demo", given=False)
    s = service.load(d)
    assert s.spec.sdg is None and s.spec.room.density == "lived_in"
    row = next(a for a in s.spec.assumptions if a.item == "purpose")
    assert not row.given and row.basis == "おまかせ（既定）" and row.options[0] == "見た目の確認・デモ"


def test_what_the_user_said_stays(tmp_path):
    d = make(tmp_path, STUDY, density="lived_in")["scene_dir"]
    service.set_assumptions(d, [{"item": "density", "value": "生活感あり", "given": True}])
    service.set_purpose(d, "amr")
    s = service.load(d)
    assert s.spec.room.density == "lived_in"
    assert [a.given for a in s.spec.assumptions if a.item == "density"] == [True]
