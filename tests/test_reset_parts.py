"""Parts with !resetXformStack! follow the placed asset: offline, with a tiny oven made with usd-core."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pxr")
from pxr import Gf, Usd, UsdGeom, UsdPhysics  # noqa: E402

from agentic_sim_world_gen import service  # noqa: E402
from agentic_sim_world_gen.assets.providers.local_folder import build_index  # noqa: E402


def _cube(stage, path, size, at):
    c = UsdGeom.Cube.Define(stage, path)
    c.CreateSizeAttr(1.0)
    xf = UsdGeom.Xformable(c)
    xf.AddTranslateOp().Set(Gf.Vec3d(*at))
    xf.AddScaleOp().Set(Gf.Vec3f(*size))
    return c


def make_oven(folder: Path) -> Path:
    """A 0.6 m oven body (rigid body) with a knob (rigid body nested in it) authored like ForgeCAD's assets: reset
    transform stack and the knob's world matrix in the asset's stage."""
    folder.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(folder / "oven.usda"))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Model").GetPrim()
    stage.SetDefaultPrim(root)
    body = UsdGeom.Xform.Define(stage, "/Model/Geometry/Body").GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(body)
    _cube(stage, "/Model/Geometry/Body/Box", (0.6, 0.6, 0.9), (0, 0, 0.45))
    knob = UsdGeom.Xform.Define(stage, "/Model/Geometry/Body/Knob")
    knob.SetResetXformStack(True)
    knob.AddTransformOp(opSuffix="simulationWorld").Set(Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(0.1, -0.3, 0.8)))
    UsdPhysics.RigidBodyAPI.Apply(knob.GetPrim())
    _cube(stage, "/Model/Geometry/Body/Knob/Cap", (0.04, 0.02, 0.04), (0, 0, 0))
    stage.GetRootLayer().Save()
    return folder / "oven.usda"


def test_reset_part_moves_with_the_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_CACHE", str(tmp_path / "cache"))
    make_oven(tmp_path / "lib" / "oven")
    recs = build_index(tmp_path / "lib", tmp_path / "cache")
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    try:
        d = tmp_path / "s"
        service.create_scene({"name": "s", "room": {"width": 5, "depth": 4, "height": 2.6}, "objects": [
            {"id": "oven", "query": "oven", "position": [3.0, 2.0], "yaw_deg": 90},
            {"id": "ovens", "query": "oven", "position": [1.0, 1.0], "array": {"count": 2, "spacing": 0.1}},
        ]}, d)
        service.compose_scene(d)
    finally:
        service.set_provider(None)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    xc = UsdGeom.XformCache()

    def world(p):
        return xc.GetLocalToWorldTransform(stage.GetPrimAtPath(p))

    for unit in ("/World/Objects/oven", "/World/Objects/ovens/ovens_r0c0", "/World/Objects/ovens/ovens_r0c1"):
        body = world(f"{unit}/Asset/Geometry/Body")
        knob = world(f"{unit}/Asset/Geometry/Body/Knob")
        # where the knob is relative to the body is the same as in the asset itself
        rel = knob * body.GetInverse()
        assert rel.ExtractTranslation() == pytest.approx(Gf.Vec3d(0.1, -0.3, 0.8), abs=1e-6), unit
        assert UsdGeom.Xformable(stage.GetPrimAtPath(f"{unit}/Asset/Geometry/Body/Knob")).GetResetXformStack()
        assert stage.GetPrimAtPath(unit).GetCustomDataByKey("agentic_sim_world_gen:reset_parts") == 1
    # the oven at (3, 2) turned 90°: the knob 0.3 m in front (-y in the asset) is at +x of the body
    t = world("/World/Objects/oven/Asset/Geometry/Body/Knob").ExtractTranslation()
    b = world("/World/Objects/oven/Asset/Geometry/Body").ExtractTranslation()
    assert (t - b) == pytest.approx(Gf.Vec3d(0.3, 0.1, 0.8), abs=1e-6)
    assert b[0] == pytest.approx(3.0, abs=0.05) and b[1] == pytest.approx(2.0, abs=0.05)


def test_hung_objects_are_static(tmp_path, monkeypatch):
    """A hood hung from the ceiling or a unit hung on the wall has nothing holding it up in physics: its bodies are
    disabled (static colliders) like display stock."""
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_CACHE", str(tmp_path / "cache"))
    make_oven(tmp_path / "lib" / "oven")
    recs = build_index(tmp_path / "lib", tmp_path / "cache")
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    try:
        d = tmp_path / "s"
        service.create_scene({"name": "s", "room": {"width": 5, "depth": 4, "height": 2.6}, "objects": [
            {"id": "hood", "query": "oven", "position": [3.0, 2.0], "layout": {"ceiling": True}},
            {"id": "wall", "query": "oven", "layout": {"against_wall": "north", "wall_height": 1.0},
             "array": {"count": 2, "spacing": 0.1}},
            {"id": "floor", "query": "oven", "position": [1.0, 1.0]},
        ]}, d)
        service.compose_scene(d)
    finally:
        service.set_provider(None)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    for unit in ("/World/Objects/hood", "/World/Objects/wall/wall_r0c0", "/World/Objects/wall/wall_r0c1"):
        assert stage.GetPrimAtPath(unit).GetCustomDataByKey("agentic_sim_world_gen:hung") is True, unit
        assert UsdPhysics.RigidBodyAPI(stage.GetPrimAtPath(f"{unit}/Asset/Geometry/Body")).GetRigidBodyEnabledAttr().Get() is False
    assert not stage.GetPrimAtPath("/World/Objects/floor").GetCustomDataByKey("agentic_sim_world_gen:hung")
    assert UsdPhysics.RigidBodyAPI(stage.GetPrimAtPath("/World/Objects/floor/Asset/Geometry/Body")).GetRigidBodyEnabledAttr().Get() is not False


def make_rug(folder: Path) -> Path:
    """A 1 cm rug that is a rigid body, like SimReady's area rug."""
    folder.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(folder / "rug.usda"))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Model").GetPrim()
    stage.SetDefaultPrim(root)
    body = UsdGeom.Xform.Define(stage, "/Model/Geometry/Body").GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(body)
    _cube(stage, "/Model/Geometry/Body/Mat", (1.6, 1.2, 0.01), (0, 0, 0.005))
    stage.GetRootLayer().Save()
    return folder / "rug.usda"


