"""Poly Haven's CC0 3D models: catalog, provider, download into the cache, USD in the scene, licenses."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

from agentic_sim_world_gen import polyhaven_models as ph, service
from agentic_sim_world_gen.assets.providers.base import SizeFilter
from agentic_sim_world_gen.assets.providers.polyhaven import PolyHavenProvider
from agentic_sim_world_gen.license import report, to_markdown
from agentic_sim_world_gen.origin import origin
from agentic_sim_world_gen.scene.spec import ResolvedScene

DL = "https://dl.polyhaven.org/file/ph-assets/Models"
CHAIR_RAW = {
    "type": 2, "name": "Dining Chair 02", "category": "Furniture/Seating/Chairs",
    "categories": ["furniture", "seating", "collection: Dining Room"], "tags": ["chair", "dining", "leather"],
    "dimensions": [433.57, 576.42, 973.38], "authors": {"Kizzy": "All"}, "files_hash": "f" * 40,
    "date_published": 1700000000, "description": "Free 8K model: Modern dining chair with tufted leather.",
}
TABLE_RAW = {
    "type": 2, "name": "Side Table 01", "category": "Furniture/Tables/Side & End Tables", "categories": ["furniture"],
    "tags": ["wood"], "dimensions": [500, 500, 550], "authors": {"A": "Model", "B": "Textures"}, "files_hash": "abc",
}


def _usdc(path: Path, size=(0.43, 0.58, 0.97), offset=(0.0, 0.04, 0.001), up="Z", mpu=1.0) -> bytes:
    """A small Blender-like export: a box mesh off center, a material reading two textures."""
    st = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(st, up)
    UsdGeom.SetStageMetersPerUnit(st, mpu)
    root = UsdGeom.Xform.Define(st, "/chair")
    st.SetDefaultPrim(root.GetPrim())
    m = UsdGeom.Mesh.Define(st, "/chair/body")
    (w, d, h), (ox, oy, oz) = size, offset
    pts = [Gf.Vec3f(ox + x * w / 2, oy + y * d / 2, oz + z * h) for x in (-1, 1) for y in (-1, 1) for z in (0, 1)]
    m.CreatePointsAttr(pts)
    m.CreateFaceVertexCountsAttr([4, 4])
    m.CreateFaceVertexIndicesAttr([0, 1, 3, 2, 4, 5, 7, 6])
    mat = UsdShade.Material.Define(st, "/chair/mat")
    for name, f in (("diff", "./textures/chair_diff_1k.jpg"), ("rough", "./textures/chair_rough_1k.exr")):
        t = UsdShade.Shader.Define(st, f"/chair/mat/{name}")
        t.CreateIdAttr("UsdUVTexture")
        t.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(f)
    UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)
    st.GetRootLayer().Save()
    return path.read_bytes()


def _f(url: str, data: bytes) -> dict:
    return {"url": url, "md5": hashlib.md5(data).hexdigest(), "size": len(data)}


@pytest.fixture
def site(tmp_path, monkeypatch):
    """An index with two models and the files API / downloads of dining_chair_02, faked: URL -> bytes."""
    idx = tmp_path / "index.jsonl.gz"
    with gzip.open(idx, "wt", encoding="utf-8") as f:
        for name, raw in (("dining_chair_02", CHAIR_RAW), ("side_table_01", TABLE_RAW)):
            f.write(json.dumps(ph.from_api(name, raw)) + "\n")
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_POLYHAVEN_INDEX", str(idx))
    src = tmp_path / "_src"
    src.mkdir()
    usdc, diff, rough = _usdc(src / "chair.usdc"), b"\xff\xd8diff", b"v/1\x01rough"
    base = f"{DL}/usd/1k/dining_chair_02"
    files = {
        "usd": {"1k": {"usd": {**_f(f"{base}/dining_chair_02_1k.usdc", usdc),
                               "include": {"textures/chair_diff_1k.jpg": _f(f"{base}/textures/chair_diff_1k.jpg", diff)}}}},
        # the USD reads rough_1k.exr but the download only includes the jpg diffuse: found among the other files
        "Rough": {"1k": {"exr": _f(f"{DL}/textures/1k/chair_rough_1k.exr", rough)},
                  "2k": {"exr": _f(f"{DL}/textures/2k/chair_rough_2k.exr", b"2k")}},
    }
    blobs = {f"https://api.polyhaven.com/files/dining_chair_02": json.dumps(files).encode(),
             f"{base}/dining_chair_02_1k.usdc": usdc, f"{base}/textures/chair_diff_1k.jpg": diff,
             f"{DL}/textures/1k/chair_rough_1k.exr": rough}

    class Net:
        calls: list[str] = []
        data = blobs
        api = files

        @staticmethod
        def get(url, timeout):
            ph._allowed(url)
            Net.calls.append(url)
            if url not in Net.data:
                raise ph.FetchError(f"404 {url}")
            return Net.data[url]

    monkeypatch.setattr(ph, "_get", Net.get)
    return Net


def test_catalog_record():
    r = ph.from_api("dining_chair_02", CHAIR_RAW)
    assert r["dimensions_mm"] == [433.6, 576.4, 973.4] and r["license"] == "CC0-1.0" and r["author"] == "Kizzy"
    assert r["collections"] == ["Dining Room"] and r["categories"] == ["furniture", "seating"]
    assert r["url"] == "https://polyhaven.com/a/dining_chair_02" and r["version"] == "f" * 40
    a = ph.to_asset_record(r)
    assert a.uri == "polyhaven:dining_chair_02" and a.semantic_class == "dining chair" and a.front_deg == 270
    assert a.caption == "Modern dining chair with tufted leather."
    assert a.size_m == pytest.approx((0.4336, 0.5764, 0.9734)) and a.bbox_min[2] == 0
    assert "chair" in a.tags and a.has_physics is False
    assert ph.kind_phrases("Furniture/Tables/Side & End Tables") == ["side table", "end table", "table"]
    assert ph.from_api("x", {**CHAIR_RAW, "dimensions": []}) is None
    assert ph.from_api("../x", CHAIR_RAW) is None


def test_provider_search(site):
    p = PolyHavenProvider()
    assert p.available and len(p.records()) == 2
    assert [r.name for r in p.search("dining chair")] == ["dining_chair_02"]
    assert [r.name for r in p.search("end table")] == ["side_table_01"]
    assert p.search("chair", size=SizeFilter(max_z=0.5)) == []  # too big for a 0.5 m high limit


def test_fetch(site):
    m = ph.fetch("dining_chair_02")
    d = ph.cache_root() / "dining_chair_02"
    assert (d / "textures" / "chair_rough_1k.exr").read_bytes() == site.data[f"{DL}/textures/1k/chair_rough_1k.exr"]
    assert m["missing_textures"] == [] and m["version"] == "f" * 40 and m["license"] == "CC0-1.0"
    assert set(m["files"]) == {"dining_chair_02_1k.usdc", "textures/chair_diff_1k.jpg",
                               "textures/chair_rough_1k.exr", "model.usda"}
    assert m["files"]["dining_chair_02_1k.usdc"] == hashlib.sha256((d / "dining_chair_02_1k.usdc").read_bytes()).hexdigest()
    assert m["size_m"] == pytest.approx([0.43, 0.58, 0.97])
    st = Usd.Stage.Open(str(d / "model.usda"))
    assert UsdGeom.GetStageUpAxis(st) == "Z" and UsdGeom.GetStageMetersPerUnit(st) == 1.0
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(st.GetDefaultPrim()).ComputeAlignedRange()
    assert tuple(box.GetMin()) == pytest.approx((-0.215, -0.29, 0.0)) and tuple(box.GetMax()) == pytest.approx((0.215, 0.29, 0.97))
    assert st.GetDefaultPrim().GetCustomDataByKey("agentic_sim_world_gen:polyhaven")["license"] == "CC0-1.0"
    n = len(site.calls)
    assert ph.fetch("dining_chair_02") == m and len(site.calls) == n  # cached
    # the provider now uses the measured size
    a = next(r for r in PolyHavenProvider().records() if r.name == "dining_chair_02")
    assert a.size_m == pytest.approx((0.43, 0.58, 0.97))
    o = origin(a.uri, a.source, a.id)
    assert o["from"] == "polyhaven.com (CC0 models)" and o["version"] == "f" * 40 and o["note"] is None
    assert o["sha256"] == m["files"]["dining_chair_02_1k.usdc"]


def test_fetch_y_up_centimeters(site, tmp_path):
    usdc = _usdc(tmp_path / "_src" / "y.usdc", size=(43, 97, 58), offset=(0, 0, 0), up="Y", mpu=0.01)
    base = f"{DL}/usd/1k/dining_chair_02"
    site.api["usd"]["1k"]["usd"].update(_f(f"{base}/dining_chair_02_1k.usdc", usdc))
    site.data[f"{base}/dining_chair_02_1k.usdc"] = usdc
    site.data["https://api.polyhaven.com/files/dining_chair_02"] = json.dumps(site.api).encode()
    # box's "height" axis (z of the mesh) is the Y-up file's depth; after the rotation it is -y
    m = ph.fetch("dining_chair_02")
    assert m["source_up_axis"] == "Y" and m["source_meters_per_unit"] == 0.01
    assert m["size_m"] == pytest.approx([0.43, 0.58, 0.97])


@pytest.mark.parametrize("break_it", ["md5", "url", "name", "no_usd"])
def test_fetch_refuses(site, break_it):
    usd = site.api["usd"]["1k"]["usd"]
    if break_it == "md5":
        usd["include"]["textures/chair_diff_1k.jpg"]["md5"] = "0" * 32
    elif break_it == "url":
        usd["include"]["textures/chair_diff_1k.jpg"]["url"] = "https://evil.example.com/x.jpg"
    elif break_it == "name":
        usd["include"]["../../escape.jpg"] = usd["include"].pop("textures/chair_diff_1k.jpg")
    else:
        del site.api["usd"]
    site.data["https://api.polyhaven.com/files/dining_chair_02"] = json.dumps(site.api).encode()
    with pytest.raises(ph.FetchError):
        ph.fetch("dining_chair_02")
    assert not (ph.cache_root() / "dining_chair_02").exists()
    assert not (ph.cache_root().parent / "escape.jpg").exists()
    with pytest.raises(ph.FetchError):
        ph.fetch("../etc")


def test_default_run_never_downloads():
    with pytest.raises(ph.FetchError, match="tests do not download"):
        ph.fetch("dining_chair_02")


SPEC = {"name": "dining", "room": {"width": 3.0, "depth": 3.0, "height": 2.4},
        "objects": [{"id": "chair", "query": "dining chair", "position": [1.5, 1.5]}]}


def test_scene(site, tmp_path):
    service.set_provider(PolyHavenProvider())
    try:
        d = tmp_path / "scene"
        st = service.create_scene(SPEC, d)
        assert site.calls == []  # nothing is downloaded until the scene is composed
        c = st["objects"][0]["candidates"][st["objects"][0]["selected"]]
        assert c["name"] == "dining_chair_02"
        res = service.compose_scene(d)
        lic = service.license_report(d)
    finally:
        service.set_provider(None)
    stage = Usd.Stage.Open(res["usd"])
    asset = stage.GetPrimAtPath("/World/Objects/chair/Asset")
    refs = asset.GetMetadata("references").GetAddedOrExplicitItems()
    assert refs[0].assetPath == "./assets/polyhaven/dining_chair_02/model.usda"
    assert (d / "assets/polyhaven/dining_chair_02/textures/chair_rough_1k.exr").exists()
    obj = stage.GetPrimAtPath("/World/Objects/chair")
    assert stage.GetPrimAtPath("/World/Objects/chair/Collider").IsValid()  # no physics in the model: a static box
    assert obj.GetCustomDataByKey("agentic_sim_world_gen:license") == "CC0-1.0" and obj.GetCustomDataByKey("agentic_sim_world_gen:author") == "Kizzy"
    assert obj.GetCustomDataByKey("agentic_sim_world_gen:origin")["version"] == "f" * 40
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(asset).ComputeAlignedRange()
    assert box.GetMin()[2] == pytest.approx(0, abs=1e-6)
    assert sorted(round(v, 3) for v in box.GetSize()[:2]) == [0.43, 0.58]
    assert lic["warnings"] == [] and lic["origin_warnings"] == []
    item = lic["assets"]["CC0-1.0"][0]
    assert item["source"] == "https://polyhaven.com/a/dining_chair_02"
    assert item["credit"].startswith('"Dining Chair 02" by Kizzy, Poly Haven (https://polyhaven.com/a/dining_chair_02), CC0 1.0')
    md = (d / "LICENSES.md").read_text()
    assert "Kizzy" in md and "polyhaven.com (CC0 models)" in md


def test_report_before_fetch(site, tmp_path):
    """The license report works from the index alone (no download)."""
    service.set_provider(PolyHavenProvider())
    try:
        d = tmp_path / "scene"
        service.create_scene(SPEC, d)
    finally:
        service.set_provider(None)
    s = ResolvedScene.load(d / "scene.resolved.json")
    r = report(s)
    assert "files_hash " + "f" * 40 in to_markdown(s, r) and site.calls == []


def test_fetch_repoints_absolute_texture_paths(site, tmp_path):
    """The 1k USD of dining_table names its textures /mnt/prod/Assets/...: the wrapper points them at textures/."""
    p = tmp_path / "_src" / "abs.usdc"
    _usdc(p)
    layer = Sdf.Layer.FindOrOpen(str(p))
    st = Usd.Stage.Open(layer)
    st.GetPrimAtPath("/chair/mat/diff").GetAttribute("inputs:file").Set(
        Sdf.AssetPath("/mnt/prod/Assets/Models/chair/staging/textures/chair_diff_1k.jpg"))
    layer.Save()
    usdc = p.read_bytes()
    base = f"{DL}/usd/1k/dining_chair_02"
    site.api["usd"]["1k"]["usd"].update(_f(f"{base}/dining_chair_02_1k.usdc", usdc))
    site.data[f"{base}/dining_chair_02_1k.usdc"] = usdc
    site.data["https://api.polyhaven.com/files/dining_chair_02"] = json.dumps(site.api).encode()
    m = ph.fetch("dining_chair_02")
    assert m["missing_textures"] == [] and m["repointed_textures"] == ["/mnt/prod/Assets/Models/chair/staging/textures/chair_diff_1k.jpg"]
    d = ph.cache_root() / "dining_chair_02"
    w = Usd.Stage.Open(str(d / "model.usda"))
    a = w.GetPrimAtPath("/Model/Geom/mat/diff").GetAttribute("inputs:file").Get()
    assert Path(a.resolvedPath) == (d / "textures" / "chair_diff_1k.jpg").resolve()


def _leaf_cards(p: Path) -> bytes:
    """Like potted_plant_01: the UsdPreviewSurface has opacity 1, only Blender's MaterialX graph reads the alpha map."""
    _usdc(p)
    layer = Sdf.Layer.FindOrOpen(str(p))
    st = Usd.Stage.Open(layer)
    uv = UsdShade.Shader.Define(st, "/chair/mat/uvmap")
    uv.CreateIdAttr("UsdPrimvarReader_float2")
    uv_out = uv.CreateOutput("result", Sdf.ValueTypeNames.Float2)
    diff = UsdShade.Shader(st.GetPrimAtPath("/chair/mat/diff"))
    diff.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(uv_out)
    surf = UsdShade.Shader.Define(st, "/chair/mat/surface")
    surf.CreateIdAttr("UsdPreviewSurface")
    surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
        diff.CreateOutput("rgb", Sdf.ValueTypeNames.Float3))
    surf.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(1.0)
    mx = UsdShade.Shader.Define(st, "/chair/mat/NodeGraphs/Image_Texture_003_Color")
    mx.CreateIdAttr("ND_image_color3")
    mx.CreateInput("file", Sdf.ValueTypeNames.Asset).Set("./textures/chair_diff_1k.jpg".replace("diff", "alpha"))
    layer.Save()
    return p.read_bytes()


