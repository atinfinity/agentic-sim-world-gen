"""The numbers of an end-to-end run, from recorded `claude -p` events (no claude, no network)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from test_service import SPEC, fake  # noqa: F401  (the offline provider)

HERE = Path("scripts/e2e_eval")


def _load(name):
    sys.path.insert(0, str(HERE.resolve()))
    spec = importlib.util.spec_from_file_location(f"e2e_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


metrics = _load("metrics")


def _assistant(mid, content, context):
    return {"type": "assistant", "message": {"id": mid, "content": content, "usage": {
        "input_tokens": 2, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": context - 1002}}}


def _use(tid, name):
    return {"type": "tool_use", "id": tid, "name": name}


def _result(tid, error=False):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": error}]}}


def _done(cost, turns, ms, **more):
    return {"type": "result", "subtype": "success", "total_cost_usd": cost, "num_turns": turns, "duration_ms": ms, **more}


TURN1 = [
    {"type": "system", "subtype": "init"},
    _assistant("m1", [{"type": "thinking", "thinking": ""}], 25000),
    _assistant("m1", [{"type": "text", "text": "書斎を作ります。"}, _use("t1", "mcp__agentic-sim-world-gen__create_scene")], 25000),
    _result("t1"),
    _assistant("m2", [_use("t2", "mcp__agentic-sim-world-gen__move_object"), _use("t3", "Read")], 60000),
    _result("t2", error=True), _result("t3"),
    {"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "auto"}},
    _assistant("m3", [{"type": "text", "text": "The physics check passed. Now checking the requirements."},
                      _use("t4", "mcp__agentic-sim-world-gen__check_request")], 30000),
    _result("t4"),
    _assistant("m4", [{"type": "text", "text": "`scene.usda` OK"}, {"type": "text", "text": "できました。"}], 31000),
    _done(1.5, 4, 90000),
]
TURN2 = [
    _assistant("m5", [{"type": "text", "text": "椅子を動かしました。"}, _use("t5", "mcp__agentic-sim-world-gen__move_object")], 33000),
    _result("t5"),
    _done(1.75, 2, 30000, permission_denials=[{"tool_name": "Bash"}]),  # the cost is of the whole session
]


def test_language_of():
    assert metrics.language_of("通りました。") == "ja" and metrics.language_of("Passed. Let me look at it.") == "en"
    assert metrics.language_of("OK") is None and metrics.language_of("`scene.usda` scenes/study/check.png") is None
    assert metrics.language_of("check_in_isaac が通りました") == "ja"


def test_stream_metrics():
    m = metrics.stream_metrics([TURN1, TURN2], "ja")
    assert m["turns"] == 6 and m["tool_calls"] == 5 and m["seconds"] == 120.0
    assert m["tools"] == {"move_object": 2, "Read": 1, "check_request": 1, "create_scene": 1}
    assert m["tool_errors"] == 1 and m["tool_errors_by"] == {"move_object": 1}
    assert m["cost_usd"] == 1.75 and [t["cost_usd"] for t in m["per_turn"]] == [1.5, 0.25]
    assert m["context_first"] == 25000 and m["context_max"] == 60000  # one request once, though it came as two events
    assert m["compactions"] == 1 and m["permission_denials"] == 1 and "failed" not in m
    assert m["notes"] == 5 and m["notes_off_language"] == 1 and m["notes_off_language_after_compaction"] == 1
    assert m["off_language_examples"][0].startswith("The physics check passed")
    assert "notes_off_language" not in metrics.stream_metrics([TURN1])

    cut = metrics.stream_metrics([TURN1[:-1], [{"type": "result", "subtype": "error_max_budget_usd", "is_error": True}]])
    assert cut["failed"] == ["no result (timed out or stopped)", "error_max_budget_usd"] and cut["cost_usd"] == 0


def test_read_events_skips_a_cut_line(tmp_path):
    f = tmp_path / "turn1.jsonl"
    f.write_text(json.dumps(TURN2[0]) + "\n" + '{"type": "assis')
    assert len(metrics.read_events(f)) == 1


def test_scene_metrics():
    status = {"objects": [{"id": "desk", "selected": 0}, {"id": "rug", "selected": 0, "substitute": {"not_in_catalog": True}},
                          {"id": "lamp", "selected": 1, "substitute": {}}, {"id": "bike", "selected": None}],
              "issues": [{"kind": "overlap"}], "usability": [], "usd": "/x/scene.usda",
              "requirements_summary": {"met": 3, "unmet": 1, "unknown": 0, "total": 4},
              "last_check": {"verdict": "passed"}, "decor_skipped": [{}, {}]}
    m = metrics.scene_metrics(status)
    assert m == {"objects": 4, "issues": 1, "usability": 0, "check": "passed", "check_stale": False,
                 "requirements_met": 3, "requirements_total": 4, "requirements_rate": 0.75, "substitutes": 1,
                 "not_in_catalog": 1, "no_asset": 1, "decor_skipped": 2, "usd": True}
    empty = metrics.scene_metrics({"objects": []})
    assert empty["check"] is None and empty["requirements_rate"] is None and empty["usd"] is False


def test_scene_metrics_of_a_real_status(fake, tmp_path):
    from agentic_sim_world_gen import service

    service.create_scene(SPEC, tmp_path / "study")
    m = metrics.scene_metrics(service.status(tmp_path / "study"))
    assert m["objects"] == 3 and m["issues"] == 1 and m["no_asset"] == 0 and m["check"] is None


def test_table_and_report(tmp_path):
    report = _load("report")
    now = [{"case": "nl_study", "repeat": 1, "turns": 40, "tool_calls": 38, "tool_errors": 0, "cost_usd": 1.5, "seconds": 300.0,
            "context_max": 150000, "compactions": 0, "notes_off_language": 0, "check": "passed", "issues": 0, "usability": 1,
            "requirements_rate": 1.0, "substitutes": 0, "tools": {"move_object": 6, "create_scene": 1}},
           {"case": "nl_study", "repeat": 2, "turns": 44, "tool_calls": 40, "tool_errors": 2, "cost_usd": 1.9, "seconds": 340.0,
            "context_max": 160000, "compactions": 1, "notes_off_language": 2, "notes_off_language_after_compaction": 2,
            "off_language_examples": ["Passed. Now the licenses."], "check": "needs_fix", "issues": 0, "usability": 1,
            "requirements_rate": 0.8, "substitutes": 0, "tools": {"move_object": 2}},
           {"case": "photo_meeting", "repeat": 1, "turns": 0, "tool_calls": 0, "tool_errors": 0, "cost_usd": 0.0, "seconds": 0.0,
            "context_max": None, "compactions": 0, "failed": ["no result (timed out or stopped)"], "scene": None}]
    old = [{**now[0], "turns": 50, "cost_usd": 2.2, "requirements_rate": 0.7}]
    rows = metrics.by_case(now)
    assert rows["nl_study"]["n"] == 2 and rows["nl_study"]["turns"] == 42 and rows["nl_study"]["cost_usd"] == 1.7
    assert rows["nl_study"]["check"] == "passed/needs_fix" and rows["nl_study"]["cost_range"] == [1.5, 1.9]
    text = metrics.table(now, old)
    line = next(l for l in text.splitlines() if l.startswith("| nl_study"))
    assert "42 (↓8)" in line and "1.7 (↓0.5)" in line and "0.9 (↑0.2)" in line and "passed/needs_fix" in line
    assert "| photo_meeting ⚠ | 1 |" in text and "合計：$3.40" in text
    assert "採点" not in text  # no run had a judge score

    for name, data in (("new", now), ("old", old)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in data))
        (tmp_path / name / "run.json").write_text(json.dumps({"commit": "abc1234", "dirty": name == "new", "model": None}))
    out = report.report(tmp_path / "new", tmp_path / "old", tools=True)
    assert "commit `abc1234`（未コミットの変更あり）" in out and "前回" in out
    assert "photo_meeting #1：no result" in out and "nl_study #2：2 件（うち圧縮の後 2）" in out
    assert "| nl_study | move_object 4、create_scene 0.5 |" in out
    assert report.main([str(tmp_path / "new")]) == 0 and (tmp_path / "new" / "report.md").exists()


def test_cases_and_run_helpers(tmp_path, capsys):
    run = _load("run")
    cases = json.loads((HERE / "cases.json").read_text())
    assert 8 <= len(cases) <= 12 and len({c["id"] for c in cases}) == len(cases)
    assert sum(len(c["turns"]) > 1 for c in cases) >= 3  # some are conversations (--resume)
    for c in cases:
        assert c["lang"] == "ja" and all(metrics.language_of(t) == "ja" for t in c["turns"])
        for src in c.get("inputs") or []:
            assert Path(src).exists() and f"{{inputs}}/{Path(src).name}" in c["turns"][0]
    assert run.main(["--list"]) == 0 and "nl_study" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run.main([])  # a run needs somewhere to go
    with pytest.raises(SystemExit):
        run.main([str(tmp_path / "r"), "--cases", "nope"])
    assert run.latest_scene(tmp_path) is None
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "scene.resolved.json").write_text("{}")
    assert run.latest_scene(tmp_path) == tmp_path / "a"
    assert set(run.git_state()) == {"commit", "branch", "dirty"}


def test_context_breakdown(tmp_path):
    """What filled the conversation, by tool and by file read."""
    def use(tid, name, **args):
        return {"type": "tool_use", "id": tid, "name": name, "input": args}

    def result(tid, content):
        return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": content}]}}

    turn = [
        {"type": "user", "message": {"content": "書斎を作って"}},
        _assistant("m1", [{"type": "text", "text": "作ります。"}, use("t1", "Read", file_path="/x/skill/tools.md")], 1),
        _assistant("m1", [{"type": "text", "text": "作ります。"}, use("t1", "Read", file_path="/x/skill/tools.md")], 1),
        result("t1", "a" * 9000),
        _assistant("m2", [use("t2", "mcp__agentic-sim-world-gen__preview", scene_dir="s"), use("t3", "mcp__agentic-sim-world-gen__status")], 1),
        result("t2", [{"type": "text", "text": "b" * 500}, {"type": "image", "source": {}}, {"type": "image", "source": {}}]),
        result("t3", "c" * 2000),
    ]
    b = metrics.context_breakdown([turn, [result("t3", "c" * 1000)]])
    assert list(b["by"])[:2] == ["result: Read", "result: status"]
    assert b["by"]["result: Read"] == {"chars": 9000, "images": 0, "n": 1}
    assert b["by"]["result: status"] == {"chars": 3000, "images": 0, "n": 2}  # the id is known from the turn before
    assert b["by"]["result: preview"] == {"chars": 500, "images": 2, "n": 1}
    assert b["by"]["agent: text"] == {"chars": 5, "images": 0, "n": 1}  # the repeated message counts once
    assert b["by"]["user / harness text"]["chars"] == 6
    assert b["files"] == {"tools.md": {"chars": 9000, "images": 0, "n": 1}} and b["images"] == 2
    assert b["chars"] == sum(r["chars"] for r in b["by"].values())

    report = _load("report")
    case = tmp_path / "study__1"
    case.mkdir()
    (case / "turn1.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in turn))
    rows = report.context_rows(tmp_path)
    assert [c for c, _ in rows] == ["study #1"]
    table = metrics.breakdown_table(rows)
    assert "result: Read 9.0k×1" in table and "画2" in table and "Read の内訳：tools.md 9.0k×1" in table
