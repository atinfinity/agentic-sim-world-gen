"""The hooks that catch a note or report to the user written in another language than the user's."""
import json
import subprocess
import sys

from agentic_sim_world_gen import lang_guard as g


def _user(text, kind="human"):
    return {"type": "user", "origin": {"kind": kind}, "message": {"role": "user", "content": text}}


def _say(*blocks):
    content = []
    for b in blocks:
        content.append({"type": "tool_use", "id": b[1:], "name": "x", "input": {}} if b.startswith("@")
                       else {"type": "text", "text": b})
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


def _result(tid):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": "{\"ok\": true, \"issues\": []}"}]}}


def _transcript(tmp_path, records):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n")
    return str(p)


PROMPT = "computer.mp4 は部屋の中を歩いて撮った動画です。この部屋を Isaac Sim のワールドにしてください。"


def test_language():
    assert g.language("机は 1.8 m の白い机（sm_desk_a01_01）になりました。") == "ja"
    assert g.language("USD を書き出しました。`compose_scene` の結果を見ます。") == "ja"
    assert g.language("Meanwhile I'll read the landmarks in the other frames.") == "en"
    assert g.language("OK") is None and g.language("`/home/a/b.usd` scenes/computer_room/check.png") is None


def test_post_tool(tmp_path):
    ja = [_user(PROMPT), _say("フレームを切り出します。", "@a"), _result("a")]
    assert g.check({"transcript_path": _transcript(tmp_path, ja), "tool_use_id": "a"}, "post") is None
    en = ja + [_say("Meanwhile I'll read the landmarks in the other frames.", "@b"),
               {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "c", "name": "x", "input": {}}]}}]
    path = _transcript(tmp_path, en)
    out = g.check({"transcript_path": path, "tool_use_id": "b"}, "post")
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "日本語に戻す" in ctx and "Meanwhile I'll read" in ctx
    assert g.check({"transcript_path": path, "tool_use_id": "c"}, "post") is None  # told once per note
    # the note was before an earlier tool result: not this call's
    assert g.check({"transcript_path": _transcript(tmp_path, en[:-1] + [_result("b"), _say("@d")]),
                    "tool_use_id": "d"}, "post") is None


def test_notifications_are_not_the_user(tmp_path):
    """A background task's notification is English but not what the user writes in."""
    note = ("<task-notification>\n<task-id>k58ht4ako</task-id>\n<status>completed</status>\n"
            "<summary>MCP task k58ht4ako (agentic-sim-world-gen/video_poses) completed</summary>\n</task-notification>")
    recs = [_user(PROMPT), _say("@a"), _result("a"), _user(note, "task-notification"),
            _say("推定が通りました。部屋の寸法を当てはめます。", "@b")]
    assert g.check({"transcript_path": _transcript(tmp_path, recs), "tool_use_id": "b"}, "post") is None
    recs[3].pop("origin")  # an older transcript without `origin`: the tags are left out
    assert g.check({"transcript_path": _transcript(tmp_path, recs), "tool_use_id": "b"}, "post") is None


def test_stop(tmp_path):
    recs = [_user(PROMPT), _say("@a"), _result("a"),
            _say("The room from computer.mp4 is now a finished Isaac Sim world. The check passed.")]
    path = _transcript(tmp_path, recs)
    out = g.check({"transcript_path": path}, "stop")
    assert out["decision"] == "block" and "日本語で書き直す" in out["reason"]
    assert g.check({"transcript_path": path, "stop_hook_active": True}, "stop") is None  # once
    # Stop runs before the last message is in the transcript: Claude Code passes it as `last_assistant_message`
    early = _transcript(tmp_path, recs[:-1])
    assert g.check({"transcript_path": early}, "stop") is None
    out = g.check({"transcript_path": early, "last_assistant_message": "The test is done and everything worked."}, "stop")
    assert out["decision"] == "block"
    recs[-1] = _say("computer.mp4 の部屋を Isaac Sim のワールドに仕上げました。チェックも通りました。")
    assert g.check({"transcript_path": _transcript(tmp_path, recs)}, "stop") is None
    en = [_user("Make a small study room with a desk and a bookshelf."), _say("@a"), _result("a"),
          _say("The study is ready: a desk, a chair and a bookshelf.")]
    assert g.check({"transcript_path": _transcript(tmp_path, en)}, "stop") is None


def test_the_prompt_far_back(tmp_path, monkeypatch):
    """A long turn: the prompt is beyond the tail that is read, and is found at the head of the file."""
    monkeypatch.setattr(g, "TAIL_BYTES", 2000)
    monkeypatch.setattr(g, "CHUNK", 512)
    filler = [r for i in range(40) for r in (_say(f"@f{i}"), _result(f"f{i}"))]
    recs = [_user(PROMPT)] + filler + [_say("Now the desk asset, widening the size range a little.", "@z")]
    out = g.check({"transcript_path": _transcript(tmp_path, recs), "tool_use_id": "z"}, "post")
    assert out and "日本語" in out["hookSpecificOutput"]["additionalContext"]


