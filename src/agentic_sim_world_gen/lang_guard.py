"""Claude Code hooks that catch a message to the user written in the wrong language.

The skill, every tool result, the summary's first line and the hook right after a compaction all say to keep
writing in the user's language, and still the notes between tool calls and the final report of a long run
came out in English: the thinking, the tool results and the summary's body are English, and the notes drift towards
them. These hooks look at what the agent actually wrote instead:

- ``post-tool`` (PostToolUse): the note written just before this tool call is in another language than the user's
  -> additionalContext asking the next note to be in the user's language again (quoting the note).
- ``stop`` (Stop): the turn's last message is in another language -> block the stop once and ask for the same
  report in the user's language (``stop_hook_active`` lets the second stop through).

Claude Code may not have written this call (the note before it and the tool_use) to the transcript yet when
PostToolUse runs: the latest call in the transcript is checked instead, one call late, and the calls already
checked are kept in a small state file per transcript so a note is told once.

The user's language is that of their own last prompt in the transcript. Only the standard library: the hooks run
on every tool call (`python -I -m agentic_sim_world_gen.lang_guard post-tool`, 4-14 ms a call on a 25 MB transcript).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterator
from typing import Any

CJK = re.compile(r"[\u3005-\u3007\u3040-\u30ff\u3400-\u9fff\uff10-\uff19\uff21-\uff3a\uff41-\uff5a\uff66-\uff9f]")  # 々〆, full-width 0-9 A-Z a-z
LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
PATHISH = re.compile(r"[\x21-\x7e]*(?:[/\\_]|\w\.\w)[\x21-\x7e]*")  # paths, ids, 1.8 (ASCII only; not "Done.")
HEAD_BYTES = 2 << 20  # the first prompt, when no prompt is left in the tail (a long turn)
TAIL_BYTES = 4 << 20  # how far back to look for the last prompt once the text is found
CHUNK = 1 << 20
STATE_KEEP = 200  # calls remembered per transcript

MESSAGES = {
    "ja": {
        "post": "直前にユーザーへ書いた文が日本語ではなかった（「{quote}」）。ユーザーは日本語で書いている。"
                "次の文から日本語に戻す（ツール呼び出しの間の 1 行も、最終報告も）。",
        "stop": "最終報告が日本語ではなかった（「{quote}」）。ユーザーは日本語で書いている。"
                "同じ内容の報告を日本語で書き直す（英語の版をなぞらず、日本語の報告だけを書く）。",
    },
}
NAMES = {"ja": "Japanese", "en": "English"}


def language(text: str, prompt: bool = False) -> str | None:
    """'ja' / 'en' for prose, None when too little prose to tell (code, paths and ids are left out). A user's
    prompt is told from less (`prompt`): 「はい」 is Japanese, while a two-word note of the agent is left alone."""
    prose = PATHISH.sub(" ", CODE.sub(" ", text))
    cjk = len(CJK.findall(prose))
    words = LATIN_WORD.findall(prose)
    latin = sum(len(w) for w in words)
    if cjk + latin < (2 if prompt else 12):
        return None
    if cjk >= (2 if prompt else 6) and cjk >= 0.15 * latin:
        return "ja"
    if cjk <= (0 if prompt else 2) and len(words) >= (3 if prompt else 4):
        return "en"
    return None


def _reverse_lines(path: str) -> Iterator[bytes]:
    """The lines of a file from the last one, reading it in chunks from the end."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos, rest = f.tell(), b""
        while pos > 0:
            step = min(CHUNK, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + rest
            lines = buf.split(b"\n")
            rest = lines[0]
            for line in reversed(lines[1:]):
                if line.strip():
                    yield line
        if rest.strip():
            yield rest


def _record(line: bytes) -> dict[str, Any] | None:
    try:
        r = json.loads(line)
    except ValueError:
        return None
    return r if isinstance(r, dict) else None


def _prompt_text(r: dict[str, Any]) -> str | None:
    """The user's own words in a transcript record (not a tool result, a summary or an injected reminder)."""
    if r.get("type") != "user" or r.get("isMeta") or r.get("isCompactSummary"):
        return None
    origin = r.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return None  # a background task's notification and the like
    c = (r.get("message") or {}).get("content")
    if isinstance(c, str):
        text = c
    elif isinstance(c, list) and not any(isinstance(i, dict) and i.get("type") == "tool_result" for i in c):
        text = " ".join(i.get("text", "") for i in c if isinstance(i, dict) and i.get("type") == "text")
    else:
        return None
    text = re.sub(r"<(system-reminder|task-notification|bash-[a-z]+|command-[a-z]+|local-command-[a-z]+)>.*?</\1>",
                  " ", text, flags=re.DOTALL)  # bash-input/stdout/stderr: what the user ran with `!` and its output
    if text.strip().startswith("[Request interrupted"):
        return None  # Claude Code's own line when the user pressed Esc
    return text if text.strip() else None


def _blocks(r: dict[str, Any]) -> list[tuple[str, str]]:
    """An assistant record's text and tool_use blocks in order: ("text", text) / ("tool_use", id)."""
    c = (r.get("message") or {}).get("content")
    if r.get("type") != "assistant" or not isinstance(c, list):
        return []
    out = []
    for i in c:
        if isinstance(i, dict) and i.get("type") == "text" and i.get("text", "").strip():
            out.append(("text", i["text"]))
        elif isinstance(i, dict) and i.get("type") == "tool_use":
            out.append(("tool_use", i.get("id", "")))
    return out


def _is_tool_result(r: dict[str, Any]) -> bool:
    c = (r.get("message") or {}).get("content")
    return r.get("type") == "user" and isinstance(c, list) and any(
        isinstance(i, dict) and i.get("type") == "tool_result" for i in c)


def scan(path: str, stop: bool, tool_use_id: str | None = None) -> tuple[str | None, str | None]:
    """(the user's language, the agent's text to check): the turn's last text (stop), or the text written
    between the previous tool result and this call (post-tool). With `tool_use_id` the post-tool scan starts at
    the record holding this call, past results of calls made in parallel that were written before the hook ran,
    and the text is returned only for the first call after it (parallel calls after one note are told once)."""
    texts: list[str] = []
    calls: list[str] = []  # the tool calls made after the text, in order
    anchored = stop or not tool_use_id
    collecting, user_lang, read = True, None, 0
    for line in _reverse_lines(path):
        read += len(line)
        if read > TAIL_BYTES and not (collecting and anchored):
            break
        r = _record(line)
        if r is None:
            continue
        if (p := _prompt_text(r)) is not None:
            if user_lang := language(p, prompt=True):
                break
            collecting = False  # an earlier turn's text is not this call's
            continue
        if not collecting:
            continue
        blocks = _blocks(r)
        if not anchored:
            if ("tool_use", tool_use_id) not in blocks:
                continue
            anchored = True
        if _is_tool_result(r):
            collecting = False  # what came before belongs to an earlier call (post-tool) or earlier steps (stop)
            continue
        for kind, value in reversed(blocks):
            if kind == "tool_use" and not texts:
                calls.insert(0, value)
            elif kind == "text":
                texts.insert(0, value)
        if stop and texts:
            collecting = False
    if user_lang is None:  # the prompt is far back (a long turn): the latest one in the head of the file
        with open(path, "rb") as f:
            for line in f.read(HEAD_BYTES).split(b"\n"):
                if (r := _record(line)) and (p := _prompt_text(r)):
                    user_lang = language(p, prompt=True) or user_lang
    if not anchored or (not stop and tool_use_id and calls and calls[0] != tool_use_id):
        return user_lang, None
    return user_lang, "\n".join(texts) if texts else None


def call_groups(path: str, n: int = 2) -> list[list[str]]:
    """The tool calls made after each of the agent's latest `n` texts, newest first, each in order (back to the
    user's prompt at most)."""
    groups: list[list[str]] = []
    calls: list[str] = []
    read = 0
    for line in _reverse_lines(path):
        read += len(line)
        if read > TAIL_BYTES:
            break
        if (r := _record(line)) is None:
            continue
        if _prompt_text(r) is not None:
            break
        for kind, value in reversed(_blocks(r)):
            if kind == "tool_use":
                calls.insert(0, value)
            elif calls:
                groups.append(calls)
                calls = []
                if len(groups) == n:
                    return groups
    return groups


def _state_path(transcript: str) -> str:
    key = hashlib.sha1(os.path.abspath(transcript).encode()).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), "agentic-sim-world-gen-lang-guard", key)


