"""Plain rugs built in compose: no catalog has a floor rug, so a rug / carpet / mat search offers a thin slab
with a CC0 carpet texture; furniture stands on it and aisles / doors / robots pass over it (no network)."""

import json

import pytest

from agentic_sim_world_gen import pbr, service
from agentic_sim_world_gen.assets import generic, rug
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.layout.validate import flat


def rec(name, cls, tags=(), caption="", size=(0.4, 0.3, 0.02)):
    w, d, h = size
    return AssetRecord(id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
                       bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), semantic_class=cls,
                       tags=list(tags), caption=caption, license="CC-BY-4.0")


PLACEMAT = rec("placemat_01", "placemat", ["mat", "table"], "A woven placemat.", (0.45, 0.3, 0.005))
SOFA = rec("sofa_01", "sofa", ["sofa"], "A sofa.", (2.0, 0.9, 0.8))
# SimReady's D00144: a 0.54 m woven cloth mat tagged "rug" -- a real (not weak) match for "rug"
CLOTH = rec("D00144", "rug", ["rug", "cloth", "woven"], "A small woven cloth rug.", (0.544, 0.473, 0.004))
TABLE = rec("coffee_table", "coffee table", ["table"], "A low coffee table.", (1.0, 0.6, 0.45))
CATALOG = {"rug": [PLACEMAT], "carpet": [], "area rug": [CLOTH], "sofa": [SOFA], "coffee table": [TABLE]}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


@pytest.fixture
def carpet_tex(monkeypatch):
    """The CC0 PBR index on, and the maps of its carpets already in the (temporary) cache: no download."""
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_PBR", "1")
    for r in pbr.records():
        if r["category"] in rug.CATEGORIES:
            d = pbr.cache_root() / r["library"] / r["name"]
            d.mkdir(parents=True, exist_ok=True)
            for m in ("color", "normal", "roughness"):
                (d / f"{m}.jpg").write_bytes(b"\xff\xd8 fake " + m.encode())
            (d / "manifest.json").write_text(json.dumps({
                "id": r["id"], "library": r["library"], "name": r["name"], "title": r.get("title"), "url": r.get("url"),
                "license": "CC0-1.0", "author": r.get("author"), "size_m": r.get("size_m"), "color": "#7a2e2a",
                "files": {f"{m}.jpg": "x" for m in ("color", "normal", "roughness")}}))

    def refuse(*a, **k):
        raise pbr.FetchError("tests do not download")

    monkeypatch.setattr(pbr, "_ambientcg_maps", refuse)
    monkeypatch.setattr(pbr, "_polyhaven_maps", refuse)


def test_words():
    for q in ("rug", "area rug", "beige shag rug", "carpet", "door mat", "doormat", "hallway runner", "round rug",
              "dining table rug", "rug under coffee table", "under desk mat", "kitchen runner", "kilim", "rug pad",
              "bath mat", "yoga mat"):
        assert generic.kind_of(q) == "rug", q
    for q in ("placemat", "table mat", "mouse mat", "carpet extractor", "table runner", "cutting board", "sofa",
              "sushi mat", "crash mat", "landing mat", "tatami mat", "trampoline mat", "runner", "carpet tile",
              "wall hanging rug"):
        assert generic.kind_of(q) is None, q
    assert rug.round_asked("round wool rug") and not rug.round_asked("wool rug")
    assert rug.default_size("door mat") == (0.8, 0.5, 0.012) and rug.default_size("yoga mat")[:2] == (1.83, 0.61)
    assert rug.default_size("kitchen runner")[:2] == (1.8, 0.6) and rug.default_size("rug") == (2.0, 1.4, 0.012)


