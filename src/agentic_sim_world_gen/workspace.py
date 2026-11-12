"""A folder to make worlds in: the MCP server, the world-gen skill and a short CLAUDE.md, nothing else.

Started inside the repository, Claude Code reads the repository's CLAUDE.md (about 70k characters of
development notes) on every turn; a world-building session does not need it and paid for it in context
and compactions. Started in this folder it reads only the skill.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / ".claude" / "skills" / "world-gen"

CLAUDE_MD = """# World-building workspace

**ユーザーが日本語で書いたら、ユーザーへの文章はすべて日本語で書く。** 途中報告も、ツール呼び出しの間の 1 行も
（「Passed.」ではなく「通りました。」）、会話が要約（圧縮）された後も。
Answer in the user's language: every message, also a one-line note between tool calls and after the
conversation was compacted (the summary may be in English; the user's language has not changed).

Make Isaac Sim worlds with the agentic-sim-world-gen MCP tools and the world-gen skill (.claude/skills/world-gen).
The agentic-sim-world-gen repository is {repo}: the skill's `examples/`, `docs/` and `scripts/` paths are inside it.
Scenes are written to {out}.

# Compact instructions

Begin the summary with the language the user writes in and that every message to them, progress notes
included, stays in it (for a Japanese user write that line in Japanese: 「ユーザーへの文章はすべて日本語で書く」).
"""

# What the hooks print. After a compaction the summary is English and so was the next progress note,
# although the skill and every tool result said to keep the user's language: SessionStart(compact)
# puts the line back right after the summary, PreCompact asks the summary itself to carry it.
REMINDER_UNKNOWN = ("ユーザーが日本語で書いていたら、この後のユーザーへの文章もすべて日本語で書く（途中報告・ツール呼び出しの間の 1 行も）。\n"
                    "The conversation was just compacted: keep writing to the user in the language they write, "
                    "progress notes and one-line notes between tool calls too.")
COMPACT_ASK = ("Begin the summary with one line saying which language the user writes in and that every message to "
               "them (progress notes, one-line notes between tool calls, the final report) stays in that language.")
COMPACT_LINES = {"ja": "ユーザーは日本語で書いている。要約の最初の行を「ユーザーへの文章はすべて日本語で書く（途中報告・"
                       "ツール呼び出しの間の 1 行も）」にする。"}


SAFE = " 2>/dev/null || true"  # the hooks never fail


def language_reminder(out: str | Path | None = None, compact: bool = False) -> str:
    """The text of `agentic-sim-world-gen reply-language`: the reminder in the language of the scene saved last under `out`
    (after a compaction), or with compact=True the instruction for the summary (before one)."""
    from agentic_sim_world_gen import service

    lang = service.latest_language(out)
    if compact:
        return COMPACT_LINES.get(lang or "", "") + ("\n" if lang in COMPACT_LINES else "") + COMPACT_ASK
    return service.language_hint(lang) if lang else REMINDER_UNKNOWN


def _cli_command() -> str:
    """The agentic-sim-world-gen CLI next to this Python, for the hooks."""
    exe = Path(sys.executable).with_name("agentic-sim-world-gen")
    found = exe if exe.exists() else shutil.which("agentic-sim-world-gen")
    if not found:
        raise FileNotFoundError("agentic-sim-world-gen not found; install agentic-sim-world-gen (`pip install -e`)")
    return str(Path(found).absolute())


def hooks(out_dir: Path) -> dict[str, Any]:
    """Claude Code hooks of the workspace: the user's language around a compaction."""
    cmd = f"{shlex.quote(_cli_command())} reply-language --out {shlex.quote(str(out_dir))}"
    # `|| true`: a PreCompact hook that exits 2 blocks the compaction (an older agentic-sim-world-gen without the command did)
    return {"SessionStart": [{"matcher": "compact", "hooks": [{"type": "command", "command": cmd + SAFE}]}],
            "PreCompact": [{"hooks": [{"type": "command", "command": cmd + " --compact" + SAFE}]}]}


def _mcp_command() -> str:
    """The agentic-sim-world-gen-mcp next to this Python (the venv the CLI runs in)."""
    exe = Path(sys.executable).with_name("agentic-sim-world-gen-mcp")
    found = exe if exe.exists() else shutil.which("agentic-sim-world-gen-mcp")
    if not found:
        raise FileNotFoundError("agentic-sim-world-gen-mcp not found; install agentic-sim-world-gen with the mcp extra")
    return str(Path(found).absolute())


def _write(path: Path, text: str, force: bool) -> str:
    if path.exists() and not path.is_symlink() and path.read_text(encoding="utf-8") != text and not force:
        raise FileExistsError(f"{path} exists and differs (pass force to replace it)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def make(
    folder: str | Path,
    out: str | Path | None = None,
    env: dict[str, str] | None = None,
    copy_skill: bool = False,
    allow_tools: bool = False,
    force: bool = False,
    model: str | None = None,
) -> dict[str, Any]:
    """Create (or refresh) a workspace in `folder`.

    out: where scenes go (AGENTIC_SIM_WORLD_GEN_OUT; default <folder>/scenes, or $AGENTIC_SIM_WORLD_GEN_OUT when set).
    env: more variables for the MCP server (e.g. AGENTIC_SIM_WORLD_GEN_SOURCES); the server also inherits Claude Code's.
    copy_skill: copy the skill instead of linking it (a link follows `git pull` in the repository).
    allow_tools: let the agentic-sim-world-gen tools run without a permission prompt (.claude/settings.json).
    model: the model Claude Code uses in this folder (settings.json `model`, e.g. "sonnet": cheaper per token); None leaves Claude Code's own choice.
    """
    if not (SKILL / "SKILL.md").exists():
        raise FileNotFoundError(f"world-gen skill not found at {SKILL} (install agentic-sim-world-gen from a clone, `pip install -e`)")
    d = Path(folder).expanduser().absolute()
    if d == REPO or REPO in d.parents:  # Claude Code also reads the CLAUDE.md of every parent folder
        raise ValueError(f"make the workspace outside the agentic-sim-world-gen repository ({REPO})")
    d.mkdir(parents=True, exist_ok=True)
    out_dir = Path(out or os.environ.get("AGENTIC_SIM_WORLD_GEN_OUT") or d / "scenes").expanduser().absolute()
    server_env = {"AGENTIC_SIM_WORLD_GEN_OUT": str(out_dir), **(env or {})}
    files = [_write(d / ".mcp.json", json.dumps(
        {"mcpServers": {"agentic-sim-world-gen": {"command": _mcp_command(), "args": [], "env": server_env}}},
        indent=2, ensure_ascii=False) + "\n", force)]
    settings: dict[str, Any] = {"enabledMcpjsonServers": ["agentic-sim-world-gen"], "hooks": hooks(out_dir)}
    if allow_tools:
        settings["permissions"] = {"allow": ["mcp__agentic-sim-world-gen__*"]}
    if model:
        settings["model"] = model
    files.append(_write(d / ".claude" / "settings.json", json.dumps(settings, indent=2) + "\n", force))
    files.append(_write(d / "CLAUDE.md", CLAUDE_MD.format(repo=REPO, out=out_dir), force))

    target = d / ".claude" / "skills" / "world-gen"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        if not (copy_skill or force):
            raise FileExistsError(f"{target} is a copied skill (pass copy_skill or force to refresh it)")
        shutil.rmtree(target)
    if copy_skill:
        shutil.copytree(SKILL, target)
    else:
        target.symlink_to(SKILL, target_is_directory=True)
    files.append(str(target))
    out_dir.mkdir(parents=True, exist_ok=True)
    return {"workspace": str(d), "scenes": str(out_dir), "files": files, "skill": "copied" if copy_skill else "linked",
            "next": f"cd {d} && claude"}
