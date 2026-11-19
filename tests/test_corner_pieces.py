"""Corner pieces: a footprint with one open bbox corner is put in a room corner with that corner facing in."""

import dataclasses
import math

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets import corner
from agentic_sim_world_gen.assets.record import AssetRecord

ROOM = {"width": 4.0, "depth": 3.5, "height": 2.5}


def rec(name, w, d, h, front_deg, corner=True):
    return AssetRecord(
        id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
        semantic_class=name, license="CC-BY 4.0", front_deg=front_deg, corner=corner,
    )


CATALOG = {
    "l desk": [rec("l desk", 1.5, 1.4, 0.75, 225.0)],  # the inner corner at -X-Y
    "corner wall cabinet": [rec("corner wall cabinet", 0.6, 0.6, 0.7, 45.0)],  # diagonal doors at +X+Y
    "cabinet": [rec("cabinet", 0.6, 0.6, 0.9, 270.0, corner=False)],
}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def place(tmp_path, *objects):
    st = service.create_scene({"name": "t", "room": ROOM, "objects": list(objects)}, tmp_path / "t")
    return st, {o["id"]: o for o in st["objects"]}


def footprint(o, a):
    """World bbox of the footprint (the yaw is a multiple of 90 deg for a corner piece)."""
    w, d = a.size_m[:2]
    if round(o["yaw_deg"]) % 180:
        w, d = d, w
    x, y = o["position"]
    return x - w / 2, x + w / 2, y - d / 2, y + d / 2


def facing(o, a):
    f = math.radians(o["yaw_deg"] + a.front_deg)
    return math.cos(f), math.sin(f)


@pytest.mark.parametrize("along,end", [(0.9, "east"), (0.1, "west")])
def test_l_desk_goes_into_the_corner_open_side_in(tmp_path, along, end):
    st, o = place(tmp_path, {"id": "desk", "query": "l desk", "layout": {"against_wall": "north", "along": along}})
    assert st["issues"] == [], st["issues"]
    a = CATALOG["l desk"][0]
    x0, x1, y0, y1 = footprint(o["desk"], a)
    assert y1 == pytest.approx(3.5, abs=0.03)  # against the north wall
    assert (x1 == pytest.approx(4.0, abs=0.03)) if end == "east" else (x0 == pytest.approx(0.0, abs=0.03))
    fx, fy = facing(o["desk"], a)  # the open corner along the room's diagonal at that corner
    assert fy == pytest.approx(-math.sqrt(0.5), abs=1e-3)
    assert fx == pytest.approx(-math.sqrt(0.5) if end == "east" else math.sqrt(0.5), abs=1e-3)
    assert round(o["desk"]["yaw_deg"]) % 90 == 0  # its sides along the walls


def test_hung_corner_cabinet_without_along(tmp_path):
    """No along: either end of the wall; hung at wall_height like any wall cabinet."""
    st, o = place(tmp_path, {"id": "c", "query": "corner wall cabinet",
                             "layout": {"against_wall": "south", "wall_height": 1.5}})
    assert st["issues"] == [], st["issues"]
    a = CATALOG["corner wall cabinet"][0]
    x0, x1, y0, y1 = footprint(o["c"], a)
    assert y0 == pytest.approx(0.0, abs=0.03) and min(x0, 4.0 - x1) == pytest.approx(0.0, abs=0.03)
    fx, fy = facing(o["c"], a)
    assert fy > 0.7 and abs(fx) > 0.7 and (fx > 0) == (x0 < 1.0)  # out of the corner, into the room
    assert o["c"]["z"] == pytest.approx(1.5)


def test_plain_cabinet_keeps_its_front_to_the_room(tmp_path):
    st, o = place(tmp_path, {"id": "c", "query": "cabinet", "layout": {"against_wall": "north", "along": 0.9}})
    fx, fy = facing(o["c"], CATALOG["cabinet"][0])
    assert fy == pytest.approx(-1.0, abs=1e-6)


def test_corner_shows_in_the_summary():
    assert CATALOG["l desk"][0].summary()["corner"] is True
    assert "corner" not in CATALOG["cabinet"][0].summary()


# ------------------------------------------------------------------ corner_deg from the geometry


