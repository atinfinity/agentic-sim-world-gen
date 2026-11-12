"""CC0 PBR materials from ambientCG / Poly Haven: catalog, download into the cache, search, USD, licenses."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import zipfile

import pytest
from PIL import Image

from agentic_sim_world_gen import materials, pbr, service

TATAMI_RAW = {
    "assetId": "Tatami001", "releaseDate": "2023-10-18 18:00:00", "displayName": "Tatami 001",
    "displayCategory": "Tatami", "tags": ["1", "beige", "floor", "tatami", "yellow"], "dimensionX": 250,
    "maps": ["color", "displacement", "normal", "roughness", "ambient-occlusion"],
    "previewImage": {"256-JPG-FFFFFF": "https://acg-media.struffelproductions.com/x/Tatami001.jpg"},
    "downloadFolders": {"default": {"downloadFiletypeCategories": {"zip": {"downloads": [
        {"attribute": "2K-JPG", "downloadLink": "https://ambientcg.com/get?file=Tatami001_2K-JPG.zip"},
        {"attribute": "1K-JPG", "downloadLink": "https://ambientcg.com/get?file=Tatami001_1K-JPG.zip", "size": 9}]}}}},
}
PLASTER_RAW = {
    "type": 1, "name": "Painted Plaster Wall", "tags": ["white", "painted"], "categories": ["plaster", "wall", "indoor"],
    "dimensions": [3000, 3000], "authors": {"Rob Tuytel": "All"}, "files_hash": "abc123", "date_published": 1597061145,
    "attributes": {"surface_use": "wall"}, "description": "White painted plaster wall",
}


def _jpg(rgb) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (8, 8), rgb).save(b, "JPEG")
    return b.getvalue()


COLOR, ROUGH, NORMAL = _jpg((200, 180, 110)), _jpg((180, 180, 180)), _jpg((128, 128, 255))


def _zip(evil=False) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("Tatami001_1K-JPG_Color.jpg", COLOR)
        z.writestr("Tatami001_1K-JPG_Roughness.jpg", ROUGH)
        z.writestr("Tatami001_1K-JPG_NormalGL.jpg", NORMAL)
        z.writestr("Tatami001_1K-JPG_NormalDX.jpg", b"dx")
        if evil:
            z.writestr("../Tatami001_1K-JPG_Color.jpg", b"evil")
    return b.getvalue()


PH_FILES = {k: {"1k": {"jpg": {"url": f"https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/plaster_01/{f}",
                               "md5": hashlib.md5(data).hexdigest()}}}
            for k, f, data in (("Diffuse", "diff.jpg", COLOR), ("Rough", "rough.jpg", ROUGH), ("nor_gl", "nor.jpg", NORMAL))}


@pytest.fixture
def net(tmp_path, monkeypatch):
    """The two sites, faked: URL -> bytes. `calls` lists what was downloaded; `offline` makes every download fail."""
    recs = [pbr.from_ambientcg(TATAMI_RAW), pbr.from_polyhaven("plaster_01", PLASTER_RAW)]
    p = tmp_path / "pbr.jsonl.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r) for r in recs) + "\n")
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_PBR_INDEX", str(p))
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_PBR", "1")
    state = {"calls": [], "offline": False, "files": {
        "https://ambientcg.com/get?file=Tatami001_1K-JPG.zip": _zip(),
        "https://api.polyhaven.com/files/plaster_01": json.dumps(PH_FILES).encode(),
        **{v["1k"]["jpg"]["url"]: d for v, d in zip(PH_FILES.values(), (COLOR, ROUGH, NORMAL))}}}

    def get(lib, url, timeout):
        pbr._allowed(lib, url)
        state["calls"].append(url)
        if state["offline"]:
            raise pbr.FetchError(f"offline: {url}")
        return state["files"][url]

    monkeypatch.setattr(pbr, "_get", get)
    return state


def _scene(tmp_path):
    d = tmp_path / "s"
    service.create_scene({"name": "m", "room": {"width": 4, "depth": 3}, "objects": []}, d)
    return d


def test_catalog_records():
    t = pbr.from_ambientcg(TATAMI_RAW)
    assert t["id"] == "ambientCG/Tatami001" and t["category"] == "Natural" and t["size_m"] == 2.5
    assert t["keywords"] == ["beige", "floor", "tatami", "yellow"] and t["uses"] == ["floor"]
    assert t["download"]["url"].endswith("Tatami001_1K-JPG.zip") and t["license"] == "CC0-1.0"
    assert t["url"] == "https://ambientcg.com/view?id=Tatami001" and t["version"] == "2023-10-18"
    p = pbr.from_polyhaven("plaster_01", PLASTER_RAW)
    assert p["category"] == "Plaster" and p["size_m"] == 3.0 and p["author"] == "Rob Tuytel" and p["version"] == "abc123"
    # not a floor / wall material: a decal with opacity, a sticker, nothing to download, a model
    assert pbr.from_ambientcg({**TATAMI_RAW, "maps": ["color", "opacity"]}) is None
    assert pbr.from_ambientcg({**TATAMI_RAW, "displayCategory": "Sticker"}) is None
    assert pbr.from_ambientcg({**TATAMI_RAW, "downloadFolders": {"default": {"downloadFiletypeCategories": []}}}) is None
    assert pbr.from_ambientcg({**TATAMI_RAW, "assetId": "../x"}) is None
    assert pbr.from_polyhaven("pizza", {**PLASTER_RAW, "categories": ["food"]}) is None


def test_bundled_index_loads(monkeypatch):
    monkeypatch.delenv("AGENTIC_SIM_WORLD_GEN_PBR")
    recs = pbr.records()
    assert len(recs) > 1000 and {r["library"] for r in recs} == {"ambientCG", "PolyHaven"}
    assert all(r["license"] == "CC0-1.0" and r["id"] == f"{r['library']}/{r['name']}" for r in recs)
    assert all(r["category"] in materials.CATEGORY for r in recs)
    assert materials.by_id("ambientCG/Tatami001")["title"] == "Tatami 001"


def test_words_find_pbr_next_to_mdl(tmp_path, net):
    d = _scene(tmp_path)
    st = service.set_room(d, floor="tatami")  # a preset stays a preset
    assert st["room"]["floor"] == "tatami" and not net["calls"]
    st = service.set_room(d, floor="beige tatami mat")
    c = st["surface_candidates"]["floor"]
    assert c["candidates"][0]["id"] == "ambientCG/Tatami001" and c["selected"] == 0
    assert "CC0" in c["candidates"][0]["source"] and c["candidates"][0]["size_m"] == 2.5
    f = st["room"]["floor"]
    assert f["texture"] == "pbr:ambientCG/Tatami001/color.jpg" and f["normal"].endswith("/normal.jpg")
    assert f["roughness_texture"].endswith("/roughness.jpg") and f["tile_m"] == 2.5
    assert f["static_friction"] == materials.CATEGORY["Natural"]["static_friction"]
    cache = pbr.cache_root() / "ambientCG" / "Tatami001"
    assert sorted(x.name for x in cache.iterdir()) == ["color.jpg", "manifest.json", "normal.jpg", "roughness.jpg"]
    man = json.loads((cache / "manifest.json").read_text())
    assert man["files"]["color.jpg"] == hashlib.sha256(COLOR).hexdigest() and man["zip_sha256"]
    assert man["download"] == ["https://ambientcg.com/get?file=Tatami001_1K-JPG.zip"] and man["license"] == "CC0-1.0"
    assert man["color"].startswith("#")

    # cached: no second download, works offline
    net["offline"], n = True, len(net["calls"])
    service.set_room(d, walls="white plaster")
    st = service.set_room(d, floor="pbr:ambientCG/Tatami001")
    assert st["room"]["floor"]["texture"].startswith("pbr:ambientCG/Tatami001/")
    assert len([u for u in net["calls"][n:] if "Tatami" in u]) == 0


def test_offline_falls_back_to_the_next_candidate(tmp_path, net):
    net["offline"] = True
    d = _scene(tmp_path)
    st = service.set_room(d, walls="white painted plaster")
    c = st["surface_candidates"]["walls"]
    assert c["candidates"][0]["id"] == "PolyHaven/plaster_01" and c["skipped"][0]["id"] == "PolyHaven/plaster_01"
    if c["selected"] is None:  # nothing else matched: the walls stay as they were
        assert st["room"]["walls"] == "paint_white" and "offline" in c["note"]
    else:
        assert "mdl" in st["room"]["walls"]
    with pytest.raises(pbr.FetchError):
        service.select_surface(d, "walls", 0)
    net["offline"] = False
    st = service.select_surface(d, "walls", 0)
    assert st["room"]["walls"]["texture"] == "pbr:PolyHaven/plaster_01/color.jpg"
    assert sum("polyhaven" in u for u in net["calls"]) >= 4  # the file list and the three maps


def test_polyhaven_md5_is_checked(tmp_path, net):
    url = PH_FILES["Rough"]["1k"]["jpg"]["url"]
    net["files"][url] = b"tampered"
    with pytest.raises(pbr.FetchError, match="md5"):
        pbr.fetch(pbr.by_id("PolyHaven/plaster_01"))
    assert pbr.cached(pbr.by_id("PolyHaven/plaster_01")) is None


def test_two_servers_fetch_the_same_material(tmp_path, net):
    """Both pass "not fetched yet" before either writes; both get the material, nothing is left behind."""
    import threading

    gate, inner, errors, got = threading.Barrier(2, timeout=10), pbr._get, [], []

    def get(lib, url, timeout):
        try:
            gate.wait()
        except threading.BrokenBarrierError:
            pass
        return inner(lib, url, timeout)

    pbr._get = get  # the fixture's monkeypatch puts the real one back

    def run():
        try:
            got.append(pbr.fetch(pbr.by_id("PolyHaven/plaster_01")))
        except Exception as e:  # noqa: BLE001
            errors.append(e)
            gate.abort()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(got) == 2 and got[0]["files"] == got[1]["files"]
    d = pbr.cache_root() / "PolyHaven" / "plaster_01"
    assert sorted(f.name for f in d.iterdir()) == ["color.jpg", "manifest.json", "normal.jpg", "roughness.jpg"]
    assert (d / "color.jpg").read_bytes() == COLOR and pbr.cached(pbr.by_id("PolyHaven/plaster_01")) == got[1]


def test_only_fixed_sources(tmp_path, net):
    with pytest.raises(pbr.FetchError):
        pbr._allowed("ambientCG", "https://evil.example.com/get?file=Tatami001_1K-JPG.zip")
    with pytest.raises(pbr.FetchError):
        pbr._allowed("PolyHaven", "https://ambientcg.com/get?file=x")
    bad = {**pbr.by_id("ambientCG/Tatami001"), "download": {"url": "https://evil.example.com/t.zip"}}
    with pytest.raises(pbr.FetchError, match="refusing"):
        pbr.fetch(bad)
    with pytest.raises(pbr.FetchError):
        pbr.fetch({"id": "x", "library": "ambientCG", "name": "../../etc"})
    for ref in ("pbr:ambientCG/../color.jpg", "pbr:Elsewhere/x/color.jpg", "pbr:ambientCG/Tatami001/../../a.jpg",
                "pbr:ambientCG/Tatami001/evil.sh"):
        assert pbr.parse_ref(ref) is None
        with pytest.raises(ValueError):
            pbr.resolve_ref(ref)


def test_zip_entries_outside_the_top_level_are_ignored(tmp_path, net):
    net["files"]["https://ambientcg.com/get?file=Tatami001_1K-JPG.zip"] = _zip(evil=True)
    man = pbr.fetch(pbr.by_id("ambientCG/Tatami001"))
    assert (pbr.cache_root() / "ambientCG" / "Tatami001" / "color.jpg").read_bytes() == COLOR
    assert set(man["files"]) == {"color.jpg", "roughness.jpg", "normal.jpg"}
    assert not (pbr.cache_root() / "ambientCG" / "Tatami001_1K-JPG_Color.jpg").exists()


def test_compose_copies_maps_and_wires_roughness(tmp_path, net):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdShade

    d = _scene(tmp_path)
    service.set_room(d, floor="tatami mat")
    import shutil

    shutil.rmtree(pbr.cache_root())  # cache cleared: compose fetches the maps again
    service.compose_scene(d)
    tex = d / "textures"
    assert {"ambientCG_Tatami001_color.jpg", "ambientCG_Tatami001_roughness.jpg",
            "ambientCG_Tatami001_normal.jpg"} <= {x.name for x in tex.iterdir()}
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    ps = UsdShade.Shader(stage.GetPrimAtPath("/World/Room/Looks/Floor/PreviewSurface"))
    src = ps.GetInput("roughness").GetConnectedSources()[0][0]
    assert src.sourceName == "r" and src.source.GetPath().name == "RoughnessTexture"
    f = UsdShade.Shader(src.source.GetPrim()).GetInput("file").Get().path
    assert f.endswith("textures/ambientCG_Tatami001_roughness.jpg")


def test_license_report_and_sources(tmp_path, net):
    d = _scene(tmp_path)
    service.set_room(d, floor="tatami mat", walls="painted plaster")
    r = service.license_report(d)
    by = {m["surface"]: m for m in r["materials"]}
    assert by["floor"]["license"] == "CC0-1.0" and by["floor"]["id"] == "ambientCG/Tatami001"
    assert by["walls"]["origin"]["version"] == "abc123" and "Rob Tuytel" in by["walls"]["credit"]
    assert by["floor"]["origin"]["files"]["color.jpg"] == hashlib.sha256(COLOR).hexdigest()
    src = {g["from"]: g for g in r["sources"]}
    assert src["ambientCG (CC0 PBR)"]["count"] == 1 and "PolyHaven (CC0 PBR)" in src
    md = (d / "LICENSES.md").read_text()
    assert "\"Tatami 001\", ambientCG (https://ambientcg.com/view?id=Tatami001), CC0 1.0, version 2023-10-18" in md
    assert "ambientCG/Tatami001 2023-10-18 — https://ambientcg.com/get?file=Tatami001_1K-JPG.zip: floor" in md


def test_both_sources_side_by_side():
    """PBR records have no color, so "white plaster" scores MDL higher; the best PBR ones still make the list.
    One ambientCG series (Wallpaper001A/B/C) counts as one group for the per-file cap."""
    mdl = [{"id": f"vMaterials_2/Plaster/P{i}:P{i}_White", "library": "vMaterials_2", "category": "Plaster",
            "mdl": f"/NVIDIA/Materials/vMaterials_2/Plaster/P{i}.mdl", "name": f"P{i}_White", "color": "#f0f0f0",
            "keywords": ["plaster", "white"]} for i in range(10)]
    acg = [{"id": f"ambientCG/Plaster00{i}{v}", "library": "ambientCG", "category": "Plaster", "name": f"Plaster00{i}{v}",
            "keywords": ["plaster", "wall"], "uses": ["wall"]} for i in range(3) for v in "ABC"]
    out = materials.search("white plaster", None, "walls", recs=tuple(mdl + acg))
    srcs = [r["library"] for r in out]
    assert len(out) == 8 and srcs.count("ambientCG") == 3 and srcs[:5] == ["vMaterials_2"] * 5
    assert [r["id"] for r in out if r["library"] == "ambientCG"] == [
        "ambientCG/Plaster000A", "ambientCG/Plaster000B", "ambientCG/Plaster001A"]
