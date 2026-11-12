import json
from pathlib import Path

import pytest

from agentic_sim_world_gen.assets.localize import parse_hf_url
from agentic_sim_world_gen.assets.providers.simready_central import (
    SimReadyCentralProvider,
    hf_to_https,
    parse_response,
    thumbnail_url,
)
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.layout.validate import check, footprint, overlap_depth
from agentic_sim_world_gen.scene.spec import ObjectSpec, Placement, ResolvedScene, Room, SceneSpec, record_to_dict

FIX = Path(__file__).parent / "fixtures"


def load_records():
    return parse_response(json.loads((FIX / "search_bookshelf.json").read_text()))


def box(name, w, d, h, up="Z", mpu=1.0):
    return AssetRecord(
        id=name, source="test", uri=f"file:///{name}.usd", name=name,
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis=up, meters_per_unit=mpu,
    )


def test_parse_simready_hits():
    recs = load_records()
    assert len(recs) == 4
    r = recs[0]
    assert r.uri.startswith("https://huggingface.co/datasets/")
    assert r.thumbnail_url.endswith(".usd.png") and "/.thumbs/256x256/" in r.thumbnail_url
    assert r.license and r.semantic_class
    assert r.variant_selections == {"PhysX": "Enabled"}
    w, d, h = r.size_m
    assert 0.3 < w < 0.45 and 0.8 < h < 0.95  # sm_cabinet_file_d02: 0.37 x 0.53 x 0.88


def test_url_helpers():
    assert hf_to_https("hf://huggingface.co/datasets/a/b/resolve/x/y.usd") == "https://huggingface.co/datasets/a/b/resolve/x/y.usd"
    assert thumbnail_url("https://h/a/b.usd") == "https://h/a/.thumbs/256x256/b.usd.png"
    url = "https://huggingface.co/datasets/nvidia/simready-assets/resolve/" + "a" * 40 + "/chair_a01/simready_usd/sm.usd"
    assert parse_hf_url(url) == ("nvidia/simready-assets", "a" * 40, "chair_a01/simready_usd/sm.usd")


def test_y_up_and_units():
    r = AssetRecord(id="y", source="t", uri="u", name="y", bbox_min=(-50, 0, -20), bbox_max=(50, 80, 20),
                    up_axis="Y", meters_per_unit=0.01)
    assert r.size_m == pytest.approx((1.0, 0.4, 0.8))
    assert r.pivot == (0.0, 0, 0.0)


def test_size_filter_in_request():
    from agentic_sim_world_gen.assets.providers.base import SizeFilter

    body = SimReadyCentralProvider().build_request("desk", size=SizeFilter(max_z=1.0))
    assert body["max_bbox_z"] == 1.0 and "min_bbox_x" not in body


def test_overlap():
    a = footprint((0, 0), (1, 1), 0)
    b = footprint((0.9, 0), (1, 1), 0)
    c = footprint((2, 0), (1, 1), 45)
    assert overlap_depth(a, b) == pytest.approx(0.1)
    assert overlap_depth(a, c) == 0.0


def make_scene(positions):
    objs = [ObjectSpec(id=k, query=k, position=v) for k, v in positions.items()]
    spec = SceneSpec(name="t", room=Room(width=3, depth=2, height=2.4), objects=objs)
    pl = [Placement(object_id=k, candidates=[record_to_dict(box(k, 0.8, 0.6, 1.0))]) for k in positions]
    return ResolvedScene(spec=spec, placements=pl)


def test_check_detects_problems():
    kinds = {i.kind for i in check(make_scene({"a": (0.5, 0.5), "b": (0.9, 0.5), "c": (2.9, 1.0)}))}
    assert kinds == {"overlap", "out_of_room"}
    assert check(make_scene({"a": (0.5, 0.5), "b": (2.0, 1.0)})) == []


def test_resolved_roundtrip(tmp_path):
    s = make_scene({"a": (0.5, 0.5)})
    s.save(tmp_path / "r.json")
    s2 = ResolvedScene.load(tmp_path / "r.json")
    assert s2.placements[0].asset.size_m == pytest.approx((0.8, 0.6, 1.0))


def test_previews(tmp_path):
    from agentic_sim_world_gen.preview.candidates import render_html
    from agentic_sim_world_gen.preview.layout2d import render_svg

    s = make_scene({"a": (0.5, 0.5), "b": (0.9, 0.5)})
    svg = render_svg(s)
    assert svg.startswith("<svg") and "問題 1 件" in svg
    html = render_html(s)
    assert "overlap" in html and "card sel" in html


