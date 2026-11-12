"""The table of an end-to-end run, and what changed since an earlier one.

    python scripts/e2e_eval/report.py <run_dir> [--before <older run_dir>] [--tools]

Writes <run_dir>/report.md and prints it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics  # noqa: E402


def load(run_dir: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    d = Path(run_dir).expanduser()
    rows = [json.loads(line) for line in (d / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    try:
        meta = json.loads((d / "run.json").read_text())
    except (OSError, ValueError):
        meta = {}
    return meta, rows


def _head(name: str, meta: dict[str, Any]) -> str:
    dirty = "（未コミットの変更あり）" if meta.get("dirty") else ""
    return (f"{name}：commit `{meta.get('commit', '?')}`{dirty}、{meta.get('started', '?')}、"
            f"model {meta.get('model') or '既定'}、{meta.get('claude', '')}、Isaac Sim {'あり' if meta.get('isaac') else 'なし'}")


def tools_table(rows: list[dict[str, Any]], top: int = 12) -> str:
    """Which tools the calls went to, per case (mean of the repeats)."""
    lines = ["| 指示 | 多い順 |", "|---|---|"]
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(r["case"], []).append(r)
    for case, rs in groups.items():
        total: dict[str, float] = {}
        for r in rs:
            for k, v in (r.get("tools") or {}).items():
                total[k] = total.get(k, 0) + v / len(rs)
        best = sorted(total.items(), key=lambda kv: -kv[1])[:top]
        lines.append(f"| {case} | " + "、".join(f"{k} {v:g}" for k, v in best) + " |")
    return "\n".join(lines)


def context_rows(run_dir: str | Path) -> list[tuple[str, dict[str, Any]]]:
    """The context breakdown of every case of a run, read from its turn<k>.jsonl logs."""
    out = []
    for d in sorted(p for p in Path(run_dir).expanduser().iterdir() if p.is_dir() and "__" in p.name):
        logs = sorted(d.glob("turn*.jsonl"), key=lambda p: int("".join(ch for ch in p.stem if ch.isdigit()) or 0))
        if logs:
            out.append((d.name.replace("__", " #"), metrics.context_breakdown(metrics.read_events(p) for p in logs)))
    return out


def report(run_dir: str | Path, before: str | Path | None = None, tools: bool = False, context: bool = False) -> str:
    meta, rows = load(run_dir)
    parts = ["# 通しのベンチマーク", "", _head("今回", meta)]
    old_rows = None
    if before:
        old_meta, old_rows = load(before)
        parts += [_head("前回", old_meta), "", "かっこの中は前回からの変化（↓ 減った、↑ 増えた）。"]
    parts += ["", metrics.table(rows, old_rows)]
    bad = [r for r in rows if r.get("failed") or r.get("scene") is None]
    if bad:
        parts += ["", "## 最後まで行かなかった物", ""]
        parts += [f"- {r['case']} #{r['repeat']}：{'; '.join(r.get('failed') or ['シーンが無い'])}" for r in bad]
    off = [r for r in rows if r.get("notes_off_language")]
    if off:
        parts += ["", "## 利用者の言語でない途中報告", ""]
        for r in off:
            parts.append(f"- {r['case']} #{r['repeat']}：{r['notes_off_language']} 件"
                         f"（うち圧縮の後 {r.get('notes_off_language_after_compaction', 0)}）"
                         f"、例「{(r.get('off_language_examples') or [''])[0][:80]}」")
    if tools:
        parts += ["", "## ツール別の呼び出し", "", tools_table(rows)]
    if context:
        parts += ["", "## 会話に入った文字の内訳", "",
                  "ログにある文字数（ツールの結果・読んだファイル・エージェントの文と引数）。システムプロンプトと"
                  "ツール定義はログに無いので、表の「最大 context」の最初の要求の分に含まれる。", "",
                  metrics.breakdown_table(context_rows(run_dir))]
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_dir")
    ap.add_argument("--before", help="an earlier run to compare with")
    ap.add_argument("--tools", action="store_true", help="add the per-tool call counts")
    ap.add_argument("--context", action="store_true", help="add what filled the conversation, by source")
    a = ap.parse_args(argv)
    text = report(a.run_dir, a.before, a.tools, a.context)
    (Path(a.run_dir).expanduser() / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
