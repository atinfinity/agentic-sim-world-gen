"""Run the fixed requests end to end with `claude -p` and record what each cost and made.

    python scripts/e2e_eval/run.py ~/agentic-sim-world-gen-e2e/<name> [--cases nl_study,nl_office] [--repeat 2] [--model sonnet]
    python scripts/e2e_eval/report.py ~/agentic-sim-world-gen-e2e/<name> [--before ~/agentic-sim-world-gen-e2e/<older>]

Every case gets its own workspace (`agentic-sim-world-gen workspace`, outside the repository) under <run>/<case>__<n>/ws, its own
preferences file (AGENTIC_SIM_WORLD_GEN_PREFERENCES, so the user's remembered tastes neither steer nor learn from a trial) and one
Claude Code session: the first turn with --session-id, the next ones with --resume. The stream of each turn stays in
turn<k>.jsonl; one line per case goes to <run>/results.jsonl (metrics.py says what the numbers are).
A case that is already in results.jsonl is skipped: stop and start again as you like.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import metrics  # noqa: E402

TOOLS = ["mcp__agentic-sim-world-gen__*", "Read", "Skill", "Glob", "Grep"]
JUDGE = ("あなたは採点者です。{png} は Isaac Sim で作ったワールドの画像です。Read で見て、次の依頼にどれだけ合っているかを "
         "0〜5 の整数で採点してください（5＝依頼どおり、3＝部屋の種類と主な物は合う、0＝別物）。依頼：「{request}」\n"
         "答えは JSON 1 行だけ：{{\"score\": <0-5>, \"reason\": \"<日本語で 1 文>\"}}")


def git_state() -> dict[str, Any]:
    def run(*a: str) -> str:
        return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True).stdout.strip()

    return {"commit": run("rev-parse", "--short", "HEAD"), "branch": run("branch", "--show-current"),
            "dirty": bool(run("status", "--porcelain"))}


def claude(cwd: Path, prompt: str, session: str, first: bool, a: argparse.Namespace, out: Path) -> None:
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--allowedTools", *TOOLS,
           *(["--session-id", session] if first else ["--resume", session]),
           *(["--model", a.model] if a.model else []),
           *(["--max-budget-usd", str(a.max_budget)] if a.max_budget else [])]
    with out.open("w", encoding="utf-8") as f, (out.with_suffix(".err")).open("w", encoding="utf-8") as err:
        try:
            subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=f, stderr=err, timeout=a.timeout)
        except subprocess.TimeoutExpired:
            err.write(f"\ntimed out after {a.timeout} s\n")


def latest_scene(scenes: Path) -> Path | None:
    found = sorted(scenes.glob("*/scene.resolved.json"), key=lambda f: f.stat().st_mtime)
    return found[-1].parent if found else None


def judge(scene: Path, request: str, a: argparse.Namespace) -> dict[str, Any]:
    """Optional: a 0-5 score of check.png against the request by a separate `claude -p` (not blind to nothing but
    the picture and the request)."""
    png = scene / "check.png"
    if not png.exists():
        return {}
    r = subprocess.run(["claude", "-p", JUDGE.format(png=png, request=request), "--allowedTools", "Read",
                        *(["--model", a.judge_model] if a.judge_model else [])],
                       cwd=scene, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
    try:
        got = json.loads(r.stdout[r.stdout.index("{"):r.stdout.rindex("}") + 1])
        return {"judge": int(got["score"]), "judge_reason": str(got.get("reason", ""))[:200]}
    except (ValueError, KeyError, TypeError):
        return {"judge_error": r.stdout.strip()[:200]}


def run_case(case: dict[str, Any], n: int, run_dir: Path, a: argparse.Namespace) -> dict[str, Any]:
    from agentic_sim_world_gen import service, workspace

    d = run_dir / f"{case['id']}__{n}"
    if d.exists():
        shutil.rmtree(d)
    ws = d / "ws"
    env = {"AGENTIC_SIM_WORLD_GEN_PREFERENCES": str(d / "preferences.json"), "AGENTIC_SIM_WORLD_GEN_BROWSER": "true",
           **dict(kv.split("=", 1) for kv in a.env)}
    workspace.make(ws, env=env, allow_tools=True)
    inputs = ws / "inputs"
    for src in case.get("inputs") or []:
        inputs.mkdir(exist_ok=True)
        shutil.copy(REPO / src, inputs / Path(src).name)
    session = str(uuid.uuid4())
    started = time.time()
    for k, prompt in enumerate(case["turns"]):
        claude(ws, prompt.replace("{inputs}", str(inputs)), session, k == 0, a, d / f"turn{k + 1}.jsonl")
    row: dict[str, Any] = {"case": case["id"], "kind": case.get("kind"), "repeat": n, "session": session,
                           "wall_seconds": round(time.time() - started, 1), "model": a.model,
                           **metrics.stream_metrics([metrics.read_events(d / f"turn{k + 1}.jsonl")
                                                     for k in range(len(case["turns"]))], case.get("lang"))}
    scene = latest_scene(ws / "scenes")
    if scene is None:
        row["scene"] = None
    else:
        row["scene"] = str(scene)
        try:
            row.update(metrics.scene_metrics(service.status(scene)))
        except Exception as e:  # a broken scene is a result too
            row["scene_error"] = f"{type(e).__name__}: {e}"
        if a.judge:
            row.update(judge(scene, " ".join(case["turns"]), a))
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_dir", nargs="?", help="where this run goes (outside the repository), e.g. ~/agentic-sim-world-gen-e2e/2026-11-10")
    ap.add_argument("--cases", help="comma-separated ids of cases.json (default: all)")
    ap.add_argument("--file", default=str(HERE / "cases.json"), help="another list of cases")
    ap.add_argument("--repeat", type=int, default=1, help="run each case this many times (the spread)")
    ap.add_argument("--model", help="model for the sessions (default: Claude Code's)")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds per turn")
    ap.add_argument("--max-budget", type=float, default=15.0, help="USD per turn (claude --max-budget-usd), 0 for none")
    ap.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="more variables for the MCP server")
    ap.add_argument("--judge", action="store_true", help="score check.png against the request with another claude -p")
    ap.add_argument("--judge-model", help="model of the judge")
    ap.add_argument("--list", action="store_true", help="print the cases and stop")
    a = ap.parse_args(argv)
    cases = json.loads(Path(a.file).read_text(encoding="utf-8"))
    if a.cases:
        want = a.cases.split(",")
        if missing := [w for w in want if w not in {c["id"] for c in cases}]:
            ap.error(f"no such case: {', '.join(missing)}")
        cases = [c for c in cases if c["id"] in want]
    if a.list:
        for c in cases:
            print(f"{c['id']:20} {c.get('kind', ''):24} {len(c['turns'])} turn(s)")
        return 0
    if not a.run_dir:
        ap.error("run_dir is required")
    if not shutil.which("claude"):
        print("claude (Claude Code) is not on PATH", file=sys.stderr)
        return 1
    run_dir = Path(a.run_dir).expanduser().absolute()
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), **git_state(), "model": a.model, "repeat": a.repeat,
            "claude": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(),
            "host": os.uname().nodename, "isaac": bool(os.environ.get("ISAACSIM_PYTHON") or os.environ.get("ISAACSIM_PATH"))}
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    results = run_dir / "results.jsonl"
    done = set()
    if results.exists():
        done = {(r["case"], r["repeat"]) for r in map(json.loads, results.read_text().splitlines()) if r}
    for case in cases:
        for n in range(1, a.repeat + 1):
            if (case["id"], n) in done:
                continue
            print(f"{case['id']} #{n} ...", flush=True)
            row = run_case(case, n, run_dir, a)
            with results.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"  {row['turns']} turns, {row['tool_calls']} tools, ${row['cost_usd']:.2f}, {row['seconds']:.0f} s, "
                  f"check {row.get('check')}, issues {row.get('issues')}" + (f", FAILED {row['failed']}" if row.get("failed") else ""),
                  flush=True)
    print(f"python {Path(__file__).with_name('report.py')} {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
