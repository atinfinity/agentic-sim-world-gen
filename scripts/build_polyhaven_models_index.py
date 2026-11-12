"""Build the offline index of Poly Haven's CC0 3D models for agentic_sim_world_gen.polyhaven_models.

Only the public catalog API is read (metadata, no model files):
  https://api.polyhaven.com/assets?t=models

Writes src/agentic_sim_world_gen/data/polyhaven_models.jsonl.gz (one record per line, see agentic_sim_world_gen.polyhaven_models).
Run again to pick up new models:  uv run python scripts/build_polyhaven_models_index.py [--out PATH]
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentic_sim_world_gen import USER_AGENT, polyhaven_models as ph  # noqa: E402

UA = {"User-Agent": f"{USER_AGENT} build_polyhaven_models_index.py"}
API = "https://api.polyhaven.com/assets?t=models"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ph.DATA / ph.INDEX_NAME)
    ap.add_argument("--from-json", type=Path, help="a saved copy of the assets API response instead of fetching it")
    a = ap.parse_args()
    if a.from_json:
        raw = json.loads(a.from_json.read_text(encoding="utf-8"))
    else:
        r = requests.get(API, headers=UA, timeout=60)
        r.raise_for_status()
        raw = r.json()
    recs = [x for x in (ph.from_api(k, v) for k, v in sorted(raw.items())) if x]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(a.out, "wt", encoding="utf-8") as f:
        for x in recs:
            f.write(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"{len(recs)} of {len(raw)} models -> {a.out} ({a.out.stat().st_size // 1024} kB)")


if __name__ == "__main__":
    main()
