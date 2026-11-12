"""Per-asset cards: what an asset really is and how it behaves, looked at once instead of in every scene.

A card is one line of src/agentic_sim_world_gen/data/asset_cards.jsonl.gz (AGENTIC_SIM_WORLD_GEN_ASSET_CARDS: another file, `0`: none),
made by scripts/build_asset_cards.py from two kinds of look:

  look (pictures, by a VLM or a person)   kind, also, front_deg, colors, materials, features, mount
  drop (Isaac Sim, the asset alone)        physics: {stable, moved_m, sink_m, bodies, colliders}

    {"key": "wooden_pallet_a01/sm_wooden_pallet_a01_01", "kind": "pallet", "also": ["skid"], "front_deg": 0,
     "colors": ["brown"], "materials": ["wood"], "features": {"legs": false},
     "physics": {"stable": true, "moved_m": 0.002, "sink_m": 0.0, "bodies": 1}, "by": {"look": "...", "drop": "..."}}

The key is the asset's folder and file name (no extension, lower case), so it is the same for the public URL, a
local ISAAC_ASSETS_ROOT and a pinned SimReady Central version. What uses a card:
  rerank.score          kind / also: an asset whose card names another thing is another kind (weak), one whose
                        card names the thing asked for counts as that class; colors / materials count as its words
  frame.asset_front_deg front_deg, after the record's own and KNOWN_FRONTS
  service candidates    physics that failed alone (unstable, sunk, no collider) -> match.card, behind the others
The drop puts every asset on a floor. For a thing that hangs (`mount`: "ceiling" or "wall", from the look: a
curtain on a ceiling rail falls over on a floor) moving or sinking there says nothing and is not an issue.
Only what a card says is used: a card without `kind` changes no ranking, one without `physics` no order.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

DATA = Path(__file__).resolve().parents[1] / "data"
FILE = "asset_cards.jsonl.gz"
SINK_M = 0.01  # check_core's limit for "sunk"
MOVE_M = 0.05  # the settle's default move_tol
MOUNTS = ("floor", "on", "wall", "ceiling")  # where the thing lives; "on" = on a table or shelf
HUNG = ("wall", "ceiling")


def path() -> Path | None:
    v = os.environ.get("AGENTIC_SIM_WORLD_GEN_ASSET_CARDS")
    if v == "0":
        return None
    return Path(v).expanduser() if v else DATA / FILE


def key(uri: str) -> str:
    """Folder and file name of a URI or path, without the query and the extension, lower case."""
    p = PurePosixPath(uri.split("?", 1)[0].replace("\\", "/"))
    stem = re.sub(r"\.usd[acz]?$", "", p.name, flags=re.I)
    return f"{p.parent.name}/{stem}".lower()


def read(file: Path) -> dict[str, dict[str, Any]]:
    opener = gzip.open if file.suffix == ".gz" else open
    out = {}
    with opener(file, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                out[d["key"]] = d
    return out


@lru_cache(maxsize=4)
def _load(file: str, mtime: float) -> dict[str, dict[str, Any]]:
    return read(Path(file))


def all_cards() -> dict[str, dict[str, Any]]:
    p = path()
    if p is None or not p.exists():
        return {}
    return _load(str(p), p.stat().st_mtime)


def of(rec) -> dict[str, Any] | None:
    """The card of an AssetRecord (or of a candidate dict), if one was made."""
    uri = rec.get("uri") if isinstance(rec, dict) else rec.uri
    cards = all_cards()
    return cards.get(key(uri)) if cards and uri else None


def physics_issue(card: dict[str, Any] | None) -> str | None:
    """What went wrong when the asset was dropped alone on a floor, in a few words; None = nothing, or not tried."""
    ph = (card or {}).get("physics")
    if not ph:
        return None
    if ph.get("exploded"):
        return "blows up alone"
    if ph.get("resolved") is False:
        return "does not load"
    if ph.get("colliders") == 0:
        return "no collider"
    if card.get("mount") in HUNG:  # it was dropped on a floor, where it never stands
        return None
    if (ph.get("sink_m") or 0) > SINK_M:
        return f"sinks {ph['sink_m'] * 100:.0f} cm"
    if ph.get("stable") is False:
        return f"moves {ph['moved_m']:.2f} m alone" if ph.get("moved_m") is not None else "unstable alone"
    return None


def brief(card: dict[str, Any] | None) -> dict[str, Any]:
    """What a candidate carries of its card: {kind?, issue?} (the rest is used where it applies)."""
    if not card:
        return {}
    out = {k: card[k] for k in ("kind",) if card.get(k)}
    if issue := physics_issue(card):
        out["issue"] = issue
    return out
