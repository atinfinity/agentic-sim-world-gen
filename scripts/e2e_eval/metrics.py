"""What one end-to-end run cost and what it made: numbers from `claude -p --output-format stream-json`
and from the scene it left. Pure functions, no network; run.py calls them, tests feed them recorded events."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

JA = re.compile(r"[぀-ヿ一-鿿]")
WORD = re.compile(r"[A-Za-z]{2,}")
CODE = re.compile(r"`[^`]*`|\S*[/_.]\S*")  # paths, tool names and inline code say nothing about the language
MCP = "mcp__agentic-sim-world-gen__"


def read_events(path: str | Path) -> list[dict[str, Any]]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # a cut-off last line of a run that timed out
    return out


def language_of(text: str) -> str | None:
    """'ja' when a note has kana or kanji, 'en' when it has three plain words or more and none, else None
    ("OK", a path, a number)."""
    plain = CODE.sub(" ", text)
    if JA.search(plain):
        return "ja"
    return "en" if len(WORD.findall(plain)) >= 3 else None


def _context(usage: dict[str, Any]) -> int:
    return sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def stream_metrics(turns: Iterable[list[dict[str, Any]]], lang: str | None = None) -> dict[str, Any]:
    """Numbers of one session: `turns` holds the events of each user turn (`claude -p`, then `--resume`).

    turns / tool_calls / tools (by name, agentic-sim-world-gen's without the prefix) / tool_errors (by name) / cost_usd /
    seconds / context_first / context_max (input + cache tokens of one request) / compactions / per_turn, and with
    `lang` (the user's language) notes_off_language: the agent's texts written in another one, and how many of
    them came after a compaction."""
    tools: dict[str, int] = {}
    errors: dict[str, int] = {}
    names: dict[str, str] = {}
    seen: set[str] = set()
    contexts: list[int] = []
    off: list[str] = []
    notes = off_after = compactions = denials = 0
    cost = spent = 0.0
    seconds = 0.0
    api_turns = 0
    per_turn = []
    failed = []
    for events in turns:
        t_cost = t_ms = t_turns = 0
        t_tools = 0
        done = False
        for e in events:
            kind = e.get("type")
            if kind == "system" and e.get("subtype") == "compact_boundary":
                compactions += 1
            elif kind == "assistant":
                m = e.get("message") or {}
                if (mid := m.get("id")) and mid not in seen and (u := m.get("usage")):
                    seen.add(mid)
                    if c := _context(u):
                        contexts.append(c)
                for c in m.get("content") or []:
                    if c.get("type") == "tool_use":
                        name = str(c.get("name") or "").removeprefix(MCP)
                        names[c.get("id")] = name
                        tools[name] = tools.get(name, 0) + 1
                        t_tools += 1
                    elif c.get("type") == "text" and (c.get("text") or "").strip():
                        notes += 1
                        got = language_of(c["text"])
                        if lang and got and got != lang:
                            off.append(c["text"].strip()[:160])
                            off_after += compactions > 0
            elif kind == "user":
                content = (e.get("message") or {}).get("content")
                for c in content if isinstance(content, list) else []:
                    if c.get("type") == "tool_result" and c.get("is_error"):
                        name = names.get(c.get("tool_use_id"), "?")
                        errors[name] = errors.get(name, 0) + 1
            elif kind == "result":
                done = True
                total = float(e.get("total_cost_usd") or 0)  # of the whole session so far, also after --resume
                t_cost, spent = max(total - spent, 0.0), max(total, spent)
                t_ms = int(e.get("duration_ms") or 0)
                t_turns = int(e.get("num_turns") or 0)
                denials += len(e.get("permission_denials") or [])
                if e.get("is_error") or e.get("subtype") not in (None, "success"):
                    failed.append(str(e.get("subtype") or e.get("result"))[:120])
        if not done:
            failed.append("no result (timed out or stopped)")
        cost += t_cost
        seconds += t_ms / 1000
        api_turns += t_turns
        per_turn.append({"turns": t_turns, "tool_calls": t_tools, "cost_usd": round(t_cost, 4), "seconds": round(t_ms / 1000, 1)})
    out: dict[str, Any] = {
        "turns": api_turns,
        "tool_calls": sum(tools.values()),
        "tools": dict(sorted(tools.items(), key=lambda kv: (-kv[1], kv[0]))),
        "tool_errors": sum(errors.values()),
        "cost_usd": round(cost, 4),
        "seconds": round(seconds, 1),
        "context_first": contexts[0] if contexts else None,
        "context_max": max(contexts) if contexts else None,
        "compactions": compactions,
        "notes": notes,
        "per_turn": per_turn,
    }
    if errors:
        out["tool_errors_by"] = errors
    if denials:
        out["permission_denials"] = denials
    if failed:
        out["failed"] = failed
    if lang:
        out["notes_off_language"] = len(off)
        out["notes_off_language_after_compaction"] = off_after
        if off:
            out["off_language_examples"] = off[:5]
    return out


def _chars(content: Any) -> tuple[int, int]:
    """(characters of text, images) of a message's or a tool result's content."""
    if isinstance(content, str):
        return len(content), 0
    n = img = 0
    for c in content if isinstance(content, list) else []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text":
            n += len(c.get("text") or "")
        elif c.get("type") == "image":
            img += 1
        elif c.get("type") == "tool_reference":
            n += len(str(c.get("tool_name") or ""))
    return n, img


def context_breakdown(turns: Iterable[list[dict[str, Any]]]) -> dict[str, Any]:
    """What was put into the conversation of one session, in characters and images, by where it came
    from: each tool's results (agentic-sim-world-gen's without the prefix), the files Read (by name), text the harness adds as
    user messages (skills, reminders), the user's prompts, and what the agent wrote (texts, tool arguments).
    The system prompt and the tool definitions are not in the stream: `context_first` (tokens) holds them.
    Characters are what the log has; a compaction drops some of them from the context later."""
    by: dict[str, dict[str, int]] = {}
    files: dict[str, dict[str, int]] = {}
    calls: dict[str, tuple[str, str | None]] = {}
    seen: set[str] = set()

    def add(table: dict[str, dict[str, int]], key: str, chars: int, images: int = 0) -> None:
        row = table.setdefault(key, {"chars": 0, "images": 0, "n": 0})
        row["chars"] += chars
        row["images"] += images
        row["n"] += 1

    for events in turns:
        for e in events:
            kind = e.get("type")
            m = e.get("message") or {}
            if kind == "assistant":
                for i, c in enumerate(m.get("content") or []):
                    key = f"{m.get('id')}:{i}:{c.get('id') or ''}"
                    if key in seen:  # a message comes once per content block, sometimes repeated
                        continue
                    seen.add(key)
                    if c.get("type") == "tool_use":
                        name = str(c.get("name") or "").removeprefix(MCP)
                        path = (c.get("input") or {}).get("file_path") if name == "Read" else None
                        calls[c.get("id")] = (name, Path(path).name if path else None)
                        add(by, "agent: tool arguments", len(json.dumps(c.get("input") or {}, ensure_ascii=False)))
                    elif c.get("type") == "text":
                        add(by, "agent: text", len(c.get("text") or ""))
            elif kind == "user":
                content = m.get("content")
                if isinstance(content, str):
                    add(by, "user / harness text", len(content))
                    continue
                for c in content if isinstance(content, list) else []:
                    if c.get("type") == "tool_result":
                        name, file = calls.get(c.get("tool_use_id"), ("?", None))
                        n, img = _chars(c.get("content"))
                        add(by, f"result: {name}", n, img)
                        if file:
                            add(files, file, n, img)
                    elif c.get("type") == "text":
                        add(by, "user / harness text", len(c.get("text") or ""))
                    elif c.get("type") == "image":
                        add(by, "user / harness text", 0, 1)
    order = lambda t: dict(sorted(t.items(), key=lambda kv: (-kv[1]["chars"], kv[0])))  # noqa: E731
    return {"chars": sum(r["chars"] for r in by.values()), "images": sum(r["images"] for r in by.values()),
            "by": order(by), "files": order(files)}


def breakdown_table(rows: list[tuple[str, dict[str, Any]]], top: int = 8) -> str:
    """Markdown: per case, the biggest sources of the conversation's text."""
    lines = ["| 指示 | 文字 | 画像 | 多い順（千文字。× = 回数、画 = 画像の数） |", "|---|---|---|---|"]
    for case, b in rows:
        cell = lambda k, r: (f"{k} {r['chars'] / 1000:.1f}k×{r['n']}"  # noqa: E731
                             + (f" 画{r['images']}" if r["images"] else ""))
        best = "、".join(cell(k, r) for k, r in list(b["by"].items())[:top])
        read = "、".join(cell(k, r) for k, r in list(b["files"].items())[:4])
        lines.append(f"| {case} | {b['chars'] / 1000:.0f}k | {b['images']} | {best}"
                     + (f"<br>Read の内訳：{read}" if read else "") + " |")
    return "\n".join(lines)


def scene_metrics(status: dict[str, Any]) -> dict[str, Any]:
    """Numbers of the scene from service.status: objects / issues / usability / check (verdict of the last
    Isaac Sim check, None when it never ran) / requirements met and total / substitutes / not_in_catalog /
    no_asset / decor_skipped / usd."""
    objects = status.get("objects") or []
    subs = [o.get("substitute") or {} for o in objects if o.get("substitute")]
    req = status.get("requirements_summary") or {}
    check = status.get("last_check") or {}
    out: dict[str, Any] = {
        "objects": len(objects),
        "issues": len(status.get("issues") or []),
        "usability": len(status.get("usability") or []),
        "check": check.get("verdict"),
        "check_stale": bool(check.get("stale")),
        "requirements_met": req.get("met"),
        "requirements_total": req.get("total"),
        "requirements_rate": round(req["met"] / req["total"], 2) if req.get("total") else None,
        "substitutes": len(subs),
        "not_in_catalog": sum(bool(s.get("not_in_catalog")) for s in subs),
        "no_asset": sum(1 for o in objects if o.get("selected") is None),
        "decor_skipped": len(status.get("decor_skipped") or []),
        "usd": bool(status.get("usd")),
    }
    return out


COLUMNS = (  # key, heading, lower is better (None: not compared)
    ("turns", "ターン", True), ("tool_calls", "ツール", True), ("tool_errors", "エラー", True),
    ("cost_usd", "費用 $", True), ("seconds", "時間 s", True), ("context_max", "最大 context", True),
    ("compactions", "圧縮", True), ("notes_off_language", "言語ずれ", True), ("check", "check", None),
    ("issues", "issues", True), ("usability", "usability", True), ("requirements_rate", "充足率", False),
    ("substitutes", "代用", True), ("judge", "採点", False),
)


def _mean(vals: list[Any]) -> Any:
    nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if nums and len(nums) == len([v for v in vals if v is not None]):
        m = sum(nums) / len(nums)
        return round(m, 2) if any(isinstance(v, float) for v in nums) else round(m, 1) if m != int(m) else int(m)
    texts = [str(v) for v in vals if v is not None]
    return "/".join(dict.fromkeys(texts)) if texts else None


def by_case(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Rows of results.jsonl folded per case: the mean of the repeats (texts joined), `n` and the spread of cost."""
    out: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(r["case"], []).append(r)
    for case, rs in groups.items():
        row: dict[str, Any] = {"n": len(rs)}
        for key, _, _ in COLUMNS:
            row[key] = _mean([r.get(key) for r in rs])
        costs = [r["cost_usd"] for r in rs if isinstance(r.get("cost_usd"), (int, float))]
        if len(costs) > 1:
            row["cost_range"] = [min(costs), max(costs)]
        row["failed"] = sum(bool(r.get("failed")) for r in rs)
        out[case] = row
    return out


def _cell(v: Any, before: Any = None, lower: bool | None = None) -> str:
    if v is None:
        return "—"
    text = f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else str(v)
    if lower is None or not isinstance(v, (int, float)) or not isinstance(before, (int, float)) or v == before:
        return text
    d = v - before
    mark = "↓" if d < 0 else "↑"
    return f"{text} ({mark}{abs(round(d, 2)):g})"


def table(rows: list[dict[str, Any]], before: list[dict[str, Any]] | None = None) -> str:
    """Markdown: one line per case (mean of its repeats); with `before`, the change from that run in brackets."""
    now, old = by_case(rows), by_case(before or [])
    cols = [c for c in COLUMNS if any(r.get(c[0]) is not None for r in now.values())]
    lines = ["| 指示 | 回 | " + " | ".join(h for _, h, _ in cols) + " |", "|---|---|" + "---|" * len(cols)]
    for case, r in now.items():
        o = old.get(case, {})
        name = case + (" ⚠" if r.get("failed") else "")
        lines.append(f"| {name} | {r['n']} | " + " | ".join(_cell(r.get(k), o.get(k), low) for k, _, low in cols) + " |")
    total = {k: sum(r[k] * r["n"] for r in now.values() if isinstance(r.get(k), (int, float))) for k in ("cost_usd", "seconds")}
    lines.append("")
    lines.append(f"合計：${total['cost_usd']:.2f}、{total['seconds'] / 60:.1f} 分（{sum(r['n'] for r in now.values())} 本）")
    return "\n".join(lines)
