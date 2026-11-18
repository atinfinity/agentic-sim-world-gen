"""The Stop hook of development sessions that catches a turn ending by handing a decision back. Offline."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parents[1] / "scripts" / "hooks" / "ask_guard.py"
spec = importlib.util.spec_from_file_location("ask_guard", HOOK)
ag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ag)


def test_closing_requests_are_caught():
    for text, quote in [
        ("直しました。\n\nこれはIssueにしていません。追うなら言ってください。", "追うなら言ってください"),
        ("PRを作りました。マージしてよければ「マージ」と言ってください。", "マージしてよければ「マージ」と言ってください"),
        ("1〜3を調べてIssueにするかどうかは、指示を待っています。", "1〜3を調べてIssueにするかどうかは、指示を待っています"),
        ("どちらにするか決めてください。", "どちらにするか決めてください"),
        ("2つのリポジトリでpushしてPRを作ってよいですか？", "2つのリポジトリでpushしてPRを作ってよいですか？"),
        ("マージしてもよろしいですか？テストは通りました。", "マージしてもよろしいですか？"),
        ("本当ですか？追うなら言ってください。", "追うなら言ってください"),
        ("進めてよいでしょうか？", "進めてよいでしょうか？"),
        ("どうしますか。", "どうしますか"),
        ("Fixed it. Let me know if you want an issue for this.", "Let me know if you want an issue for this"),
        ("Done. Should I open the PR now?", "Should I open the PR now?"),
    ]:
        assert ag.ask(text) == quote, text


def test_reports_pass():
    for text in ["マージしました。テストは912件通りました。", "",
                 "CLAUDE.mdに「言ってください」「しますか？」で終えないと書きました。",  # quoted: talked about
                 "動かない場合は`--force`を付けて作り直してください。",  # an instruction, not a decision
                 "ブラウザで画面を確認してください。", "Merged; all 912 tests pass.",
                 "Check the page in the browser."]:
        assert ag.ask(text) is None, text


def test_blocks_once():
    ev = {"last_assistant_message": "追うなら言ってください。"}
    out = ag.check(ev)
    assert out["decision"] == "block" and "\"追うなら言ってください\"" in out["reason"]
    assert ag.check({**ev, "stop_hook_active": True}) is None
    r = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps(ev), capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout)["decision"] == "block"
    for bad in ["not json", "[1]", '{"last_assistant_message": 3}']:
        r = subprocess.run([sys.executable, "-I", str(HOOK)], input=bad, capture_output=True, text=True)
        assert r.returncode == 0 and r.stdout == "", bad


def test_registered_in_project_settings():
    s = json.loads((Path(__file__).parents[1] / ".claude" / "settings.json").read_text())
    assert any("scripts/hooks/ask_guard.py" in h["command"] for g in s["hooks"]["Stop"] for h in g["hooks"])
