"""One headless Isaac Sim kept running for the checks and renders of this server.

AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER=1: the first Isaac Sim call starts scripts/isaac/worker.py (Kit takes most of a draft check's
40 s to start) and later calls hand it their script and arguments through a spool folder in AGENTIC_SIM_WORLD_GEN_CACHE; the
worker runs the same scripts as the one-shot way, in its own app. It stops by itself after
AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER_IDLE seconds without a job (default 600), when this server exits, and is stopped here
after AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER_JOBS jobs (default 40: what it holds on to does not grow without bound) and after
any job that fails, hangs or kills it - `run` then returns None and the caller runs the job the one-shot way, so
a broken worker (a PhysX blow-up leaves the GPU physics of its Kit broken, CUDA error 700) costs time, not the
result. Off by default: one Kit holds a few GB of GPU memory while it waits.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "isaac" / "worker.py"
SCRIPTS = {"check_scene.py", "front_views.py", "render_view.py"}  # worker.py's ALLOWED
START_TIMEOUT_S = 600  # the first start after an install compiles shaders
_lock = threading.Lock()
_worker: dict[str, Any] | None = None
last: dict[str, Any] = {}  # how the last call went: {"mode": "worker" | "one-shot", "why"?, "start_s"?, "seconds"?}


def enabled() -> bool:
    return os.environ.get("AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER", "0") == "1"


def _number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _root() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_CACHE", "~/.cache/agentic-sim-world-gen")).expanduser() / "isaac_worker"


def _start(py: Path) -> dict[str, Any] | None:
    spool = _root() / f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    spool.mkdir(parents=True, exist_ok=True)
    logfile = open(spool / "worker.log", "w")  # noqa: SIM115  (the child's for its whole life)
    t0 = time.monotonic()
    proc = subprocess.Popen(
        [str(py), str(SCRIPT), "--dir", str(spool), "--idle", str(_number("AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER_IDLE", 600)),
         "--parent", str(os.getpid())],
        stdout=logfile, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    w = {"proc": proc, "spool": spool, "log": logfile, "jobs": 0, "py": str(py)}
    while not (spool / "ready").exists():
        if proc.poll() is not None or time.monotonic() - t0 > START_TIMEOUT_S:
            log.warning("Isaac Sim worker did not start (%s); see %s", proc.poll(), spool / "worker.log")
            _stop(w, keep_log=True)
            return None
        time.sleep(0.2)
    w["start_s"] = round(time.monotonic() - t0, 1)
    return w


def _stop(w: dict[str, Any], keep_log: bool = False) -> None:
    proc = w["proc"]
    try:
        (w["spool"] / "stop").write_text("")
        if proc.poll() is None:
            try:
                proc.wait(timeout=0.5 if keep_log else 20)  # let Kit close itself
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
    except OSError:
        pass
    finally:
        w["log"].close()
        if not keep_log:
            shutil.rmtree(w["spool"], ignore_errors=True)


def stop() -> None:
    """Stop the worker, if one runs (also at exit)."""
    global _worker
    with _lock:
        if _worker is not None:
            _stop(_worker)
            _worker = None


atexit.register(stop)


def _fail(why: str) -> None:
    """The worker cannot be trusted any more: stop it, keep its log, let the caller go one-shot."""
    global _worker
    log.warning("Isaac Sim worker: %s; running this one the one-shot way", why)
    if _worker is not None:
        _stop(_worker, keep_log=True)
        last.update(mode="one-shot", why=why, log=str(_worker["spool"] / "worker.log"))
        _worker = None
    else:
        last.update(mode="one-shot", why=why)


def run(py: Path, script: Path, args: list[str], timeout_s: float) -> subprocess.CompletedProcess | None:
    """Run `script` in the worker. None = not done there (off, not a script it runs, or it failed): run it one-shot."""
    global _worker
    last.clear()
    if not enabled() or script.name not in SCRIPTS:
        return None
    with _lock:
        if _worker is not None and (_worker["proc"].poll() is not None or _worker["py"] != str(py)
                                    or not (_worker["spool"] / "ready").exists()):
            _stop(_worker)  # went away while idle, or another Isaac Sim is asked for
            _worker = None
        started = None
        if _worker is None:
            _worker = _start(py)
            if _worker is None:
                last.update(mode="one-shot", why="the worker did not start")
                return None
            started = _worker["start_s"]
        w = _worker
        w["jobs"] += 1
        reply = w["spool"] / f"reply_{w['jobs']}.json"
        job = w["spool"] / f"job_{w['jobs']:06d}.json"
        tmp = w["spool"] / f".job_{w['jobs']}.part"
        tmp.write_text(json.dumps({"script": script.name, "argv": [str(a) for a in args], "reply": str(reply)}))
        os.replace(tmp, job)
        t0 = time.monotonic()
        while not reply.exists():
            if w["proc"].poll() is not None:
                _fail(f"it exited ({w['proc'].returncode}) during {script.name}")
                return None
            if time.monotonic() - t0 > timeout_s:
                _fail(f"{script.name} took more than {timeout_s:.0f} s")
                return None
            time.sleep(0.05)
        try:
            r = json.loads(reply.read_text())
        except ValueError:
            _fail("its reply was not JSON")
            return None
        reply.unlink(missing_ok=True)
        if r.get("returncode") != 0 or r.get("error"):
            _fail(f"{script.name} failed there ({(r.get('error') or '').strip().splitlines()[-1:] or r.get('returncode')})")
            return None
        last.update(mode="worker", seconds=round(time.monotonic() - t0, 1), jobs=w["jobs"],
                    **({"start_s": started} if started is not None else {}))
        if w["jobs"] >= _number("AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER_JOBS", 40):
            _stop(w)
            _worker = None
        return subprocess.CompletedProcess([str(py), str(script), *args], 0, r.get("stdout") or "", "")