def _stage(tmp_path, polys, up="Z", mpu=1.0):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom
    f = tmp_path / "m.usda"
    st = Usd.Stage.CreateNew(str(f))
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z if up == "Z" else UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(st, mpu)
    root = UsdGeom.Xform.Define(st, "/A")
    st.SetDefaultPrim(root.GetPrim())
    for i, poly in enumerate(polys):  # a flat top face per polygon, at a height of 0.7
        m = UsdGeom.Mesh.Define(st, f"/A/m{i}")
        pts = [(x, y, 0.7) if up == "Z" else (x, 0.7, -y) for x, y in poly]
        m.GetPointsAttr().Set(pts)
        m.GetFaceVertexCountsAttr().Set([len(poly)])
        m.GetFaceVertexIndicesAttr().Set(list(range(len(poly))))
    st.Save()
    return f


@pytest.mark.parametrize("polys,deg", [
    ([[(0, 0), (0.6, 0), (0.6, 0.3), (0.3, 0.6), (0, 0.6)]], 45),  # diagonal doors at +X+Y
    ([[(0, 0), (1.5, 0), (1.5, 1.4), (0, 1.4)]], None),  # a box
    ([[(0, 0.8), (1.5, 0.8), (1.5, 1.4), (0, 1.4)], [(0, 0), (0.6, 0), (0.6, 0.8), (0, 0.8)]], 315),  # L, open +X-Y
    ([[(0.3, 0), (0.9, 0), (1.2, 0.5), (0.6, 1.0), (0, 0.5)]], None),  # a pentagon: two corners open
])
def test_corner_deg(tmp_path, polys, deg):
    assert corner.corner_deg(_stage(tmp_path, polys)) == deg


def test_corner_deg_y_up(tmp_path):
    poly = [(0, 0), (0.6, 0), (0.6, 0.3), (0.3, 0.6), (0, 0.6)]
    assert corner.corner_deg(_stage(tmp_path, [poly], up="Y")) == 45


def test_small_things_are_not_corner_pieces(tmp_path):
    """A 0.6 m footprint in centimeters is 6 mm: under CORNER_MIN_M (a watch winder's open lid looks the same)."""
    poly = [(0, 0), (0.6, 0), (0.6, 0.3), (0.3, 0.6), (0, 0.6)]
    assert corner.corner_deg(_stage(tmp_path, [poly], mpu=0.01)) is None


def test_chair_in_front_of_an_l_desk_faces_a_side(tmp_path):
    """in_front_of a corner piece: along a side (45 deg clockwise of its open corner), next to the desk."""
    chair = rec("chair", 0.55, 0.55, 0.9, 270.0, corner=False)
    CATALOG["chair"] = [chair]
    try:
        st, o = place(tmp_path, {"id": "desk", "query": "l desk", "layout": {"against_wall": "south", "along": 0.1}},
                      {"id": "chair", "query": "chair", "layout": {"in_front_of": "desk"}})
    finally:
        del CATALOG["chair"]
    d, c = o["desk"], o["chair"]
    fx, fy = facing(c, chair)
    assert abs(abs(fx) - 1) < 1e-6 or abs(abs(fy) - 1) < 1e-6  # square to the walls, not diagonal
    x0, x1, y0, y1 = footprint(d, CATALOG["l desk"][0])
    cx, cy = c["position"]
    assert x0 - 0.6 < cx < x1 + 0.6 and y0 - 0.6 < cy < y1 + 0.6  # beside the desk, not out in the room


def test_free_wall_end_is_not_a_corner(tmp_path):
    from agentic_sim_world_gen.layout.room import Face
    from agentic_sim_world_gen.layout.solver import _concave_end

    class G:
        faces = [Face("w1", "south", (0.0, 0.0), (4.0, 0.0), 90.0), Face("w2", "west", (0.0, 3.0), (0.0, 0.0), 0.0)]
    assert _concave_end(G, G.faces[0], 0) and not _concave_end(G, G.faces[0], 1)


@pytest.mark.parametrize("deg,k", [(45, 1), (135, 2), (225, 3), (315, 0), (270, 3), (0, 0)])
def test_board_front_of_a_diagonal_front(deg, k):
    from agentic_sim_world_gen.layout.fill import _board_frame

    back = _board_frame({"x0": 0, "x1": 1, "y0": 0, "y1": 1}, deg)[1]
    assert back == [(-1.0, 0.0), (0.0, -1.0), (1.0, 0.0), (0.0, 1.0)][k]
