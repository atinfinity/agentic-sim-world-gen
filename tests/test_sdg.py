"""Synthetic data plan (agentic_sim_world_gen.sdg), semantic labels in the USD, generate_data without Isaac Sim."""

import asyncio
import json
import math

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.layout.room import point_in_polygon
from agentic_sim_world_gen.sdg import boxes, plan
from agentic_sim_world_gen.scene.plan import Opening
from agentic_sim_world_gen.scene.spec import PurposeSpec, SdgSpec


def rec(name, cls, w, d, h):
    return AssetRecord(
        id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
        semantic_class=cls, license="CC-BY 4.0",
    )


CATALOG = {
    "sofa": [rec("sofa", "couch", 1.8, 0.9, 0.8)],
    "coffee table": [rec("table", "coffee_table", 1.0, 0.5, 0.4)],
    "bookshelf": [rec("shelf", None, 0.8, 0.35, 1.8)],
    "rack": [rec("rack", "Shelf", 2.0, 1.0, 2.5)],
}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


LIVING = {
    "name": "living",
    "room": {"width": 5.0, "depth": 4.0, "height": 2.5},
    "objects": [
        {"id": "sofa", "query": "sofa", "position": [2.5, 0.6]},
        {"id": "table", "query": "coffee table", "position": [2.5, 1.8]},
        {"id": "shelf", "query": "bookshelf", "position": [4.6, 3.0], "yaw_deg": 90, "semantic_class": "Book Case"},
    ],
}


def make(tmp_path, spec=LIVING):
    service.create_scene(spec, tmp_path / spec["name"])
    return tmp_path / spec["name"], service.load(tmp_path / spec["name"])


def test_classes_and_paths(tmp_path):
    _, s = make(tmp_path)
    cls = {b.id: b.cls for b in boxes(s)}
    # object override > asset class > query; lowercased, "_" -> " "
    assert cls == {"sofa": "couch", "table": "coffee table", "shelf": "book case"}
    assert {b.id: b.cls for b in boxes(s, {"sofa": "sofa"})}["sofa"] == "sofa"
    assert plan(s)["classes"]["/World/Objects/sofa"] == "couch"


def test_plan_is_deterministic(tmp_path):
    _, s = make(tmp_path)
    a, b = plan(s, SdgSpec(frames=6, seed=3)), plan(s, SdgSpec(frames=6, seed=3))
    assert a == b and len(a["frames"]) == 6
    assert plan(s, SdgSpec(frames=6, seed=4))["frames"] != a["frames"]
    assert a["annotators"][0] == "rgb" and a["writers"][0] == "basic"


def test_cameras_stand_in_free_space(tmp_path):
    _, s = make(tmp_path)
    sdg = SdgSpec(frames=30, seed=1, camera={"clearance": 0.3, "height": [0.8, 1.6], "hfov_deg": [70, 70]})
    pl = plan(s, sdg)
    assert pl["warnings"] == []
    bx = boxes(s)
    for f in pl["frames"]:
        x, y, z = f["camera"]["position"]
        assert 0.3 <= x <= 4.7 and 0.3 <= y <= 3.7 and 0.8 <= z <= 1.6
        assert f["camera"]["hfov_deg"] == 70
        for b in bx:  # never inside an object's footprint below the camera
            assert not (b.z[0] <= z and point_in_polygon((x, y), b.poly))
        assert f["camera"]["target"] in {"sofa", "table", "shelf"}
        assert math.dist(f["camera"]["position"], f["camera"]["look_at"]) > 0.5
        assert "light_scale" in f and "color_temp_k" in f and "floor_color" not in f


def test_targets_randomize_and_warnings(tmp_path):
    _, s = make(tmp_path)
    sdg = SdgSpec(frames=8, camera={"targets": ["sofa", "nope"]}, classes={"ghost": "x"},
                  randomize={"light_scale": None, "color_temp_k": None, "room_colors": True})
    pl = plan(s, sdg)
    assert {f["camera"]["target"] for f in pl["frames"]} == {"sofa"}
    assert any("nope" in w for w in pl["warnings"]) and any("ghost" in w for w in pl["warnings"])
    f = pl["frames"][0]
    assert "light_scale" not in f and len(f["floor_color"]) == 3 and len(f["wall_color"]) == 3
    assert all(0.0 <= v <= 1.0 for v in f["floor_color"] + f["wall_color"])


def test_array_units_get_own_paths(tmp_path):
    spec = {"name": "wh", "room": {"width": 12.0, "depth": 10.0, "height": 4.0},
            "objects": [{"id": "racks", "query": "rack", "position": [6.0, 5.0],
                         "array": {"count": 3, "rows": 2, "aisle": 3.0}}]}
    _, s = make(tmp_path, spec)
    pl = plan(s, SdgSpec(frames=4))
    assert "/World/Objects/racks/racks_r1c2" in pl["classes"]
    assert set(pl["classes"].values()) == {"shelf"} and len(pl["classes"]) == 6