def test_main(tmp_path):
    path = _transcript(tmp_path, [_user(PROMPT), _say("@a"), _result("a"), _say("Done. The world is ready to use.")])
    run = lambda arg, stdin: subprocess.run([sys.executable, "-I", "-m", "agentic_sim_world_gen.lang_guard", arg], input=stdin,
                                            capture_output=True, text=True)
    r = run("stop", json.dumps({"transcript_path": path}))
    assert r.returncode == 0 and json.loads(r.stdout)["decision"] == "block"
    assert run("post-tool", "not json").returncode == 0  # a hook never breaks the session
    assert run("post-tool", json.dumps({"transcript_path": str(tmp_path / "missing.jsonl")})).stdout == ""
    assert run("bad", "{}").returncode == 2


def test_what_is_not_the_user(tmp_path):
    """Esc, `!` commands and short replies do not turn the user's language into English."""
    after = [_say("@a"), _result("a"), _say("Next I place the desks along the wall.", "@b")]
    for extra in ("[Request interrupted by user for tool use]",
                  "<bash-input>nvidia-smi</bash-input>",
                  "<bash-stdout>NVIDIA-SMI 580.82 Driver Version: 580.82 CUDA Version: 13.0</bash-stdout><bash-stderr></bash-stderr>"):
        recs = [_user(PROMPT), _user(extra), _user("はい")] + after
        out = g.check({"transcript_path": _transcript(tmp_path, recs), "tool_use_id": "b"}, "post")
        assert out and "日本語に戻す" in out["hookSpecificOutput"]["additionalContext"], extra


def test_short_english(tmp_path):
    assert g.language("Done. All checks passed.") == "en"  # "Done." is a word, not a path
    assert g.language("各々のＡ棚は１．８ｍです。", prompt=True) == "ja"
    out = g.check({"transcript_path": _transcript(tmp_path, [_user(PROMPT), _say("@a"), _result("a")]),
                   "last_assistant_message": "Done. All checks passed."}, "stop")
    assert out["decision"] == "block"


def test_parallel_results_written_first(tmp_path):
    """Two calls after one note, and the second one's result is in the transcript before the first's hook runs."""
    recs = [_user(PROMPT), _say("Next I check both views in parallel now.", "@b"), _say("@c"), _result("c")]
    path = _transcript(tmp_path, recs)
    assert g.check({"transcript_path": path, "tool_use_id": "b"}, "post")
    assert g.check({"transcript_path": path, "tool_use_id": "c"}, "post") is None


def test_call_not_written_yet(tmp_path):
    """Claude Code may run PostToolUse before this call (and the note before it) is in the
    transcript: the latest written call's note is checked one call late, and told once."""
    note = "Now set counts to 4 desks and 8 chairs per row."
    recs = [_user(PROMPT), _say("配置を決めます。", "@a"), _result("a"), _say(note), _say("@b")]
    # the hook of b ran before b was written: a's note was Japanese
    assert g.check({"transcript_path": _transcript(tmp_path, recs[:3]), "tool_use_id": "b"}, "post") is None
    path = _transcript(tmp_path, recs + [_result("b")])
    out = g.check({"transcript_path": path, "tool_use_id": "c"}, "post")  # c is not written yet either
    assert note in out["hookSpecificOutput"]["additionalContext"]
    assert g.check({"transcript_path": path, "tool_use_id": "d"}, "post") is None  # told once
    # the hook of b saw its call (written in time): the next hook does not tell it again
    path2 = str(tmp_path / "u.jsonl")
    with open(path2, "w") as f:
        f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n")
    assert g.check({"transcript_path": path2, "tool_use_id": "b"}, "post") is not None
    with open(path2, "a") as f:
        f.write(json.dumps(_result("b")) + "\n")
    assert g.check({"transcript_path": path2, "tool_use_id": "c"}, "post") is None
    assert g.call_groups(path2) == [["b"], ["a"]]
    # the first note's hook ran too early, the second's in time: the second hook tells the first note too
    first = "Next I will read the frames of the video."
    recs = [_user(PROMPT), _say(first), _say("@e"), _result("e"), _say("次はギャラリーを開きます。"), _say("@f")]
    path3 = str(tmp_path / "v.jsonl")
    with open(path3, "w") as f:
        f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n")
    out = g.check({"transcript_path": path3, "tool_use_id": "f"}, "post")
    assert first in out["hookSpecificOutput"]["additionalContext"]