def test_size_and_uri():
    assert rug.rug_size(None, None) == (2.0, 1.4, 0.012)
    assert rug.rug_size((3.0, 2.0, 0.02), None) == (3.0, 2.0, 0.02)
    assert rug.rug_size(None, (1.0, 0.6, 0.0)) == (1.0, 0.6, 0.012)
    assert rug.rug_size((0, 2.5, 0)) == (2.5, 2.0, 0.012)  # the short side asked: the rug gets wider, not cut
    assert rug.rug_size((0, 0, 0.05))[2] == 0.03 and "built 3 cm" in rug.thickness_note((0, 0, 0.05))
    assert rug.thickness_note(None, (3.0, 2.0, 0.0)) is None
    assert "built 3 cm" in rug.candidates("rug", min_size=(0, 0, 0.05))[0]["match"]["note"]
    w, d, _ = rug.rug_size(None, None, "round")
    assert w == d
    p = generic.parse(rug.uri_for(2.4, 1.6, 0.015, "ambientCG/Carpet016", "round"))
    assert p == {"kind": "rug", "w": 2.4, "d": 1.6, "t": 0.015, "shape": "round", "mat": "ambientCG/Carpet016"}
    assert generic.parse("generic:rug?w=99&d=1&t=0.01") is None
    assert generic.parse("generic:rug?w=2&d=1&t=0.01&mat=..%2Fetc") is None
    assert generic.is_rug("generic:rug?w=2&d=1&t=0.01") and not generic.is_rug("generic:whiteboard?w=1&h=1")


def test_candidates_plain_and_textured(monkeypatch):
    plain = rug.candidates("rug")
    assert len(plain) == 1 and "mat=" not in plain[0]["uri"] and plain[0]["license"] == "CC0-1.0"
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_PBR", "1")
    cs = rug.candidates("beige wool rug")
    assert len(cs) == rug.CANDIDATES and all(c["source"] == "generic" and c["match"]["generic"] for c in cs)
    assert all(generic.parse(c["uri"])["mat"] for c in cs)
    assert cs[0]["credits"][0]["license"] == "CC0-1.0"
    assert all("round" in c["uri"] for c in rug.candidates("round rug"))


def test_kind_availability_offers_the_rug():
    a = service.kind_availability(["rug", "carpet", "area rug"])
    assert a["generic"] == "rug" and "plain rug" in a["generic_hint"]


def make(tmp_path, objects, **room):
    return service.create_scene({"name": "s", "room": {"width": 6, "depth": 5, "height": 2.7, **room},
                                 "objects": objects}, tmp_path / "s")


def living(tmp_path):
    """A rug under the coffee table and in front of the sofa: the furniture stands on it."""
    return make(tmp_path, [
        {"id": "rug", "query": "rug", "alt_queries": ["carpet"], "position": [3.0, 2.5], "label": "ラグ",
         "min_size": [2.4, 1.6, 0], "tint": "#7a2e2a"},
        {"id": "table", "query": "coffee table", "position": [3.0, 2.5]},
        {"id": "sofa", "query": "sofa", "position": [3.0, 1.2]},
    ])


def test_rug_search_puts_the_generic_rug_first(tmp_path):
    st = living(tmp_path)
    s = service.load(tmp_path / "s")
    p = s.placements[0]
    assert p.asset.source == "generic" and generic.is_rug(p.asset) and not p.substitute
    assert p.asset.size_m == pytest.approx((2.4, 1.6, 0.012))
    assert p.candidates[0]["match"]["generic"]
    assert flat(s.obj("rug"), p.asset) and not flat(s.obj("table"), s.placements[1].asset)
    assert not [i for i in st["issues"] if "rug" in (i.get("objects") or [i.get("object_id")])]


def test_small_cloth_rug_is_no_floor_rug(tmp_path):
    """A 0.54 m cloth tagged "rug" is a real match, but no floor rug: the generic rug comes first."""
    from agentic_sim_world_gen.assets.rerank import score

    assert not score(CLOTH, "area rug").weak
    make(tmp_path, [{"id": "rug", "query": "area rug", "position": [3.0, 2.5]},
                    {"id": "rug2", "query": "area rug", "position": [3.0, 1.0], "max_size": [0.6, 0.5, 0.01]}])
    s = service.load(tmp_path / "s")
    assert s.placements[0].asset.source == "generic" and s.placements[0].candidates[-1]["name"] == "D00144"
    assert s.placements[1].asset.source == "generic"  # the size asked fits the cloth: still not a floor rug