def test_compose_writes_labels(tmp_path):
    from pxr import Usd, UsdSemantics

    d, _ = make(tmp_path)
    usd = service.compose_scene(d)["usd"]
    st = Usd.Stage.Open(usd)

    def cls(path):
        p = st.GetPrimAtPath(path)
        assert p, path
        return list(UsdSemantics.LabelsAPI(p, "class").GetLabelsAttr().Get())

    assert cls("/World/Objects/sofa") == ["couch"]
    assert cls("/World/Objects/shelf") == ["book case"]
    assert cls("/World/Room/Floor") == ["floor"]
    walls = [p for p in st.Traverse() if p.GetName().startswith("Wall_")]
    assert walls and all(cls(p.GetPath()) == ["wall"] for p in walls)


def test_generate_data_dry_run_and_no_isaac(tmp_path, monkeypatch):
    d, _ = make(tmp_path)
    r = service.generate_data(d, {"frames": 5, "seed": 2, "writers": ["coco"]}, dry_run=True)
    assert r["ok"] and r["frames"] == 5 and r["classes"] == ["book case", "coffee table", "couch"]
    pl = json.loads((d / "sdg" / "plan.json").read_text())
    assert pl["writers"] == ["basic", "coco"] and len(pl["frames"]) == 5
    assert service.load(d).spec.sdg.seed == 2  # settings saved with the scene
    assert service.status(d)["sdg"]["frames"] == 5

    monkeypatch.setenv("ISAACSIM_PYTHON", str(tmp_path / "missing"))
    r = service.generate_data(d)
    assert not r["ok"] and "Isaac Sim not found" in r["error"]


def test_mcp_generate_data(tmp_path):
    from agentic_sim_world_gen.mcp_server import mcp

    d, _ = make(tmp_path)

    async def run():
        tools = {t.name for t in await mcp.list_tools()}
        assert "generate_data" in tools
        return await mcp.call_tool("generate_data", {"scene_dir": str(d), "sdg": {"frames": 3}, "dry_run": True})

    res = asyncio.run(run())
    assert not getattr(res, "is_error", False), res
    assert '"frames": 3' in str(res)


def test_material_candidates_and_frames(tmp_path):
    from agentic_sim_world_gen.sdg import material_candidates

    _, s = make(tmp_path)
    s.spec.room.ceiling = True
    rz = {"light_scale": None, "color_temp_k": None, "room_colors": True, "materials": {
        "floor": ["wood_dark", "tatami", "wood_dark", "mdl:Base/Architecture/Ceiling_Tiles", "mdl:nope"],
        "walls": {"query": "white plaster", "count": 3},
        "ceiling": {"category": "Paint", "count": 2, "keep_current": False}}}
    sdg = SdgSpec(frames=12, seed=5, randomize=rz)
    mats, warns = material_candidates(s.spec.room, sdg)
    floor = mats["floor"]
    assert [c["name"] for c in floor] == ["wood_light", "wood_dark", "tatami", "Ceiling_Tiles"]  # duplicates dropped
    assert floor[0]["path"] is None and floor[1]["path"] == "/World/Room/Looks/SDG/Floor_00"
    assert floor[3]["license"] and floor[3]["mdl"].endswith("Ceiling_Tiles.mdl")
    assert any("nope" in w for w in warns)
    assert len(mats["walls"]) == 4 and all(c.get("mdl") for c in mats["walls"][1:])
    assert [c["source"] for c in mats["ceiling"]] == ["mdl", "mdl"]
    assert material_candidates(s.spec.room, sdg)[0] == mats  # deterministic (sampled from the seed)

    pl = plan(s, sdg)
    assert pl == plan(s, sdg)
    assert [c["name"] for c in pl["materials"]["floor"]] == [c["name"] for c in floor]
    assert "surface" not in pl["materials"]["floor"][0]
    seen = {i: set() for i in ("floor", "walls", "ceiling")}
    for f in pl["frames"]:
        assert "floor_color" not in f and "wall_color" not in f  # swapped surfaces keep the candidate's color
        for t, v in f["materials"].items():
            assert pl["materials"][t][v["index"]]["name"] == v["name"]
            seen[t].add(v["index"])
    assert seen == {"floor": set(range(4)), "walls": set(range(4)), "ceiling": {0, 1}}  # shuffle bag: all used
    # the camera poses do not change when materials are randomized
    plain = plan(s, SdgSpec(frames=12, seed=5, randomize={**rz, "materials": {}, "room_colors": False}))
    assert [f["camera"] for f in plain["frames"]] == [f["camera"] for f in pl["frames"]]


