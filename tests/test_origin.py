"""Where each asset came from and which version (agentic_sim_world_gen.origin)."""

import json
import subprocess

import pytest

from agentic_sim_world_gen import origin as og

HF = ("https://huggingface.co/datasets/nvidia/simready-assets/resolve/653d91e182011f357cf1644013cc94ddf194d349/"
      "cabinet_file_a02/simready_usd/sm_cabinet_file_a02_01.usd")


def git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "example-assets"
    a = r / "assets" / "beverage" / "cola" / "simready_usd"
    a.mkdir(parents=True)
    (a / "cola.usda").write_text("#usda 1.0\n")
    (a / "asset.json").write_text(json.dumps({"usd": "cola.usda", "version": "1.3.0"}))
    other = r / "assets" / "other"
    other.mkdir()
    (other / "x.usda").write_text("#usda 1.0\n")
    git(r, "init", "-q")
    git(r, "remote", "add", "origin", "git@github.com:someone/example-assets.git")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "first")
    return r, a / "cola.usda"


def head(r):
    return subprocess.run(["git", "-C", str(r), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def test_local_git_asset(repo):
    r, f = repo
    first = head(r)
    o = og.origin(str(f))
    assert o["from"] == "github.com/someone/example-assets" and o["asset_version"] == "1.3.0"
    assert o["version"] == first and o["pinned"] and not o["dirty"] and len(o["sha256"]) == 64
    assert o["url"] == f"https://github.com/someone/example-assets/blob/{first}/assets/beverage/cola/simready_usd/cola.usda"
    assert og.label(o) == f"someone/example-assets v1.3.0 @ {first[:7]}"
    # another asset changes: this asset keeps the commit that last changed its own folder
    (r / "assets" / "other" / "x.usda").write_text("#usda 1.0\n# v2\n")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "other")
    assert head(r) != first and og.origin(str(f))["version"] == first


def test_local_uncommitted_changes(repo):
    r, f = repo
    f.write_text("#usda 1.0\n# edited\n")
    o = og.origin(f.as_uri())
    assert o["dirty"] and not o["pinned"] and "uncommitted" in o["note"]


def test_local_outside_git(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Sdf

    f = tmp_path / "lamp.usda"
    layer = Sdf.Layer.CreateNew(str(f))
    prim = Sdf.PrimSpec(layer, "lamp", Sdf.SpecifierDef)
    prim.assetInfo = {"version": "2.0.1"}
    layer.defaultPrim = "lamp"
    layer.Save()
    o = og.origin(str(f))
    assert o["asset_version"] == "2.0.1" and o["version"].startswith("sha256:") and not o["pinned"]


def test_remote_sources():
    o = og.origin(HF, "simready-central")
    assert o["from"] == "huggingface.co/datasets/nvidia/simready-assets" and o["pinned"]
    assert og.label(o) == "nvidia/simready-assets @ 653d91e" and o["path"].startswith("cabinet_file_a02/")
    assert not og.origin(HF.replace("653d91e182011f357cf1644013cc94ddf194d349", "main"))["pinned"]
    o = og.origin("https://x.s3.amazonaws.com/Assets/Isaac/6.1/Isaac/Props/box.usd", "isaac-6.1", "isaac:/Isaac/Props/box.usd")
    assert og.label(o) == "Isaac Sim assets 6.1" and o["path"] == "Isaac/Props/box.usd" and not o["pinned"]
    assert og.remote_page("https://user:tok@github.com/a/b.git") == "https://github.com/a/b"  # gitleaks:allow (dummy credential: it must be dropped)
