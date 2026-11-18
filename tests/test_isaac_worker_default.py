"""workspace turns the Isaac Sim worker on; GPU programs stop an idle worker first."""
import json
import types

from agentic_sim_world_gen import depth, isaac_worker, workspace

KEY = "AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER"


def _env(d):
    return json.loads((d / ".mcp.json").read_text())["mcpServers"]["agentic-sim-world-gen"]["env"]


def test_workspace_turns_worker_on_and_env_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTIC_SIM_WORLD_GEN_OUT", raising=False)
    workspace.make(tmp_path / "a")
    assert _env(tmp_path / "a")[KEY] == "1"
    workspace.make(tmp_path / "b", env={KEY: "0"})
    assert _env(tmp_path / "b")[KEY] == "0"


def test_free_gpu_stops_a_running_worker_only(monkeypatch):
    stopped = []
    monkeypatch.setattr(isaac_worker, "_stop", lambda w, keep_log=False: stopped.append(w))
    monkeypatch.setattr(isaac_worker, "_worker", None)
    isaac_worker.free_gpu()
    assert stopped == []
    w = {"proc": types.SimpleNamespace(poll=lambda: None)}
    monkeypatch.setattr(isaac_worker, "_worker", w)
    isaac_worker.free_gpu()
    assert stopped == [w] and isaac_worker._worker is None


def test_depth_frees_the_gpu_before_it_runs(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(isaac_worker, "free_gpu", lambda: order.append("free"))
    monkeypatch.setattr(depth, "python", lambda: "python")

    def run(cmd, **k):
        order.append("depth")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(depth.subprocess, "run", run)
    img = tmp_path / "k.jpg"
    img.write_bytes(b"x")
    r = depth.run([img], force=True)
    assert not r["ok"] and order[:2] == ["free", "depth"]