def test_rug_on_the_floor_is_static_and_sunk(tmp_path, monkeypatch):
    """A table standing on a rigid 1 cm rug was pushed up by it when physics started and what was on the table slid
    off: a thin floor cover is a static collider sunk so only 1 mm stays above the floor."""
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_CACHE", str(tmp_path / "cache"))
    make_rug(tmp_path / "lib" / "rug")
    make_oven(tmp_path / "lib" / "oven")
    recs = build_index(tmp_path / "lib", tmp_path / "cache")
    by = {r.name.split(".")[0]: r for r in recs}

    class P:
        name = "f"

        def search(self, q, limit=10, size=None):
            return [by["rug"]] if "rug" in q else [by["oven"]]

    service.set_provider(P())
    try:
        d = tmp_path / "s"
        service.create_scene({"name": "s", "room": {"width": 5, "depth": 4, "height": 2.6}, "objects": [
            {"id": "rug", "query": "rug", "position": [2.0, 2.0]},
            {"id": "oven", "query": "oven", "position": [4.0, 1.0]},
        ]}, d)
        service.compose_scene(d)
    finally:
        service.set_provider(None)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    rug = stage.GetPrimAtPath("/World/Objects/rug")
    assert rug.GetCustomDataByKey("agentic_sim_world_gen:floor_cover") is True
    assert UsdPhysics.RigidBodyAPI(stage.GetPrimAtPath("/World/Objects/rug/Asset/Geometry/Body")).GetRigidBodyEnabledAttr().Get() is False
    top = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(rug).ComputeAlignedRange().GetMax()[2]
    assert top == pytest.approx(0.001, abs=1e-4)
    assert not stage.GetPrimAtPath("/World/Objects/oven").GetCustomDataByKey("agentic_sim_world_gen:floor_cover")
