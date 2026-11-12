"""Live check: talking to a running Isaac Sim's python_server, and when to fall back to headless (no Isaac)."""

import json
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.layout.validate import check


def rec(name, cls, w, d, h):
    return AssetRecord(
        id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
        semantic_class=cls, license="CC-BY 4.0",
    )


CATALOG = {"desk": [rec("desk", "desk", 1.4, 0.7, 0.74)],
           "office chair": [rec("chair", "office chair", 0.55, 0.55, 0.85)]}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


class FakeServer:
    """python_server stand-in: reads the envelope until EOF, runs `handler(envelope)` and replies with its dict."""

    def __init__(self, handler):
        self.handler, self.envelopes = handler, []
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                chunks = []
                while data := conn.recv(65536):
                    chunks.append(data)
                if not chunks:  # live_available() only connects
                    continue
                env = json.loads(b"".join(chunks))
                self.envelopes.append(env)
                conn.sendall(json.dumps(self.handler(env)).encode())

    def close(self):
        self.sock.close()


@pytest.fixture
def server(monkeypatch):
    servers = []

    def start(handler):
        s = FakeServer(handler)
        servers.append(s)
        monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_ISAAC_LIVE", f"127.0.0.1:{s.port}")
        return s

    yield start
    for s in servers:
        s.close()


def make_scene(tmp_path, chair_y=1.3):
    d = tmp_path / "t"
    service.create_scene({"name": "t", "room": {"width": 4, "depth": 3}, "objects": [
        {"id": "desk", "query": "desk", "position": [2.0, 2.0]},
        {"id": "chair", "query": "office chair", "position": [2.0, chair_y]},
    ]}, d)
    (d / "scene.usda").write_text("#usda 1.0\n")  # never opened: Isaac Sim is faked
    return d


def test_live_exec_envelope_and_token(server, monkeypatch):
    s = server(lambda env: {"status": "ok", "output": "hi\n"})
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_ISAAC_TOKEN", "secret")
    assert service.live_available()
    assert service.live_exec("print('hi')", timeout_s=5) == {"status": "ok", "output": "hi\n"}
    assert s.envelopes == [{"code": "print('hi')", "timeout": 5, "auth_token": "secret"}]


def test_live_address(monkeypatch):
    monkeypatch.delenv("AGENTIC_SIM_WORLD_GEN_ISAAC_LIVE", raising=False)
    assert service._live_address() == ("127.0.0.1", 8226)
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_ISAAC_LIVE", "10.0.0.5:9000")
    assert service._live_address() == ("10.0.0.5", 9000)


def test_check_live_reads_report(tmp_path, server):
    d = make_scene(tmp_path)
    report = {"usd": str(d / "scene.usda"), "mode": "live", "objects": {}, "unstable": ["chair"], "selected": ["chair"]}

    def handler(env):
        code = env["code"]
        assert str((d / "scene.usda").resolve()) in code and "live=True" in code and "await check_core.run" in code
        (d / "check.json").write_text(json.dumps(report))  # what check_core.run writes in Isaac Sim
        return {"status": "ok", "output": "agentic-sim-world-gen-check: ok\n"}

    server(handler)
    (d / "check.json").write_text("{\"stale\": true}")
    r = service.check_in_isaac(d, live=True)
    assert r["ok"] and r["mode"] == "live" and "live_fallback" not in r
    assert r["report"]["unstable"] == ["chair"] and r["report"]["selected"] == ["chair"]
    assert r["report"]["full_report"] == str((d / "check.json").resolve())


def test_check_live_errors(tmp_path, server):
    d = make_scene(tmp_path)
    server(lambda env: {"status": "error", "ename": "ModuleNotFoundError", "evalue": "No module named 'view'",
                        "output": "", "traceback": ["line 1", "line 2"]})
    r = service.check_in_isaac(d, live=True)
    assert not r["ok"] and r["error"] == "ModuleNotFoundError: No module named 'view'" and r["traceback"] == ["line 1", "line 2"]

    def physics_stopped(env):
        (d / "check.json").write_text(json.dumps({"objects": {}, "error": "physics did not run"}))
        return {"status": "ok"}

    server(physics_stopped)
    r = service.check_in_isaac(d, live=True)
    assert not r["ok"] and r["error"] == "physics did not run"