def test_compose(tmp_path):
    pxr = pytest.importorskip("pxr")
    from pxr import Gf, Usd, UsdGeom

    from agentic_sim_world_gen.usd.compose import compose

    s = make_scene({"a": (0.5, 0.5)})
    s.spec.objects[0].yaw_deg = 90
    rec = s.placements[0].candidates[0]
    rec["variant_selections"] = {"PhysX": "Enabled"}
    s.spec.objects[0].variants = {"Lighting": "Off"}  # the object's choice is added to the asset's
    rec["bbox_min"], rec["bbox_max"] = [-0.4, -0.3, 0.1], [0.4, 0.3, 1.1]  # pivot z = 0.1
    stage = compose(s, tmp_path / "s.usda")
    prim = stage.GetPrimAtPath("/World/Objects/a")
    assert stage.GetPrimAtPath("/World/Objects/a/Asset").GetVariantSets().GetAllVariantSelections() == {"PhysX": "Enabled", "Lighting": "Off"}
    m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    # pivot (0,0,0.1) must land on (0.5, 0.5, 0)
    assert m.Transform(Gf.Vec3d(0, 0, 0.1)) == pytest.approx(Gf.Vec3d(0.5, 0.5, 0))
    # local +X rotates to world +Y with yaw 90
    p = m.Transform(Gf.Vec3d(1, 0, 0.1))
    assert (p[0], p[1]) == pytest.approx((0.5, 1.5))
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    # Real-Time needs more transmission bounces to show liquid inside bottles; kept after saving
    saved = Usd.Stage.Open(str(tmp_path / "s.usda")).GetRootLayer().customLayerData
    assert saved["renderSettings"]["rtx:rtpt:maxSpecularAndTransmissionBounces"] == 6
    s.spec.render.transmission_bounces = 0  # 0 leaves the renderer default
    assert "renderSettings" not in compose(s, tmp_path / "s0.usda").GetRootLayer().customLayerData


@pytest.mark.network
def test_live_search():
    recs = SimReadyCentralProvider().search("office chair", limit=3)
    assert recs and recs[0].size_m[2] > 0.5


def test_fits_ignores_orientation():
    from agentic_sim_world_gen.assets.providers.base import fits, size_filter_for

    assert fits((0.8, 1.8, 0.76), (1.0, 0.5, 0.65), (1.9, 1.0, 0.85))
    assert not fits((0.21, 0.3, 0.06), (1.0, 0.5, 0.65), None)
    f = size_filter_for((1.0, 0.5, 0.65), (1.9, 1.0, 0.85))
    assert (f.max_x, f.max_y, f.max_z, f.min_z, f.min_x) == (1.9, 1.9, 0.85, 0.65, None)


def test_compose_keeps_asset_root_xform(tmp_path):
    """An asset whose root prim scales cm geometry by 0.01 must stay 1 m (Isaac Props/Mugs case)."""
    from pxr import Usd, UsdGeom

    from agentic_sim_world_gen.usd.compose import compose

    asset = tmp_path / "cm_asset.usda"
    st = Usd.Stage.CreateNew(str(asset))
    root = UsdGeom.Xform.Define(st, "/Root")
    UsdGeom.Xformable(root).AddScaleOp().Set((0.01, 0.01, 0.01))
    UsdGeom.Cube.Define(st, "/Root/Box").CreateSizeAttr(100.0)  # 100 cm cube, centered
    st.SetDefaultPrim(root.GetPrim())
    st.GetRootLayer().Save()

    s = make_scene({"a": (2.0, 2.0)})
    rec = s.placements[0].candidates[0]
    rec["uri"] = str(asset)
    rec["bbox_min"], rec["bbox_max"] = [-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]  # as the index computes it
    rec["meters_per_unit"], rec["up_axis"] = 1.0, "Z"
    stage = compose(s, tmp_path / "s.usda")
    rng = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(
        stage.GetPrimAtPath("/World/Objects/a")).ComputeAlignedRange()
    assert list(rng.GetSize()) == pytest.approx([1.0, 1.0, 1.0])
    assert list(rng.GetMin()) == pytest.approx([1.5, 1.5, 0.0], abs=1e-6)


