"""The Isaac Sim worker's service side, with a stand-in for scripts/isaac/worker.py (no Isaac Sim here)."""

import sys
import textwrap
from pathlib import Path

import pytest

from agentic_sim_world_gen import isaac_worker

FAKE = textwrap.dedent('''
    import argparse, json, os, sys, time
    from pathlib import Path
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir"); ap.add_argument("--idle"); ap.add_argument("--parent")
    o = ap.parse_args()
    spool = Path(o.dir)
    (spool / "ready").write_text(str(os.getpid()))
    n = 0
    while not (spool / "stop").exists():
        for f in sorted(spool.glob("job_*.json")):
            job = json.loads(f.read_text()); f.unlink(); n += 1
            argv = job["argv"]
            if "die" in argv:
                sys.exit(3)
            if "hang" in argv:
                time.sleep(60)
            r = {"returncode": 1 if "fail" in argv else 0, "stdout": f"{job['script']} {' '.join(argv)} pid={os.getpid()}"}
            if "raise" in argv:
                r = {"returncode": 1, "stdout": "", "error": "Traceback\\nRuntimeError: CUDA error 700"}
            Path(job["reply"] + ".part").write_text(json.dumps(r)); os.replace(job["reply"] + ".part", job["reply"])
        time.sleep(0.01)
''')


@pytest.fixture
def worker(tmp_path, monkeypatch):
    fake = tmp_path / "fake_worker.py"
    fake.write_text(FAKE)
    monkeypatch.setattr(isaac_worker, "SCRIPT", fake)
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER", "1")
    yield tmp_path
    isaac_worker.stop()


PY = Path(sys.executable)
CHECK = Path("scripts/isaac/check_scene.py")


def test_off_by_default_and_only_known_scripts(worker, monkeypatch):
    assert isaac_worker.run(PY, Path("scripts/isaac/nav_map.py"), [], 5) is None  # not one the worker runs
    assert isaac_worker._worker is None
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER", "0")
    assert isaac_worker.run(PY, CHECK, ["a.usda"], 5) is None
    assert isaac_worker._worker is None


def test_jobs_share_one_process(worker):
    a = isaac_worker.run(PY, CHECK, ["a.usda", "--draft"], 10)
    assert a.returncode == 0 and a.stdout.startswith("check_scene.py a.usda --draft")
    assert isaac_worker.last["mode"] == "worker" and "start_s" in isaac_worker.last
    b = isaac_worker.run(PY, Path("x/render_view.py"), ["b.usda"], 10)
    assert b.stdout.split("pid=")[1] == a.stdout.split("pid=")[1]  # the same process
    assert "start_s" not in isaac_worker.last and isaac_worker.last["jobs"] == 2
    spool = isaac_worker._worker["spool"]
    isaac_worker.stop()
    assert not spool.exists() and isaac_worker._worker is None


@pytest.mark.parametrize("what", ["fail", "raise", "die", "hang"])
def test_a_bad_job_stops_the_worker_and_falls_back(worker, what):
    assert isaac_worker.run(PY, CHECK, ["a.usda"], 10) is not None
    w = isaac_worker._worker
    assert isaac_worker.run(PY, CHECK, [what], 1 if what == "hang" else 10) is None  # the caller goes one-shot
    assert isaac_worker.last["mode"] == "one-shot" and isaac_worker.last["why"]
    assert isaac_worker._worker is None and w["proc"].poll() is not None
    assert Path(isaac_worker.last["log"]).exists()  # kept for looking at
    again = isaac_worker.run(PY, CHECK, ["a.usda"], 10)  # the next call starts a fresh one
    assert again is not None and isaac_worker._worker["proc"].pid != w["proc"].pid


def test_recycled_after_n_jobs(worker, monkeypatch):
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER_JOBS", "2")
    pids = [isaac_worker.run(PY, CHECK, ["a.usda"], 10).stdout.split("pid=")[1] for _ in range(3)]
    assert pids[0] == pids[1] != pids[2]


def test_run_isaac_uses_it(worker, monkeypatch):
    from agentic_sim_world_gen import service

    monkeypatch.setattr(service, "isaac_python", lambda: PY)
    proc, err = service._run_isaac(CHECK.resolve(), ["a.usda"], 10)
    assert err == {} and proc.returncode == 0 and "check_scene.py a.usda" in proc.stdout
    assert service._isaac_mode()["isaac"]["mode"] == "worker"
