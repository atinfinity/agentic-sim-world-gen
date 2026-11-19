"""Corner pieces of any source: a chosen asset against a wall whose name says corner is measured once."""

import json

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets import corner
from agentic_sim_world_gen.assets.record import AssetRecord

ROOM = {"width": 4.0, "depth": 3.5, "height": 2.5}
L_DESK = [[(0, 0.8), (1.5, 0.8), (1.5, 1.4), (0, 1.4)], [(0, 0), (0.6, 0), (0.6, 0.8), (0, 0.8)]]  # open at +X-Y
BOX = [[(0, 0), (1.5, 0), (1.5, 1.4), (0, 1.4)]]


def usd(path, polys):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom
    st = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    root = UsdGeom.Xform.Define(st, "/A")
    st.SetDefaultPrim(root.GetPrim())
    for i, poly in enumerate(polys):
        m = UsdGeom.Mesh.Define(st, f"/A/m{i}")
        m.GetPointsAttr().Set([(x, y, 0.75) for x, y in poly])
        m.GetFaceVertexCountsAttr().Set([len(poly)])
        m.GetFaceVertexIndicesAttr().Set(list(range(len(poly))))
    st.Save()
    return path


def rec(f, source="local:assets"):
    return AssetRecord(id=f"{source}:{f.stem}", source=source, uri=str(f), name=f.stem,
                       bbox_min=(0.0, 0.0, 0.0), bbox_max=(1.5, 1.4, 0.75), up_axis="Z", meters_per_unit=1.0,
                       semantic_class="desk", license="CC-BY 4.0", front_deg=270.0)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_CACHE", str(tmp_path / "cache"))
    d = tmp_path / "assets"
    d.mkdir()
    cat = {"corner desk": [rec(usd(d / "corner_desk.usda", L_DESK))],
           "desk": [rec(usd(d / "desk_l_shape.usda", L_DESK))],  # the name does not say corner: not measured
           "corner box": [rec(usd(d / "corner_box.usda", BOX))]}
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: cat.get(q, [])})())
    yield cat
    service.set_provider(None)


NE = {"against_wall": "north", "along": 0.9}


def chosen(tmp_path, query, layout=NE):
    o = {"id": "o", "query": query, **({"layout": layout} if layout else {})}
    st = service.create_scene({"name": "t", "room": ROOM, "objects": [o]}, tmp_path / "t")
    sc = json.loads((tmp_path / "t" / "scene.resolved.json").read_text())
    p = sc["placements"][0]
    return st, p["candidates"][p["selected"]]


def test_corner_named_asset_goes_into_the_corner(tmp_path, catalog):
    st, c = chosen(tmp_path, "corner desk")
    assert c["corner"] is True and c["front_deg"] == 315.0 and c["front_deg_own"] == 270.0
    assert c["corner_v"] == corner.VERSION
    assert st["issues"] == [], st["issues"]
    o = st["objects"][0]
    w, d = (1.4, 1.5) if round(o["yaw_deg"]) % 180 else (1.5, 1.4)
    x, y = o["position"]
    assert x + w / 2 == pytest.approx(4.0, abs=0.03) and y + d / 2 == pytest.approx(3.5, abs=0.03)  # NE corner


@pytest.mark.parametrize("query", ["desk", "corner box"])
def test_not_measured_or_not_a_corner(tmp_path, catalog, query):
    _, c = chosen(tmp_path, query)
    assert not c.get("corner") and c["front_deg"] == 270.0
    assert ("corner_v" in c) == (query == "corner box")  # measured: the name says corner


def test_measured_whatever_the_layout(tmp_path, catalog):
    """Not against a wall: measured all the same, so the asset faces the same way whatever its history."""
    _, c = chosen(tmp_path, "corner desk", layout=None)
    assert c["corner"] is True and c["front_deg"] == 315.0


def test_front_corrected_by_hand_is_kept(tmp_path, catalog):
    o = {"id": "o", "query": "corner desk", "front_offset_deg": 90, "layout": NE}
    service.create_scene({"name": "t", "room": ROOM, "objects": [o]}, tmp_path / "t")
    sc = json.loads((tmp_path / "t" / "scene.resolved.json").read_text())
    c = sc["placements"][0]["candidates"][0]
    assert not c.get("corner") and c["front_deg"] == 270.0 and c["corner_v"] == corner.VERSION


def test_folder_name_is_not_read(tmp_path, catalog):
    d = tmp_path / "corner_pack"
    d.mkdir()
    catalog["desk"] = [rec(usd(d / "desk_l_shape.usda", L_DESK))]
    _, c = chosen(tmp_path, "desk")
    assert "corner_v" not in c and not c.get("corner")


def test_no_meshes_is_not_cached(tmp_path):
    pytest.importorskip("pxr")
    f = usd(tmp_path / "corner_empty.usda", [])
    with pytest.raises(corner.NoMeshes):
        corner.find(str(f), tmp_path / "c")
    assert not (tmp_path / "c" / "corner").exists()


def test_unreadable_tried_again_later(tmp_path, catalog, monkeypatch):
    def boom(uri, cache):
        raise OSError("offline")
    monkeypatch.setattr(corner, "find", boom)
    _, c = chosen(tmp_path, "corner desk")
    assert "corner_v" not in c and c["corner_error"] == "offline" and c["corner_failed"] > 0
    assert not c.get("corner")


def test_new_version_goes_back_to_the_own_front(tmp_path, catalog, monkeypatch):
    chosen(tmp_path, "corner desk")
    monkeypatch.setattr(corner, "VERSION", corner.VERSION + 1)
    monkeypatch.setattr(corner, "find", lambda uri, cache: None)  # the new detector says: not a corner piece
    service.set_layout(tmp_path / "t", "o", {"against_wall": "north", "along": 0.5})
    sc = json.loads((tmp_path / "t" / "scene.resolved.json").read_text())
    c = sc["placements"][0]["candidates"][0]
    assert c["front_deg"] == 270.0 and not c.get("corner") and "front_deg_own" not in c


def test_find_is_cached(tmp_path, catalog, monkeypatch):
    f = catalog["corner desk"][0].uri
    assert corner.find(f, tmp_path / "c") == 315
    monkeypatch.setattr(corner, "corner_deg", lambda p: pytest.fail("measured again"))
    assert corner.find(f, tmp_path / "c") == 315
    assert corner.find("polyhaven:sofa", tmp_path / "c") is None