def _checked(transcript: str) -> list[str]:
    try:
        with open(_state_path(transcript)) as f:
            return f.read().split()
    except OSError:
        return []


def _mark(transcript: str, call: str) -> None:
    ids = [i for i in _checked(transcript) if i != call][-(STATE_KEEP - 1):] + [call]
    try:
        os.makedirs(os.path.dirname(_state_path(transcript)), exist_ok=True)
        with open(_state_path(transcript), "w") as f:
            f.write("\n".join(ids))
    except OSError:
        pass


def _quote(text: str, n: int = 80) -> str:
    one = " ".join(text.split())
    return one if len(one) <= n else one[:n] + "…"


def message(kind: str, user_lang: str, text: str) -> str:
    if user_lang in MESSAGES:
        return MESSAGES[user_lang][kind].format(quote=_quote(text))
    name = NAMES.get(user_lang, user_lang)
    if kind == "post":
        return (f"The note you just wrote to the user was not in {name} (\"{_quote(text)}\"). The user writes {name}: "
                f"write the next notes and the final report in {name}.")
    return (f"The final report was not in {name} (\"{_quote(text)}\"). The user writes {name}: write the same "
            f"report again in {name}, only that version.")


def _check_calls(path: str, call: str) -> dict[str, Any] | None:
    """PostToolUse: this call's note, and the one before it when that was not checked yet (its hook ran before it
    was in the transcript); when this call is not in the transcript yet, the latest one that is."""
    groups = call_groups(path)
    own = bool(groups) and call in groups[0]
    targets = ([call] if own else []) + [g[0] for g in groups[1 if own else 0:]]
    done = _checked(path)
    found = None
    for target in targets:
        if target in done and target != call:
            continue
        user_lang, text = scan(path, stop=False, tool_use_id=target)
        if not text:
            continue
        _mark(path, target)
        wrote = language(text)
        if found is None and user_lang and wrote and wrote != user_lang:
            found = (user_lang, text)
    if found is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": message("post", *found)}}


def check(event: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """The hook's JSON output, or None to stay silent."""
    if kind == "stop" and event.get("stop_hook_active"):
        return None
    path = event.get("transcript_path")
    if not path or not os.path.isfile(path):
        return None
    if kind == "post" and (call := event.get("tool_use_id")):
        return _check_calls(path, call)
    user_lang, text = scan(path, stop=kind == "stop")
    if kind == "stop" and isinstance(event.get("last_assistant_message"), str):
        text = event["last_assistant_message"]  # the transcript may not have the last message yet when Stop runs
    if not user_lang or not text:
        return None
    wrote = language(text)
    if wrote is None or wrote == user_lang:
        return None
    if kind == "stop":
        return {"decision": "block", "reason": message("stop", user_lang, text)}
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": message("post", user_lang, text)}}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    kind = args[0] if args else ""
    if kind not in ("post-tool", "stop"):
        print("usage: python -m agentic_sim_world_gen.lang_guard post-tool|stop  (a Claude Code hook, event JSON on stdin)",
              file=sys.stderr)
        return 2
    try:
        out = check(json.load(sys.stdin), "post" if kind == "post-tool" else "stop")
    except Exception:  # a hook never breaks the session
        return 0
    if out:
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
