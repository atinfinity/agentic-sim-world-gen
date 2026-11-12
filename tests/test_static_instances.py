"""Hung objects are static even when their bodies are on parts or inside an instance: offline."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pxr")
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics  # noqa: E402

from agentic_sim_world_gen import service  # noqa: E402
from agentic_sim_world_gen.assets.providers.local_folder import build_index  # noqa: E402


def make_tv(folder: Path) -> Path:
    """A wall TV laid out like SimReady: the root has no body; the panel's body is the instanceable part prim itself
    (Geometry/Panel), and a flap's body sits inside another instance (Geometry/Door/Flap, an instance proxy)."""
    folder.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(folder / "tv.usda"))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    for name, at in (("PanelProto", (0, 0, 0.35)), ("DoorProto", (0, 0.4, 0.1))):
        UsdGeom.Xform.Define(stage, f"/{name}")
        stage.GetPrimAtPath(f"/{name}").SetSpecifier(Sdf.SpecifierClass)
        part = f"/{name}" if name == "PanelProto" else f"/{name}/Flap"
        body = UsdGeom.Xform.Define(stage, part).GetPrim()
        if name == "DoorProto":
            UsdPhysics.RigidBodyAPI.Apply(body)
        c = UsdGeom.Cube.Define(stage, f"{part}/Box")
        c.CreateSizeAttr(1.0)
        xf = UsdGeom.Xformable(c)
        xf.AddTranslateOp().Set(Gf.Vec3d(*at))
        xf.AddScaleOp().Set(Gf.Vec3f(0.05, 1.0, 0.6))
        UsdPhysics.CollisionAPI.Apply(c.GetPrim())
    root = UsdGeom.Xform.Define(stage, "/Model").GetPrim()
    stage.SetDefaultPrim(root)
    for name in ("Panel", "Door"):
        geo = UsdGeom.Xform.Define(stage, f"/Model/Geometry/{name}").GetPrim()
        geo.GetReferences().AddInternalReference(f"/{name}Proto")
        geo.SetInstanceable(True)
    UsdPhysics.RigidBodyAPI.Apply(stage.GetPrimAtPath("/Model/Geometry/Panel"))
    stage.GetRootLayer().Save()
    return folder / "tv.usda"


def test_bodies_in_instances_are_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_CACHE", str(tmp_path / "cache"))
    make_tv(tmp_path / "lib" / "tv")
    recs = build_index(tmp_path / "lib", tmp_path / "cache")
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    try:
        d = tmp_path / "s"
        service.create_scene({"name": "s", "room": {"width": 5, "depth": 4, "height": 2.6}, "objects": [
            {"id": "wall_tv", "query": "tv", "layout": {"against_wall": "north", "wall_height": 1.2}},
            {"id": "floor_tv", "query": "tv", "position": [1.0, 1.0]},
        ]}, d)
        service.compose_scene(d)
    finally:
        service.set_provider(None)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    hung = "/World/Objects/wall_tv"
    assert stage.GetPrimAtPath(hung).GetCustomDataByKey("agentic_sim_world_gen:static_bodies") == 2
    assert stage.GetPrimAtPath(hung).GetCustomDataByKey("agentic_sim_world_gen:uninstanced") == 1  # the door, not the panel
    assert stage.GetPrimAtPath(f"{hung}/Asset/Geometry/Panel").IsInstance()
    for part in ("Panel", "Door/Flap"):
        p = stage.GetPrimAtPath(f"{hung}/Asset/Geometry/{part}")
        assert not p.IsInstanceProxy()
        assert UsdPhysics.RigidBodyAPI(p).GetRigidBodyEnabledAttr().Get() is False, part
    # the object on the floor keeps its instances and its bodies
    floor = "/World/Objects/floor_tv/Asset/Geometry"
    assert stage.GetPrimAtPath(f"{floor}/Door").IsInstance()
    assert UsdPhysics.RigidBodyAPI(stage.GetPrimAtPath(f"{floor}/Panel")).GetRigidBodyEnabledAttr().Get() is not False
    assert not stage.GetPrimAtPath("/World/Objects/floor_tv").GetCustomDataByKey("agentic_sim_world_gen:uninstanced")
    # the asset file itself is unchanged
    src = Usd.Stage.Open(str(tmp_path / "lib" / "tv" / "tv.usda"))
    assert src.GetPrimAtPath("/Model/Geometry/Door").IsInstanceable()


def test_disable_bodies_without_the_asset(tmp_path):
    """The scene cannot open the asset (an http URL under usd-core): the overs are still written by path."""
    from agentic_sim_world_gen.usd import reset_parts

    stage = Usd.Stage.CreateNew(str(tmp_path / "s.usda"))
    ref = UsdGeom.Xform.Define(stage, "/World/Objects/tv/Asset").GetPrim()
    ref.GetReferences().AddReference("https://example.invalid/tv.usd")
    n = reset_parts.disable_bodies(stage, "/World/Objects/tv/Asset", [
        {"path": "Geometry/Panel", "instance": None}, {"path": "Geometry/Door/Flap", "instance": "Geometry/Door"}])
    assert n == 1
    layer = stage.GetRootLayer()
    assert layer.GetPrimAtPath("/World/Objects/tv/Asset/Geometry/Door").instanceable is False
    attr = layer.GetAttributeAtPath("/World/Objects/tv/Asset/Geometry/Panel.physics:rigidBodyEnabled")
    assert attr.default is False
