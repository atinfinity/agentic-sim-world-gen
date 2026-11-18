"""A fixture's lit face glows: a thin emissive Lens over each fixture's light, part of its body. Offline."""

import pytest

pxr = pytest.importorskip("pxr")
from pxr import Gf, Usd, UsdGeom, UsdShade  # noqa: E402

from agentic_sim_world_gen.scene.fixtures import LENS, LENS_GLOW, LENS_OUT, LENS_T, kelvin_tint, plan  # noqa: E402
from agentic_sim_world_gen.scene.spec import Room  # noqa: E402
from agentic_sim_world_gen.usd.look import build_fixtures  # noqa: E402


def room(**kw):
    return Room.model_validate({"width": 6, "depth": 4, "height": 2.7, "ceiling": True, "lighting": "office", **kw})


def _emissive(prim):
    mat = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
    ps = UsdShade.Shader(mat.GetPrim().GetChild("PreviewSurface"))
    return mat, Gf.Vec3f(ps.GetInput("emissiveColor").Get())


@pytest.mark.parametrize("kind", ["panel", "line", "downlight", "pendant"])
def test_every_fixture_has_a_glowing_lens_over_its_light(kind):
    r = room(fixtures=kind)
    fx = plan(r)
    st = Usd.Stage.CreateInMemory()
    build_fixtures(st, r)
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    for i, f in enumerate(fx):
        lens = st.GetPrimAtPath(f"/World/Room/Fixtures/Fixture_{i:02d}/Lens")
        assert lens.IsValid(), (kind, i)
        mat, em = _emissive(lens)
        tint = kelvin_tint(f["light"]["kelvin"])
        assert tuple(em) == pytest.approx(tuple(LENS_GLOW * t for t in tint), rel=1e-4)
        assert mat.GetPrim().GetCustomDataByKey("agentic_sim_world_gen:glow") == LENS_GLOW
        # just above the light (which emits toward -Z), so the camera sees it where the light is: a flush or hung
        # body's lens sticks out LENS_OUT below its bottom (LENS above the light), a pendant's sits in its shade
        box = cache.ComputeWorldBound(lens).ComputeAlignedRange()
        lz = f["light"]["z"]
        bottom = lz + LENS - (LENS_OUT if kind != "pendant" else LENS_T)
        assert box.GetMin()[2] == pytest.approx(bottom, abs=1e-6)
        assert box.GetMax()[2] == pytest.approx(bottom + LENS_T, abs=1e-6)
        assert lz < box.GetMin()[2]
        assert box.GetMin()[0] < f["x"] < box.GetMax()[0] and box.GetMin()[1] < f["y"] < box.GetMax()[1]
        if kind != "pendant":  # lowest (the obstacle) stays the body's bottom
            assert f["lowest"] == pytest.approx(lz + LENS, abs=1e-4)
    # the body parts keep the body's paint (not glowing)
    body = st.GetPrimAtPath("/World/Room/Fixtures/Fixture_00/Part_0")
    mat = UsdShade.MaterialBindingAPI(body).ComputeBoundMaterial()[0]
    assert mat.GetPrim().GetCustomDataByKey("agentic_sim_world_gen:glow") is None


def test_lens_matches_the_rect_light_and_its_yaw():
    r = room(width=4, depth=8, fixtures="line")  # the long side along y turns the lines
    fx = plan(r)
    st = Usd.Stage.CreateInMemory()
    build_fixtures(st, r)
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    f = fx[0]
    w, h = f["light"]["width"], f["light"]["height"]
    size = cache.ComputeWorldBound(st.GetPrimAtPath("/World/Room/Fixtures/Fixture_00/Lens")).ComputeAlignedRange().GetSize()
    want = (w, h) if f["light"]["yaw"] % 180 == 0 else (h, w)
    assert (size[0], size[1]) == pytest.approx(want, abs=1e-6)


def test_one_lens_material_tinted_and_scaled_with_the_room():
    st = Usd.Stage.CreateInMemory()
    build_fixtures(st, room(fixtures="panel", light_kelvin=3000, lighting_scale=0.5))
    looks = [p.GetName() for p in st.GetPrimAtPath("/World/Room/Fixtures/Looks").GetChildren()]
    assert looks.count("Lens") == 1
    mat, em = _emissive(st.GetPrimAtPath("/World/Room/Fixtures/Fixture_00/Lens"))
    assert em[0] > em[2]  # warm
    assert mat.GetPrim().GetCustomDataByKey("agentic_sim_world_gen:glow") == pytest.approx(LENS_GLOW * 0.5)
