"""Who, how many, doing what: the size, the main furniture and the counts follow from the activity."""

from agentic_sim_world_gen.scene import activity as act
from agentic_sim_world_gen.scene.questions import options_for, plan_questions


def keys(out):
    return [q["key"] for q in out["questions"]]


def test_meeting_of_four_with_a_screen():
    r = act.derive("office", "4人で会議、画面共有あり")
    assert r["activity"] == "meeting" and r["people"] == 4 and r["people_given"] and r["extras"] == ["画面共有"]
    assert r["area"] == 12.0 and r["room"] == {"width": 4.0, "depth": 3.0}
    assert {o["label"]: o["count"] for o in r["objects"]} == {"会議テーブル": 1, "椅子": 4, "モニター": 1}
    rows = {a["item"]: a for a in r["assumptions"]}
    assert rows["size"]["basis"].startswith("活動から") and not rows["size"]["given"] and "12 m²" in rows["size"]["value"]
    assert rows["people"]["given"] and rows["椅子の数"]["value"] == "4"


def test_counts_and_area_follow_the_people():
    a, b = act.derive("office", "会議", people=4), act.derive("office", "十人で会議")
    assert b["people"] == 10 and b["area"] > a["area"] and not a["extras"]
    assert next(o["count"] for o in b["objects"] if o["label"] == "会議テーブル") == 2  # 8 seats a table
    d = act.derive("office", "デスクワーク")
    assert d["people"] == 8 and not d["people_given"] and d["area"] == 48.0  # 6 m² a person
    assert "既定の人数" in next(r for r in d["assumptions"] if r["item"] == "people")["basis"]
    assert act.derive("home", "夫婦の寝室")["people"] == 2
    assert act.derive("office", "事務所") is None and act.derive("convenience_store", "買い物") is None


def test_the_option_of_the_question_reads_back():
    q = act.question("office")
    assert q["question"].startswith("誰が") and q["default"] == q["options"][0] == "8人でデスクワーク"
    r = act.derive("office", "4人で会議（画面共有あり）")
    assert r["activity"] == "meeting" and r["extras"] == ["画面共有"]
    assert act.question("convenience_store") is None
    assert options_for("office", "activity") == q["options"]


def test_plan_questions_asks_the_activity_instead_of_size_and_people():
    out = plan_questions("事務所を作って")
    assert keys(out)[0] == "activity" and "size" not in keys(out) and "people" not in keys(out)
    assert {s["key"] for s in out["skipped"] if s["reason"] == "asked as the activity"} == {"size", "people"}
    assert "derive_activity" in out["hint"] and out["defaults"][0]["item"] == "activity"
    assert out["drafts"]["axis"] == "size"  # the size is still nobody's decision
    out = plan_questions("6人のオフィスを作って")
    assert out["questions"][0]["options"][0] == "6人でデスクワーク"
    assert keys(plan_questions("コンビニを作って"))[0] == "size"  # no activities for a shop
    assert "activity" not in keys(plan_questions("8 × 6 m の事務所を作って"))  # the size is given


def test_plan_questions_derives_when_the_request_says_it():
    out = plan_questions("4人で会議する部屋を作って。画面共有あり")
    assert out["place"] == "office" and out["activity"]["room"] == {"width": 4.0, "depth": 3.0}
    assert "activity" not in keys(out) and "size" not in keys(out)
    assert any(s["key"] == "size" and s["reason"].startswith("derived from the activity") for s in out["skipped"])
    assert "「活動から」" in out["hint"]
    out = plan_questions("６畳の書斎を作って")
    assert out["activity"]["activity"] == "telework" and any(s["reason"] == "given: 6畳" for s in out["skipped"])


def test_mcp_tool():
    from agentic_sim_world_gen.mcp_server import derive_activity

    r = derive_activity("6人で会議、ホワイトボードも", "office")
    assert r["people"] == 6 and "ホワイトボード" in r["extras"]
    assert derive_activity("4人で会議")["place"] == "office"  # the place from the words
    assert "one of" in derive_activity("なにか", "office")["error"]