@pytest.fixture
def headless(monkeypatch):
    """Fake the headless Isaac Sim run: record the args and write an empty report."""
    calls = []

    def run(script, args, timeout_s):
        calls.append(args)
        report = args[args.index("--report") + 1]
        with open(report, "w") as f:
            json.dump({"objects": {}, "unstable": []}, f)
        return subprocess.CompletedProcess(args, 0, "", ""), {}

    monkeypatch.setattr(service, "_run_isaac", run)
    return calls


def test_falls_back_without_server(tmp_path, monkeypatch, headless):
    d = make_scene(tmp_path)
    s = socket.create_server(("127.0.0.1", 0))  # a free port with nobody listening after close
    port = s.getsockname()[1]
    s.close()
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_ISAAC_LIVE", f"127.0.0.1:{port}")
    r = service.check_in_isaac(d, live=True)
    assert r["ok"] and "python_server" in r["live_fallback"] and len(headless) == 1
    assert "mode" not in r


def test_overlap_falls_back_to_headless(tmp_path, server, headless):
    d = make_scene(tmp_path, chair_y=1.475)  # chair 0.1 m under the desk: fine for the layout check
    s = server(lambda env: pytest.fail("a risky scene must not reach the open Isaac Sim"))
    assert service.status(d)["issues"] == []
    assert service._live_risk(d) == "desk / chair"
    r = service.check_in_isaac(d, live=True)
    assert r["ok"] and "desk / chair" in r["live_fallback"] and len(headless) == 1 and s.envelopes == []
    d2 = make_scene(tmp_path / "ok")
    assert service._live_risk(d2) == ""


def test_allow_tuck_flag(tmp_path):
    d = make_scene(tmp_path, chair_y=1.475)
    s = service.load(d)
    assert check(s) == [] and [i.kind for i in check(s, allow_tuck=False)] == ["overlap"]


def test_check_result_is_short_for_many_bodies(tmp_path, monkeypatch):
    """~1000 filled products must not make a 300k-character tool result."""
    d = make_scene(tmp_path)
    objs = {f"cans/cans_u_l{i // 100}_{i % 100}_0": {"resolved": True, "uri": "/a/very/long/path/can.usd" * 3,
                                                   "rigid_bodies": 1, "colliders": 1, "max_displacement_m": 0.002,
                                                   "stable": True, "world_bbox_size": [0.06, 0.06, 0.12]}
            for i in range(1000)}
    objs["cans/cans_u_l0_0_0"].update(stable=False, max_displacement_m=0.3)
    objs["desk"] = {"resolved": True, "rigid_bodies": 0, "colliders": 2, "max_displacement_m": 0.0, "stable": True}
    unstable = ["cans/cans_u_l0_0_0"]
    static = sorted(objs)[:30]  # long list -> grouped

    def run(script, args, timeout_s):
        rep = args[args.index("--report") + 1]
        Path(rep).write_text(json.dumps({"objects": objs, "unstable": unstable, "sunk": [], "no_collider": [],
                                         "static": static}))
        return subprocess.CompletedProcess(args, 0, "", ""), {}

    monkeypatch.setattr(service, "_run_isaac", run)
    r = service.check_in_isaac(d)
    rep = r["report"]
    assert len(json.dumps(r, ensure_ascii=False)) < 5000
    assert rep["objects_total"] == 1001 and rep["groups"]["cans"] == {"count": 1000, "max_displacement_m": 0.3,
                                                                       "unstable": 1}
    assert rep["unstable"] == unstable and list(rep["problems"]) == unstable
    assert rep["static"] == ["cans/* (30)"]
    assert rep["max_displacement"] == {"object": "cans/cans_u_l0_0_0", "m": 0.3}
    assert len(json.loads((d / "check.json").read_text())["objects"]) == 1001  # full report kept on disk