def test_rug_is_not_missing(tmp_path):
    from agentic_sim_world_gen import missing

    make(tmp_path, [{"id": "rug", "query": "rug", "position": [3.0, 2.5]}])
    assert all(r["query"] != "rug" for r in missing.summary()["rows"])


def test_thin_floor_thing_that_is_no_rug_is_checked(tmp_path):
    """A laptop dropped on the floor in a door swing still blocks the door: only rugs / mats lie flat."""
    from agentic_sim_world_gen.layout import validate

    laptop = rec("laptop", "laptop", ["laptop", "computer"], "A closed laptop.", (0.33, 0.23, 0.018))
    CATALOG["laptop"] = [laptop]
    try:
        st = make(tmp_path, [{"id": "pc", "query": "laptop", "position": [0.3, 2.5]}],
                  openings=[{"kind": "door", "wall": "west", "offset": 2.05, "width": 0.9}])
    finally:
        del CATALOG["laptop"]
    s = service.load(tmp_path / "s")
    assert not validate.flat(s.obj("pc"), s.placements[0].asset)
    assert [i for i in st["issues"] if i["kind"] == "blocks_door"]
    mat = rec("mat", "floor mat", ["mat", "floor mat"], "A floor mat.", (0.8, 0.5, 0.015))
    assert validate.flat(s.obj("pc"), mat)


def test_rug_is_no_overlap_and_no_obstacle(tmp_path, monkeypatch):
    from agentic_sim_world_gen.layout import validate
    from agentic_sim_world_gen.layout.usability import _units
    from agentic_sim_world_gen.scene.conflicts import _floor_units

    living(tmp_path)
    s = service.load(tmp_path / "s")
    assert not [i for i in validate.check(s) if "rug" in i.objects]
    assert "rug" not in {u.o.id for u in _units(s)} and "table" in {u.o.id for u in _units(s)}
    assert "rug" not in {o.id for o, _, _ in _floor_units(s)}
    # without the rule, the table on a 1.2 cm rug would overlap it (validate's 1 cm tolerance)
    monkeypatch.setattr(validate, "flat", lambda o, a: False)
    assert [i for i in validate.check(s) if i.kind == "overlap" and "rug" in i.objects]


def test_rug_may_lie_in_front_of_a_door(tmp_path, monkeypatch):
    from agentic_sim_world_gen.layout import validate

    door = {"openings": [{"kind": "door", "wall": "west", "offset": 2.05, "width": 0.9}]}
    st = make(tmp_path, [{"id": "mat", "query": "door mat", "position": [0.35, 2.5], "yaw_deg": 90,
                          "max_size": [0.8, 0.5, 0]}], **door)
    assert service.load(tmp_path / "s").placements[0].asset.source == "generic"
    assert not [i for i in st["issues"] if i["kind"] in ("blocks_door", "in_keep_clear")], st["issues"]
    monkeypatch.setattr(validate, "flat", lambda o, a: False)  # a 1.2 cm box would block the door
    assert [i for i in validate.check(service.load(tmp_path / "s")) if i.kind == "blocks_door"]


