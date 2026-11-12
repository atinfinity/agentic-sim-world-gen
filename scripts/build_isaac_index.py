"""Build the Isaac Sim bundled-asset index (src/agentic_sim_world_gen/data/isaac_<version>.jsonl.gz).

    python scripts/build_isaac_index.py --version 6.1                # list + SimReady Central join
    python scripts/build_isaac_index.py --version 6.1 --bbox-props   # + bbox of Props via usd-core

Steps
1. List the public S3 bucket (Isaac/SimReady, Props, Environments). A "main" asset is a USD file with
   a 256x256 thumbnail next to it (.thumbs/256x256/<file>.png); helper layers (parts, payloads,
   instanceable meshes) have none.
2. SimReady: the same assets are on SimReady Central under the same file name, so bbox, class,
   caption and tags are copied from a name search there. License from the folder's LICENSE.txt.
3. Props (optional --bbox-props): mirror the USD layers (no textures) into a cache and compute the
   bounding box with usd-core. Environments are whole scenes: indexed without bbox.

Output records are AssetRecord dicts (agentic_sim_world_gen.scene.spec.record_to_dict) plus "isaac_path"
(path below the assets root, e.g. /Isaac/Props/...), so a local assets root can be swapped in.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentic_sim_world_gen.assets.record import AssetRecord  # noqa: E402
from agentic_sim_world_gen.scene.spec import record_from_dict, record_to_dict  # noqa: E402

BUCKET = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/"
SECTIONS = ["SimReady", "Props", "Environments"]
SKIP = re.compile(r"/(parts|payloads|Materials|Textures|source_assets|configuration)/|instanceable_meshes", re.I)
ISAAC_LICENSE = "NVIDIA-Isaac-Sim"  # see agentic_sim_world_gen.license.KNOWN


def make_session() -> requests.Session:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    retry = Retry(total=5, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=None)
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
    return s


def list_keys(prefix: str, session: requests.Session) -> list[tuple[str, int]]:
    out, tok = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if tok:
            params["continuation-token"] = tok
        t = session.get(BUCKET, params=params, timeout=120).text
        out += [(k, int(s)) for k, s in re.findall(r"<Key>([^<]*)</Key>.*?<Size>(\d+)</Size>", t, re.S)]
        m = re.search(r"<NextContinuationToken>([^<]*)<", t)
        if not m:
            return out
        tok = m.group(1)


def main_assets(keys: list[tuple[str, int]]) -> list[str]:
    keyset = {k for k, _ in keys}
    mains = []
    for k, _ in keys:
        if not k.endswith((".usd", ".usda", ".usdc")) or "/.thumbs/" in k or SKIP.search(k):
            continue
        d, f = os.path.split(k)
        if f"{d}/.thumbs/256x256/{f}.png" in keyset:
            mains.append(k)
    return mains


def thumb(key: str) -> str:
    d, f = os.path.split(key)
    return f"{BUCKET}{d}/.thumbs/256x256/{f}.png"


def base_record(key: str, version: str) -> AssetRecord:
    rel = key.split(f"Assets/Isaac/{version}", 1)[1]  # /Isaac/...
    parts = rel.strip("/").split("/")
    name = parts[-1]
    # one tag per category folder, as a phrase: "KLT_Bin" -> "klt bin", "PalletJack" -> "pallet jack"
    folders = [re.sub(r"(?<=[a-z])(?=[A-Z])", " ", f).replace("_", " ").strip().lower() for f in parts[2:-1]]
    return AssetRecord(
        id=f"isaac:{rel}",
        source=f"isaac-{version}",
        uri=BUCKET + key,
        name=name,
        bbox_min=(0.0, 0.0, 0.0),
        bbox_max=(0.0, 0.0, 0.0),
        caption=None,
        tags=list(dict.fromkeys([parts[1].lower(), *folders])),
        thumbnail_url=thumb(key),
        raw={"isaac_path": rel, "category": "/".join(parts[1:-1])},
    )


def join_simready(rec: AssetRecord, central, session: requests.Session) -> AssetRecord:
    stem = rec.name.rsplit(".", 1)[0]
    try:
        hits = central.search(stem, limit=5)
    except requests.RequestException:
        hits = []
    hit = next((h for h in hits if h.name.rsplit(".", 1)[0] == stem), None)
    if hit:
        rec.bbox_min, rec.bbox_max = hit.bbox_min, hit.bbox_max
        rec.up_axis, rec.meters_per_unit = hit.up_axis, hit.meters_per_unit
        rec.semantic_class, rec.qcode, rec.caption = hit.semantic_class, hit.qcode, hit.caption
        rec.tags = list(dict.fromkeys([*hit.tags, *rec.tags]))
        rec.mass_kg = hit.mass_kg
        rec.raw["joined"] = hit.uri
    lic_url = rec.uri.rsplit("/", 1)[0] + "/LICENSE.txt"
    try:
        head = session.get(lic_url, timeout=60).text[:200]
    except requests.RequestException:
        head = ""
    rec.license = "CC-BY-4.0" if "Attribution 4.0" in head else (ISAAC_LICENSE if not head else head.split("\n")[0])
    return rec


def props_bbox(recs: list[AssetRecord], keys: list[tuple[str, int]], cache: Path, max_mb: float) -> None:
    from pxr import Usd, UsdGeom

    session = make_session()
    layers = [(k, s) for k, s in keys if k.endswith((".usd", ".usda", ".usdc")) and "/.thumbs/" not in k]

    def fetch(k_s):
        k, s = k_s
        p = cache / k
        if s > max_mb * 1e6 or (p.exists() and p.stat().st_size == s):
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = session.get(BUCKET + k, timeout=600)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  skip {k}: {e}", flush=True)
            return
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(r.content)
        tmp.replace(p)

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(fetch, layers))
    for r in recs:
        local = cache / r.uri.removeprefix(BUCKET)
        if not local.exists():
            continue
        stage = Usd.Stage.Open(str(local))
        if not stage:
            continue
        cache_ = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
        root = stage.GetDefaultPrim() or stage.GetPseudoRoot()
        rng = cache_.ComputeWorldBound(root).ComputeAlignedRange()
        if rng.IsEmpty():
            continue
        r.bbox_min, r.bbox_max = tuple(rng.GetMin()), tuple(rng.GetMax())
        r.up_axis = UsdGeom.GetStageUpAxis(stage)
        r.meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="6.1")
    ap.add_argument("--out", default=None)
    ap.add_argument("--bbox-props", action="store_true", help="download Props USD layers and compute bbox")
    ap.add_argument("--cache", default=str(Path.home() / ".cache" / "agentic-sim-world-gen" / "isaac"))
    ap.add_argument("--max-layer-mb", type=float, default=100)
    a = ap.parse_args()

    from agentic_sim_world_gen.assets.providers.simready_central import SimReadyCentralProvider

    session = make_session()
    central = SimReadyCentralProvider(session=make_session())
    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    out = Path(a.out or ROOT / "src" / "agentic_sim_world_gen" / "data" / f"isaac_{a.version}.jsonl.gz")
    records: list[AssetRecord] = []
    for sec in SECTIONS:
        keys = list_keys(f"Assets/Isaac/{a.version}/Isaac/{sec}/", session)
        recs = [base_record(k, a.version) for k in main_assets(keys)]
        print(f"{sec}: {len(keys)} keys, {len(recs)} main assets", flush=True)
        if sec == "SimReady":
            # checkpoint: the join takes ~10 min, keep it across runs
            ck = cache / f"simready_join_{a.version}.json"
            done = json.loads(ck.read_text()) if ck.exists() else {}
            todo = [r for r in recs if r.id not in done]
            with ThreadPoolExecutor(8) as ex:
                for r in ex.map(lambda r: join_simready(r, central, session), todo):
                    d = record_to_dict(r)
                    d["raw"] = r.raw
                    done[r.id] = d
            ck.write_text(json.dumps(done))
            recs = []
            for k in main_assets(keys):
                d = dict(done[base_record(k, a.version).id])
                raw = d.pop("raw")
                rec = record_from_dict(d)
                rec.raw = raw
                recs.append(rec)
            print(f"  joined with SimReady Central: {sum('joined' in r.raw for r in recs)}", flush=True)
        else:
            for r in recs:
                r.license = ISAAC_LICENSE
            if sec == "Props" and a.bbox_props:
                props_bbox(recs, keys, cache, a.max_layer_mb)
                print(f"  bbox computed: {sum(r.bbox_max != (0.0, 0.0, 0.0) for r in recs)}", flush=True)
        records += recs

    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        for r in records:
            d = record_to_dict(r)
            d["isaac_path"] = r.raw["isaac_path"]
            d["category"] = r.raw["category"]
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} records -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