def test_check_result_drops_hull_paths_and_orbit_cameras(tmp_path, monkeypatch):
    """756 SDF colliders made hulls and the orbit cameras made a 40 kB check_in_isaac result."""
    d = make_scene(tmp_path)
    objs = {f"stock_{g}/stock_{g}_l0_{i}": {"resolved": True, "max_displacement_m": 0.001, "stable": True}
            for g in range(30) for i in range(25)}
    objs["beer"] = {"resolved": True, "max_displacement_m": 0.09, "stable": False}
    hulls = [n for n in objs if n != "beer"]
    cams = [{"eye": [1.0, 2.0, 3.0], "forward": [0.1, 0.2, -0.9], "up": [0, 0, 1], "hfov_deg": 60.0}] * 24

    def run(script, args, timeout_s):
        Path(args[args.index("--report") + 1]).write_text(json.dumps({
            "objects": objs, "unstable": ["beer"], "sunk": [], "no_collider": [], "static": [],
            "sdf_to_hull": {"sdf": 756, "objects": hulls},
            "orbit": {"dir": "/x/check_orbit", "frames": [f"{i:02d}.jpg" for i in range(24)],
                      "azimuth_deg": [15.0 * i for i in range(24)], "cameras": cams}}))
        return subprocess.CompletedProcess(args, 0, "", ""), {}

    monkeypatch.setattr(service, "_run_isaac", run)
    r = service.check_in_isaac(d)
    rep = r["report"]
    assert len(json.dumps(r, ensure_ascii=False)) < 3000
    assert r["verdict"] == "needs_fix"
    assert rep["sdf_to_hull"]["sdf"] == 756 and rep["sdf_to_hull"]["hulled"] == 750
    assert len(rep["sdf_to_hull"]["objects"]) == 13 and rep["sdf_to_hull"]["objects"][0] == "stock_0/* (25)"
    assert rep["orbit"] == {"dir": "/x/check_orbit", "frames": 24}
    assert rep["groups"] == {"beer": {"count": 1, "max_displacement_m": 0.09, "unstable": 1}}
    assert rep["groups_still"] == 30
    full = json.loads((d / "check.json").read_text())
    assert len(full["orbit"]["cameras"]) == 24 and len(full["sdf_to_hull"]["objects"]) == 750


def test_check_result_names_boxes_that_dropped_onto_a_lower_collider(tmp_path, monkeypatch):
    """100 boxes on a rack's lower deck all went 4.1-4.3 cm straight down (the board was found 4 cm too
    high); the summary says so instead of leaving 100 unstable boxes to be fixed."""
    d = make_scene(tmp_path)
    objs = {f"boxes/boxes_r0c0_l0_{i}_0": {"resolved": True, "max_displacement_m": 0.05, "stable": False,
                                           "moved_body": {"path": "x", "from": [i, 0.0, 1.245],
                                                          "to": [i + 0.01 * (i % 3), 0.0, 1.245 - 0.041 - 0.0002 * i]}}
            for i in range(10)}
    objs["chair"] = {"resolved": True, "max_displacement_m": 0.3, "stable": False,
                     "moved_body": {"path": "y", "from": [0, 0, 0.5], "to": [0.3, 0, 0.45]}}
    for i in range(3):  # tipped over: different drops
        objs[f"cans/cans_{i}"] = {"resolved": True, "max_displacement_m": 0.1, "stable": False,
                                  "moved_body": {"path": "z", "from": [0, 0, 1.0], "to": [0, 0, 1.0 - 0.02 * (i + 1)]}}
    unstable = sorted(objs)

    def run(script, args, timeout_s):
        Path(args[args.index("--report") + 1]).write_text(json.dumps({
            "objects": objs, "unstable": unstable, "sunk": [], "no_collider": [], "static": []}))
        return subprocess.CompletedProcess(args, 0, "", ""), {}

    monkeypatch.setattr(service, "_run_isaac", run)
    rep = service.check_in_isaac(d)["report"]
    assert rep["dropped"] == {"boxes": {"count": 10, "m": 0.042}}
    assert "collider" in rep["dropped_hint"]