def test_compose_mutes_the_assets_own_physics_scene(tmp_path):
    """ForgeCAD assets bring Physics/PhysicsScene each; ~25 PhysX scenes stop Isaac Sim. One scene stays."""
    from pxr import Usd, UsdGeom, UsdPhysics

    from agentic_sim_world_gen.usd.compose import compose

    asset = tmp_path / "plate.usda"
    st = Usd.Stage.CreateNew(str(asset))
    root = UsdGeom.Xform.Define(st, "/Root")
    UsdGeom.Cube.Define(st, "/Root/Box")
    UsdPhysics.Scene.Define(st, "/Root/Physics/PhysicsScene")
    st.SetDefaultPrim(root.GetPrim())
    st.GetRootLayer().Save()

    s = make_scene({"a": (0.6, 0.6), "b": (2.2, 1.2)})
    s.placements[0].candidates[0]["uri"] = str(asset)
    stage = compose(s, tmp_path / "s.usda")
    scenes = [str(p.GetPath()) for p in stage.Traverse() if p.IsA(UsdPhysics.Scene)]
    assert scenes == ["/World/PhysicsScene"]
    ps = stage.GetPrimAtPath("/World/PhysicsScene")  # GPU buffers big enough for a full restaurant
    assert "PhysxSceneAPI" in ps.GetMetadata("apiSchemas").GetAddedOrExplicitItems()  # unknown to usd-core
    assert ps.GetAttribute("physxScene:gpuCollisionStackSize").Get() >= 128 * 2**20
    assert stage.GetPrimAtPath("/World/Objects/a/Asset/Box")  # the rest of the asset is untouched
    assert not stage.GetPrimAtPath("/World/Objects/b/Asset/Physics").IsDefined()  # no scene there: nothing made


def test_isaac_rebase_uri(monkeypatch):
    from dataclasses import replace

    from agentic_sim_world_gen.assets.providers.isaac_index import rebase_uri

    rec = replace(box("klt", 0.3, 0.2, 0.15), id="isaac:/Isaac/Props/KLT_Bin/small_KLT.usd", source="isaac-6.1")
    monkeypatch.delenv("ISAAC_ASSETS_ROOT", raising=False)
    assert rebase_uri(rec) is None
    monkeypatch.setenv("ISAAC_ASSETS_ROOT", "/data/isaac/")
    assert rebase_uri(rec) == "/data/isaac/Isaac/Props/KLT_Bin/small_KLT.usd"
    assert rebase_uri(box("x", 1, 1, 1)) is None  # not an Isaac record


def test_box_collider_for_assets_without_physics(tmp_path):
    from pxr import Usd, UsdGeom, UsdPhysics

    from agentic_sim_world_gen.usd.compose import compose

    rack = box("rack", 0.6, 1.2, 2.2)
    rack.source, rack.rigid_body_count = "simready-central", 0  # Vertiv: no PhysX variant, no rigid body
    chair = box("chair", 0.6, 0.6, 0.9)
    chair.source, chair.rigid_body_count, chair.variant_selections = "simready-central", 1, {"PhysX": "Enabled"}
    lamp = box("lamp", 0.3, 0.3, 0.5)  # unknown physics
    assert (rack.has_physics, chair.has_physics, lamp.has_physics) == (False, True, None)
    assert rack.summary()["physics"] is False and "physics" not in chair.summary()

    objs = [ObjectSpec(id="rack", query="rack", position=(1, 1)), ObjectSpec(id="chair", query="chair", position=(2, 1)),
            ObjectSpec(id="lamp", query="lamp", position=(2.5, 1.5), collider="box"),
            ObjectSpec(id="rack2", query="rack", position=(0.5, 1.5), collider="none")]
    recs = {"rack": rack, "chair": chair, "lamp": lamp, "rack2": rack}
    spec = SceneSpec(name="t", room=Room(width=3, depth=2, height=2.4), objects=objs)
    s = ResolvedScene(spec=spec, placements=[Placement(object_id=k, candidates=[record_to_dict(r)]) for k, r in recs.items()])
    stage = compose(s, tmp_path / "s.usda")
    has = {k: stage.GetPrimAtPath(f"/World/Objects/{k}/Collider").IsValid() for k in recs}
    assert has == {"rack": True, "chair": False, "lamp": True, "rack2": False}
    col = stage.GetPrimAtPath("/World/Objects/rack/Collider")
    assert col.HasAPI(UsdPhysics.CollisionAPI) and not col.HasAPI(UsdPhysics.RigidBodyAPI)
    assert UsdGeom.Imageable(col).GetPurposeAttr().Get() == UsdGeom.Tokens.guide
    # world box = the asset's footprint at the placement (bottom on the floor)
    rng = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.guide]).ComputeWorldBound(col).ComputeAlignedRange()
    assert list(rng.GetMin()) == pytest.approx([0.7, 0.4, 0.0], abs=1e-6)
    assert list(rng.GetMax()) == pytest.approx([1.3, 1.6, 2.2], abs=1e-6)
