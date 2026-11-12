"""Third-party USD folder provider (offline, builds a tiny asset library with usd-core)."""

from __future__ import annotations

from pathlib import Path

import pytest

pxr = pytest.importorskip("pxr")
from pxr import Usd, UsdGeom  # noqa: E402

from agentic_sim_world_gen.assets.providers.base import SizeFilter  # noqa: E402
from agentic_sim_world_gen.assets.providers.local_folder import LocalFolderProvider, build_index  # noqa: E402
from agentic_sim_world_gen.license import detect  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


def _cube_layer(path: Path, size, up="Z", mpu=1.0) -> None:
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, up)
    UsdGeom.SetStageMetersPerUnit(stage, mpu)
    root = UsdGeom.Xform.Define(stage, "/Root")
    stage.SetDefaultPrim(root.GetPrim())
    cube = UsdGeom.Cube.Define(stage, "/Root/Geom")
    cube.CreateSizeAttr(1.0)
    UsdGeom.XformCommonAPI(cube).SetScale(tuple(size))
    stage.GetRootLayer().Save()


def _asset(path: Path, payload: Path, doc: str | None = None, variants: bool = False) -> None:
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Root").GetPrim()
    stage.SetDefaultPrim(root)
    root.GetPayloads().AddPayload(f"./{payload.parent.name}/{payload.name}")
    if variants:
        vs = root.GetVariantSets().AddVariantSet("PhysX")
        vs.AddVariant("Enabled")
        vs.AddVariant("Disabled")
    if doc:
        stage.GetRootLayer().documentation = doc
    stage.GetRootLayer().Save()


@pytest.fixture()
def library(tmp_path: Path) -> Path:
    lib = tmp_path / "acme_lib"
    chairs = lib / "Furniture" / "OfficeChair"
    (chairs / "payloads").mkdir(parents=True)
    _cube_layer(chairs / "payloads" / "chair_geo.usdc", (0.6, 0.6, 1.0))
    _asset(chairs / "office_chair_01.usda", chairs / "payloads" / "chair_geo.usdc", "Black mesh office chair", variants=True)
    (chairs / ".thumbs" / "256x256").mkdir(parents=True)
    (chairs / ".thumbs" / "256x256" / "office_chair_01.usda.png").write_bytes(PNG)

    shelves = lib / "Furniture" / "Book_Shelf"
    shelves.mkdir(parents=True)
    _cube_layer(shelves / "bookShelfTall.usda", (90, 35, 180), mpu=0.01)  # centimeters
    (shelves / "bookShelfTall.png").write_bytes(PNG)
    (shelves / "agentic-sim-world-gen.json").write_text('{"license": "CC-BY-NC-4.0", "author": "Shelf Co", "tags": ["bookcase"], "front": {"bookShelf*": 270}}')

    (lib / "Furniture" / "LICENSE.txt").write_text("Creative Commons Attribution 4.0 International Public License\n...")
    (lib / "agentic-sim-world-gen.json").write_text('{"author": "ACME", "exclude": ["*_lod*.usda"]}')
    _cube_layer(lib / "Furniture" / "OfficeChair" / "office_chair_lod1.usda", (0.6, 0.6, 1.0))

    mats = lib / "Materials"
    mats.mkdir()
    _cube_layer(mats / "wood.usda", (1, 1, 1))  # skipped folder
    return lib


def _by_name(recs):
    return {r.name: r for r in recs}


def test_index_finds_main_assets_only(library: Path, tmp_path: Path):
    recs = _by_name(build_index(library, cache_dir=tmp_path / "cache"))
    assert set(recs) == {"office_chair_01.usda", "bookShelfTall.usda"}  # not payload, lod (excluded), Materials

    chair = recs["office_chair_01.usda"]
    assert [round(v, 3) for v in chair.size_m] == [0.6, 0.6, 1.0]
    assert chair.caption == "Black mesh office chair"
    assert chair.variant_selections == {"PhysX": "Enabled"}
    assert chair.variant_sets == {"PhysX": ["Disabled", "Enabled"]}
    assert chair.license == "CC-BY-4.0"  # from Furniture/LICENSE.txt
    assert chair.author == "ACME"  # from the root agentic-sim-world-gen.json
    assert chair.front_deg is None  # default front (+X)
    assert chair.thumbnail_url and chair.thumbnail_url.startswith("file://")
    assert "office chair" in chair.tags and chair.source == "local:acme_lib"
    assert Path(chair.uri).is_absolute()

    shelf = recs["bookShelfTall.usda"]
    assert [round(v, 3) for v in shelf.size_m] == [0.9, 0.35, 1.8]  # cm -> m
    assert shelf.license == "CC-BY-NC-4.0" and shelf.author == "Shelf Co"  # nearest agentic-sim-world-gen.json wins
    assert {"book shelf", "book shelf tall", "bookcase"} <= set(shelf.tags)
    assert shelf.thumbnail_url.endswith("bookShelfTall.png")
    assert shelf.front_deg == 270  # agentic-sim-world-gen.json {"front": {glob: deg}}


