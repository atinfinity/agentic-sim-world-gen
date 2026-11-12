"""F = NVIDIA's public USD Search (Isaac Sim 6.2 content) with the quickstart.sh hybrid settings (f_probe.json)."""
import json, sys
from pathlib import Path

import requests

E = sys.argv[1]
URL = "https://search.simready.omniverse.nvidia.com/search_hybrid"
base = json.loads((Path(__file__).parent / "f_probe.json").read_text())
qs = json.load(open(f"{E}/queries.json"))
out = {}
for q in qs:
    for lang, text in (("F", q["q"]), ("F_ja", q["ja"])):
        b = json.loads(json.dumps(base)); b["hybrid_text_query"] = text; b["vector_queries"][0]["query"] = text
        b["limit"] = 5; b["return_vision_generated_metadata"] = True
        hits = requests.post(URL, json=b, timeout=60).json().get("hits", [])
        rows = []
        for h in hits:
            s = h["source"]; cap = ""
            for k in ("plugin_rendering_to_vision_metadata_metadata_vlm_generated", "plugin_thumbnail_to_vision_metadata_metadata_vlm_generated"):
                for m in s.get(k) or []:
                    if m.get("name") == "caption" and not cap: cap = (m.get("value_text") or [""])[0]
            url = s.get("base_key", ""); head, _, fn = url.rpartition("/")
            rows.append({"name": s["name"], "url": url, "thumb": f"{head}/.thumbs/256x256/{fn}.png", "caption": cap})
        out.setdefault(lang, {})[q["id"]] = rows
    print(q["id"], [r["name"] for r in out["F"][q["id"]]], file=sys.stderr)
json.dump(out, open(f"{E}/runs_f.json", "w"), indent=1)
