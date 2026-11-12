"""An exception agentic-sim-world-gen did not mean still tells the model what it was (and leaves a traceback), and a
compose that dies on the way leaves scene.usda as it was (no network)."""

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agentic_sim_world_gen import mcp_server, service
from tests.test_fewer_turns import SPEC, fake  # noqa: F401  (the fake provider, no Isaac Sim)


def call(name, args):
    return anyio.run(lambda: mcp_server.mcp.call_tool(name, args))


def test_an_unexpected_error_says_what_it_was(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_CACHE", str(tmp_path / "cache"))

    def boom(scene):
        raise RuntimeError("Error in 'pxr::Open' at line 3 in file layer.cpp :\n\t'Failed to open layer @x.usdc@'")

    monkeypatch.setattr(service, "license_report", boom)
    with pytest.raises(ToolError) as e:
        call("license_report", {"scene_dir": str(tmp_path)})
    log = tmp_path / "cache" / mcp_server.ERROR_LOG
    assert str(e.value) == ("Error executing tool license_report: unexpected RuntimeError: "
                            f"'Failed to open layer @x.usdc@' (traceback in {log})")
    text = log.read_text()
    assert "tool license_report" in text and "Traceback" in text and "in boom" in text

    # a second one is appended; a log that cannot be written does not hide the error
    with pytest.raises(ToolError):
        call("license_report", {"scene_dir": str(tmp_path)})
    assert log.read_text().count("tool license_report") == 2
    (tmp_path / "file").write_text("")
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_CACHE", str(tmp_path / "file" / "under"))
    with pytest.raises(ToolError) as e:
        call("license_report", {"scene_dir": str(tmp_path)})
    assert str(e.value).endswith("'Failed to open layer @x.usdc@'")

    # a long line keeps its end, where pxr puts the reason
    monkeypatch.setattr(service, "license_report", lambda scene: (_ for _ in ()).throw(OSError("p" * 500 + " : 'why'")))
    with pytest.raises(ToolError) as e:
        call("license_report", {"scene_dir": str(tmp_path)})
    assert str(e.value) == "Error executing tool license_report: unexpected OSError: …" + ("p" * 500 + " : 'why'")[-300:]


def test_a_failed_compose_leaves_the_usd_as_it_was(tmp_path, monkeypatch):
    pytest.importorskip("pxr")
    from pxr import Usd

    from agentic_sim_world_gen.usd import compose as compose_mod

    d = tmp_path / "t"
    service.start_scene(SPEC, d, skip=["draft", "preview"])
    usd = d / "scene.usda"
    real = compose_mod._compose

    def dies(scene, out_path, uri_map):
        Usd.Stage.CreateNew(str(out_path))  # what the real one does first
        raise RuntimeError("cannot open layer")

    # no USD yet: none is left behind
    monkeypatch.setattr(compose_mod, "_compose", dies)
    with pytest.raises(RuntimeError):
        service.compose_scene(d)
    assert not usd.exists()

    monkeypatch.setattr(compose_mod, "_compose", real)
    service.compose_scene(d)
    before = usd.read_bytes()
    assert b"Objects" in before

    # the scene has not changed since: the failed compose must not leave an empty stage that finish takes for it
    monkeypatch.setattr(compose_mod, "_compose", dies)
    with pytest.raises(RuntimeError):
        service.compose_scene(d)
    assert usd.read_bytes() == before
    monkeypatch.setattr(compose_mod, "_compose", real)
    assert service.finish(d)["usd"] == str(usd) and b"Objects" in usd.read_bytes()