def test_search_and_size_filter(library: Path, tmp_path: Path):
    p = LocalFolderProvider(library, cache_dir=tmp_path / "cache")
    assert p.search("office chair", limit=3)[0].name == "office_chair_01.usda"
    assert p.search("bookcase", limit=3)[0].name == "bookShelfTall.usda"
    assert p.search("bookshelf", limit=3, size=SizeFilter(max_z=1.0)) == []


def test_cache_is_incremental(library: Path, tmp_path: Path, monkeypatch):
    cache = tmp_path / "cache"
    build_index(library, cache_dir=cache)
    import agentic_sim_world_gen.assets.providers.local_folder as lf

    calls = []
    real = lf._info
    monkeypatch.setattr(lf, "_info", lambda f, *a: calls.append(f.name) or real(f, *a))
    build_index(library, cache_dir=cache)
    assert calls == []  # nothing changed -> nothing re-read

    _cube_layer(library / "Furniture" / "Book_Shelf" / "bookShelfTall.usda", (120, 40, 200), mpu=0.01)
    recs = _by_name(build_index(library, cache_dir=cache))
    assert calls == ["bookShelfTall.usda"]
    assert [round(v, 3) for v in recs["bookShelfTall.usda"].size_m] == [1.2, 0.4, 2.0]


def test_unrecognized_license_file_is_flagged(tmp_path: Path):
    lib = tmp_path / "lib"
    lib.mkdir()
    _cube_layer(lib / "box.usda", (1, 1, 1))
    (lib / "LICENSE").write_text("Proprietary. Internal use only.")
    (rec,) = build_index(lib, cache_dir=tmp_path / "cache")
    assert rec.license.startswith("see ") and rec.license.endswith("LICENSE")


def test_detect_license_text():
    assert detect("SPDX-License-Identifier: MIT") == "MIT"
    assert detect("Attribution-NonCommercial-ShareAlike 4.0 International") == "CC-BY-NC-SA-4.0"
    assert detect("Apache License\nVersion 2.0, January 2004") == "Apache-2.0"
    assert detect("all rights reserved") is None


S3 = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.1"


def _referrer(path: Path, url: str, offset=(0, 0, 0)) -> None:
    """A third-party file that places a remote asset (usd-core cannot open http by itself)."""
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(root.GetPrim())
    child = UsdGeom.Xform.Define(stage, "/Asset/Part")
    child.GetPrim().GetReferences().AddReference(url)
    UsdGeom.XformCommonAPI(child).SetTranslate(offset)
    stage.GetRootLayer().Save()


def test_remote_refs_use_isaac_assets_root(tmp_path: Path, monkeypatch):
    mirror = tmp_path / "mirror"
    (mirror / "Isaac" / "Props" / "Box").mkdir(parents=True)
    _cube_layer(mirror / "Isaac" / "Props" / "Box" / "box.usda", (2, 1, 0.5))
    monkeypatch.setenv("ISAAC_ASSETS_ROOT", str(mirror))
    lib = tmp_path / "lib"
    lib.mkdir()
    _referrer(lib / "crate.usda", f"{S3}/Isaac/Props/Box/box.usda", offset=(1, 0, 0.25))
    (rec,) = build_index(lib, cache_dir=tmp_path / "cache")
    assert rec.size_m == pytest.approx((2, 1, 0.5))
    assert rec.uri == (lib / "crate.usda").as_posix()  # the record still points at the original