def test_material_shorthands_and_outdoor(tmp_path):
    from agentic_sim_world_gen.scene.spec import SdgMaterialChoice
    from agentic_sim_world_gen.sdg import material_candidates

    assert SdgMaterialChoice.model_validate("brick").surfaces == ["brick"]
    assert SdgMaterialChoice.model_validate("light oak planks").query == "light oak planks"
    for bad in (["oak"], [{"query": "oak"}]):  # not a preset / a search inside the list
        with pytest.raises(ValueError):
            SdgMaterialChoice.model_validate(bad)
    _, s = make(tmp_path)
    mats, warns = material_candidates(s.spec.room, SdgSpec(randomize={"materials": {
        "floor": {"tags": ["parquet"], "count": 3}, "ceiling": ["paint_white"], "walls": ["paint_white"]}}))
    assert len(mats["floor"]) == 4 and all(c["source"] == "mdl" for c in mats["floor"][1:])
    assert "ceiling" not in mats and any("no ceiling" in w for w in warns)
    assert "walls" not in mats and any("walls: fewer than 2" in w for w in warns)  # same as the current wall


def test_compose_builds_sdg_looks_and_dry_run(tmp_path):
    from pxr import Usd, UsdShade

    d, _ = make(tmp_path)
    r = service.generate_data(d, {"frames": 4, "randomize": {"materials": {
        "floor": ["wood_dark", "mdl:Base/Architecture/Ceiling_Tiles"], "walls": ["brick"]}}}, dry_run=True)
    assert r["materials"] == {"floor": ["wood_light", "wood_dark", "Ceiling_Tiles"], "walls": ["paint_white", "brick"]}
    assert all("materials" in f for f in r["sample_frames"])
    st = Usd.Stage.Open(service.compose_scene(d)["usd"])
    m = UsdShade.Material(st.GetPrimAtPath("/World/Room/Looks/SDG/Floor_01"))
    assert m and m.GetSurfaceOutput("mdl").HasConnectedSource()
    assert st.GetPrimAtPath("/World/Room/Looks/SDG/Wall_00")
    # the room keeps its own binding; the floor label is unchanged
    floor = st.GetPrimAtPath("/World/Room/Floor")
    assert UsdShade.MaterialBindingAPI(floor).GetDirectBindingRel().GetTargets()[0] == "/World/Room/Looks/Floor"
    lic = service.license_report(d)
    assert any(x["surface"] == "sdg floor" for x in lic["materials"])


def test_objects_outside_the_classes_stay_unlabelled(tmp_path):
    """An "other" class made up for what the data is not about outnumbered every real class."""
    _, s = make(tmp_path)
    paths = {b.id: b.path for b in boxes(s)}
    pl = plan(s, SdgSpec(frames=2, classes={"sofa": "sofa", "table": "other", "shelf": "other"}))
    assert any("outside the dataset's classes (other 2)" in w for w in pl["warnings"])
    pl = plan(s, SdgSpec(frames=4, classes={"sofa": "sofa", "table": ""}))
    assert pl["classes"][paths["table"]] == "" and pl["classes"][paths["shelf"]] == "book case"
    assert not any("outside" in w for w in pl["warnings"])  # renaming one object is not a scope
    pl = plan(s, SdgSpec(frames=4, classes={"sofa": "Sofa"}, others="background"))
    assert pl["classes"] == {paths["sofa"]: "sofa", paths["table"]: "", paths["shelf"]: ""}
    assert {f["camera"]["target"] for f in pl["frames"]} == {"sofa"}  # cameras aim at what is labelled
    assert pl["stuff"] == ["floor", "wall"] and not pl["blank_structure"]
    s.spec.room.openings = [Opening(kind="door", wall="south", offset=1.0, width=1.2, style="auto_sliding")]
    assert plan(s, SdgSpec(frames=1))["stuff"] == ["floor", "wall", "door"]  # not CocoWriter's "other"
    pl = plan(s, SdgSpec(frames=1, structure=False))
    assert pl["stuff"] == [] and pl["blank_structure"]
    # with the purpose's targets, objects left out of `classes` count as outside too
    s.spec.purpose = PurposeSpec(name="sdg", targets=["sofa"])
    pl = plan(s, SdgSpec(frames=1, classes={"sofa": "sofa"}))
    assert any("outside the dataset's classes" in w for w in pl["warnings"])
    d = tmp_path / "living"
    r = service.generate_data(d, {"frames": 1, "classes": {"sofa": "sofa"}, "others": "background"}, dry_run=True)
    assert r["classes"] == ["sofa"]
