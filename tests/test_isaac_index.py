"""Isaac bundled-asset index provider and provider merging (no network)."""

import gzip
import json

from agentic_sim_world_gen.assets.providers.base import size_filter_for
from agentic_sim_world_gen.assets.providers.composite import CompositeProvider
from agentic_sim_world_gen.assets.providers.isaac_index import IsaacIndexProvider
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.license import normalize

S3 = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.1"


def row(path, cls, size, tags=(), lic="CC-BY-4.0"):
    w, d, h = size
    name = path.rsplit("/", 1)[1]
    return {"id": f"isaac:{path}", "source": "isaac-6.1", "uri": S3 + path, "name": name,
            "bbox_min": [-w / 2, -d / 2, 0], "bbox_max": [w / 2, d / 2, h], "up_axis": "Z", "meters_per_unit": 1.0,
            "semantic_class": cls, "tags": list(tags), "license": lic, "isaac_path": path, "category": "x"}


def make_index(tmp_path):
    p = tmp_path / "idx.jsonl.gz"
    rows = [
        row("/Isaac/SimReady/Industrial/Warehouse/Pallets/sm_pallet_a01_01.usd", "pallet", (1.2, 1.0, 0.15), ["pallet"]),
        row("/Isaac/Props/KLT_Bin/small_KLT.usd", None, (0.6, 0.4, 0.3), ["props", "klt", "bin"], "NVIDIA-Isaac-Sim"),
        row("/Isaac/Environments/Simple_Warehouse/warehouse.usd", None, (0, 0, 0), ["environments", "warehouse"]),
    ]
    with gzip.open(p, "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return p


def test_search_and_size(tmp_path):
    prov = IsaacIndexProvider(make_index(tmp_path), assets_root="")
    assert [r.name for r in prov.search("wooden pallet")] == ["sm_pallet_a01_01.usd"]
    assert [r.name for r in prov.search("bin")] == ["small_KLT.usd"]
    assert [r.name for r in prov.search("warehouse")] == ["warehouse.usd"]  # no size filter: environments ok
    assert prov.search("warehouse", size=size_filter_for(None, (5, 5, 5))) == []  # unknown size never fits
    assert prov.search("pallet", size=size_filter_for(None, (1.0, 1.0, 1.0))) == []  # 1.2 m too long


def test_local_assets_root(tmp_path):
    prov = IsaacIndexProvider(make_index(tmp_path), assets_root="/data/isaac_assets/6.1/")
    assert prov.search("pallet")[0].uri == "/data/isaac_assets/6.1/Isaac/SimReady/Industrial/Warehouse/Pallets/sm_pallet_a01_01.usd"


def test_composite_dedupes_and_survives_failure(tmp_path):
    isaac = IsaacIndexProvider(make_index(tmp_path), assets_root="")
    hf = AssetRecord(id="hf", source="simready-central", uri="https://huggingface.co/x/sm_pallet_a01_01.usd",
                     name="sm_pallet_a01_01.usd", bbox_min=(0, 0, 0), bbox_max=(1.2, 1.0, 0.15))

    class Central:
        name = "simready-central"

        def search(self, q, limit=10, size=None):
            return [hf]

    class Broken:
        name = "broken"

        def search(self, q, limit=10, size=None):
            raise RuntimeError("down")

    c = CompositeProvider([Central(), isaac])
    assert [r.source for r in c.search("pallet")] == ["simready-central"]  # same file kept once
    c = CompositeProvider([Broken(), isaac])
    assert [r.name for r in c.search("pallet")] == ["sm_pallet_a01_01.usd"] and c.errors


def test_isaac_license_name():
    assert normalize("NVIDIA Isaac Sim assets (Isaac Sim license)") == "NVIDIA-Isaac-Sim"