def test_remote_refs_are_fetched(tmp_path: Path, monkeypatch):
    """Remote layers are downloaded with their relative payloads; absolute URLs inside them too."""
    from agentic_sim_world_gen.assets import remote_layers

    srv = tmp_path / "srv"  # what the "server" holds
    (srv / "A" / "payloads").mkdir(parents=True)
    _cube_layer(srv / "A" / "payloads" / "geo.usda", (1, 3, 2))
    _asset(srv / "A" / "a.usda", srv / "A" / "payloads" / "geo.usda")
    (srv / "B").mkdir()
    _cube_layer(srv / "B" / "b.usda", (1, 1, 4))
    a = Usd.Stage.Open(str(srv / "A" / "a.usda"))
    extra = a.DefinePrim("/Root/Extra")
    extra.GetReferences().AddReference(f"{S3}/B/b.usda")
    a.GetRootLayer().Save()

    got = []

    class Resp:
        def __init__(self, url):
            p = srv / url.removeprefix(S3 + "/")
            self.status, self.content = (200, p.read_bytes()) if p.exists() else (404, b"")

        def raise_for_status(self):
            if self.status != 200:
                raise OSError("404")

    class Session:
        def get(self, url, timeout=None):
            got.append(url.removeprefix(S3 + "/"))
            return Resp(url)

    monkeypatch.delenv("ISAAC_ASSETS_ROOT", raising=False)
    monkeypatch.setattr(remote_layers._Fetcher, "session", lambda self: Session())
    lib = tmp_path / "lib"
    lib.mkdir()
    _referrer(lib / "thing.usda", f"{S3}/A/a.usda")
    (rec,) = build_index(lib, cache_dir=tmp_path / "cache")
    assert sorted(got) == ["A/a.usda", "A/payloads/geo.usda", "B/b.usda"]
    assert rec.size_m == pytest.approx((1, 3, 4))


def test_simready_folder_with_asset_json_and_catalog(tmp_path: Path):
    """SimReady-style folders: <slug>/simready_usd/<slug>.usd + asset.json, and a catalog stage."""
    import json

    lib = tmp_path / "retail"
    lib.mkdir()
    (lib / "LICENSE").write_text("Creative Commons Legal Code\n\nCC0 1.0 Universal\n")
    paths = []
    for slug, tags, front in [("atm_kiosk", ["fixture", "ATM", "cash machine"], "-Y"),
                              ("green_tea_pet_2l", ["beverage", "green tea", "PET bottle"], None),
                              ("gondola_shelf", ["fixture"], None), ("shopping_cart", [], None),
                              ("magazine_rack", [], None)]:
        d = lib / "assets" / slug.split("_")[0] / slug / "simready_usd"
        d.mkdir(parents=True)
        _cube_layer(d / f"{slug}.usd", (0.5, 0.7, 1.6))
        stage = Usd.Stage.Open(str(d / f"{slug}.usd"))
        layer = stage.GetRootLayer()
        layer.documentation = "Blender v4.4.0"
        layer.Save()
        side = {"usd": f"{slug}.usd", "name": slug.upper(), "category": tags[0] if tags else None, "tags": tags,
                "description": f"{slug} の 3D モデル", "license": None, "author": "atinfinity"}
        if front:
            side["front"] = front
        side["image_credits"] = [{"subject": "書体", "title": "Noto Sans JP", "author": "Google / Adobe",
                                  "license": "SIL Open Font License 1.1"}, "not a dict", {"author": "no title"}]
        (d / "asset.json").write_text(json.dumps(side))
        paths.append(d / f"{slug}.usd")
    cat = Usd.Stage.CreateNew(str(lib / "assets" / "catalog.usda"))
    for i, p in enumerate(paths):
        cat.DefinePrim(f"/C/A{i}").GetReferences().AddReference("./" + p.relative_to(lib / "assets").as_posix())
    cat.GetRootLayer().Save()

    recs = {r.name: r for r in build_index(lib, tmp_path / "cache")}
    assert set(recs) == {p.name for p in paths}  # the catalog itself is not an asset
    atm = recs["atm_kiosk.usd"]
    assert {"atm", "cash machine", "fixture"} <= set(atm.tags)
    assert atm.caption == "atm_kiosk の 3D モデル" and atm.raw["label"] == "ATM_KIOSK"
    assert atm.license == "CC0-1.0" and atm.author == "atinfinity"  # null license in asset.json -> LICENSE file
    assert atm.front_deg == 270.0 and recs["gondola_shelf.usd"].front_deg is None
    assert [c["title"] for c in atm.credits] == ["Noto Sans JP"]  # image_credits; entries without a title are dropped
    p = LocalFolderProvider(lib, tmp_path / "cache")
    assert p.search("ATM")[0].name == "atm_kiosk.usd"
    assert p.search("green tea bottle")[0].name == "green_tea_pet_2l.usd"


