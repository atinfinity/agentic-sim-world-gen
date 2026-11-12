"""Build the offline index of CC0 PBR materials (ambientCG, Poly Haven) for agentic_sim_world_gen.pbr.

Only the public catalog APIs are read (metadata, no textures):
  https://ambientcg.com/api/v2/full_json?type=Material   (paged, 250 per request)
  https://api.polyhaven.com/assets?t=textures

Writes src/agentic_sim_world_gen/data/pbr_cc0.jsonl.gz (one record per line, see agentic_sim_world_gen.pbr). Run again to pick up new
materials:  uv run python scripts/build_pbr_index.py [--out PATH]
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentic_sim_world_gen import USER_AGENT, pbr  # noqa: E402

UA = {"User-Agent": f"{USER_AGENT} build_pbr_index.py"}
ACG = "https://ambientcg.com/api/v2/full_json"
PH = "https://api.polyhaven.com/assets?t=textures"


def ambientcg() -> list[dict]:
    out, offset = [], 0
    while True:
        r = requests.get(ACG, headers=UA, timeout=60, params={
            "type": "Material", "limit": 250, "offset": offset, "sort": "Alphabet",
            "include": "downloadData,tagData,displayData,dimensionsData,imageData"})
        r.raise_for_status()
        found = r.json().get("foundAssets") or []
        out += found
        if len(found) < 250:
            return out
        offset += 250


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=pbr.DATA / pbr.INDEX_NAME)
    a = ap.parse_args()
    recs = [x for x in (pbr.from_ambientcg(raw) for raw in ambientcg()) if x]
    ph = requests.get(PH, headers=UA, timeout=60)
    ph.raise_for_status()
    recs += [x for x in (pbr.from_polyhaven(k, v) for k, v in sorted(ph.json().items())) if x]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(a.out, "wt", encoding="utf-8") as f:
        for x in recs:
            f.write(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n")
    n = {lib: sum(x["library"] == lib for x in recs) for lib in ("ambientCG", "PolyHaven")}
    print(f"{len(recs)} materials {n} -> {a.out} ({a.out.stat().st_size // 1024} kB)")


if __name__ == "__main__":
    main()
