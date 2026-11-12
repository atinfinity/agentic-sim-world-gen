import json
import subprocess

import pytest

from agentic_sim_world_gen import cli, workspace


def test_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTIC_SIM_WORLD_GEN_OUT", raising=False)
    d = tmp_path / "ws"
    r = workspace.make(d, env={"AGENTIC_SIM_WORLD_GEN_SOURCES": "simready-central,isaac"})
    mcp = json.loads((d / ".mcp.json").read_text())["mcpServers"]["agentic-sim-world-gen"]
    assert mcp["command"].endswith("agentic-sim-world-gen-mcp")
    assert mcp["env"] == {"AGENTIC_SIM_WORLD_GEN_OUT": str(d / "scenes"), "AGENTIC_SIM_WORLD_GEN_SOURCES": "simready-central,isaac"}
    skill = d / ".claude" / "skills" / "world-gen"
    assert skill.is_symlink() and (skill / "SKILL.md").exists()
    settings = json.loads((d / ".claude" / "settings.json").read_text())
    assert settings["enabledMcpjsonServers"] == ["agentic-sim-world-gen"] and set(settings) == {"enabledMcpjsonServers", "hooks"}
    after, = settings["hooks"]["SessionStart"]  # the user's language right after a compaction
    assert after["matcher"] == "compact" and after["hooks"][0]["type"] == "command"
    assert after["hooks"][0]["command"].endswith(f"agentic-sim-world-gen reply-language --out {d / 'scenes'} 2>/dev/null || true")
    before = settings["hooks"]["PreCompact"][0]["hooks"][0]["command"]
    assert before.endswith("--compact 2>/dev/null || true")  # exit 2 would block the compaction
    assert subprocess.run(before.replace("reply-language", "no-such-command"), shell=True, capture_output=True).returncode == 0
    md = (d / "CLAUDE.md").read_text()
    assert str(workspace.REPO) in md and len(md) < 1300
    assert md.index("日本語") < md.index("Make Isaac Sim worlds") and "# Compact instructions" in md
    assert r["next"] == f"cd {d} && claude" and (d / "scenes").is_dir()

    workspace.make(d, env={"AGENTIC_SIM_WORLD_GEN_SOURCES": "simready-central,isaac"})  # the same again is fine
    with pytest.raises(FileExistsError):
        workspace.make(d, out=tmp_path / "elsewhere")
    workspace.make(d, out=tmp_path / "elsewhere", copy_skill=True, allow_tools=True, force=True)
    assert not skill.is_symlink() and (skill / "SKILL.md").exists()
    assert json.loads((d / ".claude" / "settings.json").read_text())["permissions"] == {"allow": ["mcp__agentic-sim-world-gen__*"]}
    with pytest.raises(FileExistsError):  # a copied skill is not replaced silently
        workspace.make(d, out=tmp_path / "elsewhere", allow_tools=True)

    with pytest.raises(ValueError):  # Claude Code would still read the repository's CLAUDE.md
        workspace.make(workspace.REPO / "tmp_ws")
    assert not (workspace.REPO / "tmp_ws").exists()

    assert cli.main(["workspace", str(tmp_path / "ws2"), "--env", "AGENTIC_SIM_WORLD_GEN_PBR=0"]) == 0
    assert json.loads((tmp_path / "ws2" / ".mcp.json").read_text())["mcpServers"]["agentic-sim-world-gen"]["env"]["AGENTIC_SIM_WORLD_GEN_PBR"] == "0"
    assert "model" not in json.loads((tmp_path / "ws2" / ".claude" / "settings.json").read_text())
    assert cli.main(["workspace", str(tmp_path / "ws3"), "--model", "sonnet"]) == 0
    assert json.loads((tmp_path / "ws3" / ".claude" / "settings.json").read_text())["model"] == "sonnet"
    assert cli.main(["workspace", str(tmp_path / "ws3"), "--env", "bad"]) == 2
    assert cli.main(["workspace", str(workspace.REPO / "tmp_ws")]) == 1


def test_language_reminder(tmp_path, capsys, monkeypatch):
    """What the hooks print, in the language of the scene saved last."""
    from agentic_sim_world_gen.scene.spec import ResolvedScene, SceneSpec

    monkeypatch.delenv("AGENTIC_SIM_WORLD_GEN_OUT", raising=False)
    assert "日本語" in workspace.language_reminder(tmp_path) and "language they write" in workspace.language_reminder(tmp_path)
    assert workspace.language_reminder(tmp_path, compact=True) == workspace.COMPACT_ASK
    spec = SceneSpec.model_validate({"name": "書斎", "room": {"width": 3, "depth": 3}, "objects": []})
    (tmp_path / "study").mkdir()
    (tmp_path / "study" / "scene.resolved.json").write_text(ResolvedScene(spec=spec, placements=[]).model_dump_json())
    r = workspace.language_reminder(tmp_path)
    assert "日本語" in r and "圧縮" in r and "language they write" not in r
    c = workspace.language_reminder(tmp_path, compact=True)
    assert c.startswith("ユーザーは日本語") and c.endswith(workspace.COMPACT_ASK)
    assert cli.main(["reply-language", "--out", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == r
    assert cli.main(["reply-language", "--out", str(tmp_path), "--compact"]) == 0
    assert capsys.readouterr().out.strip() == c