def test_tool_stamp_is_not_a_caption(library: Path, tmp_path: Path):
    from agentic_sim_world_gen.assets.providers.local_folder import _TOOL_STAMP

    assert _TOOL_STAMP.match("Blender v4.4.0") and _TOOL_STAMP.match("Houdini 20.5.332")
    assert not _TOOL_STAMP.match("Black mesh office chair")


def test_asset_json_slug_counts_as_class(tmp_path: Path):
    """A local asset has no USD semantic class; its slug ranks like one, so it is not beaten by a
    SimReady asset of the same thing (the local croissant fell out of the bakery's candidates)."""
    import json

    from agentic_sim_world_gen.assets.record import AssetRecord
    from agentic_sim_world_gen.assets.rerank import rerank, score

    lib = tmp_path / "retail"
    for slug in ["croissant", "curry_bread"]:
        d = lib / "food" / slug / "simready_usd"
        d.mkdir(parents=True)
        _cube_layer(d / f"{slug}.usd", (0.12, 0.06, 0.05))
        (d / "asset.json").write_text(json.dumps({"slug": slug, "category": "food", "license": "CC0-1.0"}))
    recs = {r.name: r for r in build_index(lib, tmp_path / "cache")}
    local = recs["croissant.usd"]
    assert local.semantic_class is None and local.raw["kind"] == "croissant"  # the class itself stays empty (SDG labels)
    assert score(recs["curry_bread.usd"], "bread").matched == ["'bread' in class"]  # "curry_bread" -> "curry bread"

    simready = AssetRecord(id="sr", source="simready-central", uri="sr.usd", name="sm_food_prepared_croissant_a01",
                           bbox_min=(0, 0, 0), bbox_max=(1, 1, 1), semantic_class="croissant")
    assert score(local, "croissant").score == score(simready, "croissant").score
    assert rerank([local, simready], "croissant")[0][0] is local  # ties keep the source order


def test_small_catalog_is_not_an_asset(tmp_path: Path):
    """A catalog.usda of only a few assets (a small library) is still a collection, not one big asset."""
    lib = tmp_path / "farm"
    paths = []
    for slug in ("growing_rack", "plant_pot"):
        d = lib / "assets" / slug / "simready_usd"
        d.mkdir(parents=True)
        _cube_layer(d / f"{slug}.usd", (0.5, 0.5, 0.5))
        paths.append(d / f"{slug}.usd")
    cat = Usd.Stage.CreateNew(str(lib / "assets" / "catalog.usda"))
    for i, p in enumerate(paths):
        cat.DefinePrim(f"/C/A{i}").GetReferences().AddReference("./" + p.relative_to(lib / "assets").as_posix())
    cat.GetRootLayer().Save()
    assert {r.name for r in build_index(lib, tmp_path / "cache")} == {"growing_rack.usd", "plant_pot.usd"}


def test_faces_are_counted(tmp_path: Path):
    """polygons = faces of every mesh, through payloads and instances (heavy scans are not stocked)."""
    lib = tmp_path / "lib"
    (lib / "toy" / "payloads").mkdir(parents=True)
    geo = Usd.Stage.CreateNew(str(lib / "toy" / "payloads" / "geo.usda"))
    geo.SetDefaultPrim(UsdGeom.Xform.Define(geo, "/Root").GetPrim())
    m = UsdGeom.Mesh.Define(geo, "/Root/Quads")
    m.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1)])
    m.CreateFaceVertexCountsAttr([4, 4, 3])
    m.CreateFaceVertexIndicesAttr([0, 1, 2, 3, 0, 1, 5, 4, 0, 3, 4])
    geo.GetRootLayer().Save()
    _asset(lib / "toy" / "toy.usda", lib / "toy" / "payloads" / "geo.usda")
    (rec,) = build_index(lib, cache_dir=tmp_path / "cache")
    assert rec.polygons == 3