def test_compose_builds_the_textured_rug(tmp_path, carpet_tex):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

    living(tmp_path)
    s = service.load(tmp_path / "s")
    a = s.placements[0].asset
    mat = generic.parse(a.uri)["mat"]
    out = service.compose_scene(tmp_path / "s")
    stage = Usd.Stage.Open(out["usd"])
    prim = stage.GetPrimAtPath("/World/Objects/rug")
    assert prim.GetCustomDataByKey("agentic_sim_world_gen:source") == "generic"
    assert prim.GetCustomDataByKey("agentic_sim_world_gen:license") == "CC0-1.0"
    assert prim.GetCustomDataByKey("agentic_sim_world_gen:label") == "ラグ"
    assert prim.GetCustomDataByKey("agentic_sim_world_gen:generic")["kind"] == "rug"
    files = list((tmp_path / "s" / "assets" / "generic").glob("rug_2p4x1p6x0p012_*.usda"))
    assert len(files) == 1
    slab = stage.GetPrimAtPath("/World/Objects/rug/Asset/Slab")
    assert slab.IsA(UsdGeom.Mesh) and not slab.HasAPI(UsdPhysics.CollisionAPI)
    mesh = UsdGeom.Mesh(slab)  # flat top: per-face normals, not smoothed into the bevel
    assert mesh.GetNormalsInterpolation() == UsdGeom.Tokens.faceVarying
    normals, counts = mesh.GetNormalsAttr().Get(), mesh.GetFaceVertexCountsAttr().Get()
    assert len(normals) == sum(counts) and all(tuple(v) == (0, 0, 1) for v in normals[:counts[0]])
    assert all(0 < v[2] < 1 for v in normals[2 * counts[0]:])  # the bevel leans outward
    col = stage.GetPrimAtPath("/World/Objects/rug/Asset/Collider")
    assert col.HasAPI(UsdPhysics.CollisionAPI) and not col.HasAPI(UsdPhysics.RigidBodyAPI)
    assert not prim.HasAPI(UsdPhysics.RigidBodyAPI) and not stage.GetPrimAtPath("/World/Objects/rug/Asset").HasAPI(
        UsdPhysics.RigidBodyAPI)
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"]).ComputeWorldBound(prim).ComputeAlignedRange()
    lo, hi = box.GetMin(), box.GetMax()
    assert hi[0] - lo[0] == pytest.approx(2.4, abs=1e-3) and hi[1] - lo[1] == pytest.approx(1.6, abs=1e-3)
    assert lo[2] == pytest.approx(0.0, abs=1e-6) and hi[2] == pytest.approx(0.012, abs=1e-4)  # on the floor, not sunk
    # the CC0 texture: copied into the scene, referenced relative to the rug's file
    tex = [UsdShade.Shader(q).GetInput("file").Get() for q in Usd.PrimRange(stage.GetPrimAtPath("/World/Objects/rug"))
           if q.IsA(UsdShade.Shader) and UsdShade.Shader(q).GetInput("file")]
    assert tex and all(not t.path.startswith("/") for t in tex)
    assert any(t.resolvedPath.endswith("_color.jpg") for t in tex)
    assert (tmp_path / "s" / "textures").is_dir()
    from agentic_sim_world_gen.license import report

    rep = report(service.load(tmp_path / "s"))
    assert not rep["warnings"] and mat.split("/")[1] in json.dumps(rep, ensure_ascii=False)


def test_compose_rug_offline_is_a_plain_color(tmp_path, monkeypatch):
    """The material cannot be fetched (offline, empty cache): a plain slab, still composed."""
    pytest.importorskip("pxr")
    from pxr import Usd

    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_PBR", "1")
    monkeypatch.setattr(pbr, "_ambientcg_maps", lambda *a: (_ for _ in ()).throw(pbr.FetchError("offline")))
    monkeypatch.setattr(pbr, "_polyhaven_maps", lambda *a: (_ for _ in ()).throw(pbr.FetchError("offline")))
    make(tmp_path, [{"id": "rug", "query": "carpet", "position": [3.0, 2.5]}])
    out = service.compose_scene(tmp_path / "s")
    stage = Usd.Stage.Open(out["usd"])
    assert stage.GetPrimAtPath("/World/Objects/rug/Asset/Slab").IsValid()
    assert stage.GetPrimAtPath("/World/Objects/rug/Asset").GetCustomDataByKey("agentic_sim_world_gen:generic")["textured"] is False


def test_layout_svg_draws_the_rug_under_the_table(tmp_path):
    living(tmp_path)
    svg = (tmp_path / "s" / "layout.svg").read_text(encoding="utf-8")
    assert 'class="wg-rug"' in svg and "#7a2e2a" in svg
    assert svg.index('class="wg-rug"') < svg.index(">table<")  # under the table