def _serve_usdc(site, usdc: bytes) -> None:
    base = f"{DL}/usd/1k/dining_chair_02"
    site.api["usd"]["1k"]["usd"].update(_f(f"{base}/dining_chair_02_1k.usdc", usdc))
    site.api["usd"]["1k"]["usd"]["include"]["textures/chair_alpha_1k.jpg"] = _f(f"{base}/textures/chair_alpha_1k.jpg", b"alpha")
    site.data[f"{base}/dining_chair_02_1k.usdc"] = usdc
    site.data[f"{base}/textures/chair_alpha_1k.jpg"] = b"alpha"
    site.data["https://api.polyhaven.com/files/dining_chair_02"] = json.dumps(site.api).encode()


def _cutout_of(d: Path):
    w = Usd.Stage.Open(str(d / "model.usda"))
    surf = UsdShade.Shader(w.GetPrimAtPath("/Model/Geom/mat/surface"))
    src = surf.GetInput("opacity").GetConnectedSources()[0][0]
    tex = UsdShade.Shader(src.source.GetPrim())
    return surf, src, tex, w  # keep the stage alive


def test_alpha_map_becomes_a_cutout(site, tmp_path):
    """RTX draws the UsdPreviewSurface: the alpha map of leaf cards is wired to its opacity, with a threshold."""
    _serve_usdc(site, _leaf_cards(tmp_path / "_src" / "leaf.usdc"))
    ph.fetch("dining_chair_02")
    d = ph.cache_root() / "dining_chair_02"
    surf, src, tex, _w = _cutout_of(d)
    assert src.sourceName == "r" and tex.GetIdAttr().Get() == "UsdUVTexture"
    assert Path(tex.GetInput("file").Get().resolvedPath) == (d / "textures" / "chair_alpha_1k.jpg").resolve()
    assert str(tex.GetInput("st").GetConnectedSources()[0][0].source.GetPath()) == "/Model/Geom/mat/uvmap"
    assert surf.GetInput("opacityThreshold").Get() == pytest.approx(0.5)


def test_old_cache_is_rewrapped_without_downloading(site, tmp_path):
    _serve_usdc(site, _leaf_cards(tmp_path / "_src" / "leaf.usdc"))
    m = ph.fetch("dining_chair_02")
    d = ph.cache_root() / "dining_chair_02"
    old = {k: v for k, v in m.items() if k != "wrapper_version"}
    (d / "manifest.json").write_text(json.dumps(old), encoding="utf-8")
    (d / "model.usda").write_text("#usda 1.0\n", encoding="utf-8")  # as an older agentic-sim-world-gen left it (no cutout)
    (d / "manifest.json").write_text(json.dumps({**old, "files": {**old["files"],
        "model.usda": hashlib.sha256(b"#usda 1.0\n").hexdigest()}}), encoding="utf-8")
    site.calls.clear()
    m2 = ph.fetch("dining_chair_02")
    assert site.calls == [] and m2["wrapper_version"] == ph.WRAPPER_VERSION
    assert m2["files"]["model.usda"] == hashlib.sha256((d / "model.usda").read_bytes()).hexdigest()
    assert _cutout_of(d)[1].sourceName == "r"
    assert ph.fetch("dining_chair_02") == m2  # once
