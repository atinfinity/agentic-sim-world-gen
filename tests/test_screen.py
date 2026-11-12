"""Screen images on signage (ObjectSpec.screen): offline, with a tiny signage asset made with usd-core."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pxr")
PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402
from pxr import Sdf, Usd, UsdGeom, UsdShade  # noqa: E402

from agentic_sim_world_gen import service  # noqa: E402
from agentic_sim_world_gen.assets.providers.local_folder import build_index  # noqa: E402
from agentic_sim_world_gen.scene.spec import record_from_dict, record_to_dict  # noqa: E402

GREY = (40, 40, 40)
W, H = 0.8, 0.5  # screen size [m]


def _quad(x0, x1, z0, z1, y, front):
    """Corners counter-clockwise as seen from the side the quad faces (-Y if front)."""
    if front:
        return [(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)]
    return [(x1, y, z0), (x0, y, z0), (x0, y, z1), (x1, y, z1)]


def make_signage(folder: Path, front_first: bool = False) -> Path:
    """Signage like a local folder's: a thin panel facing -Y whose texture is a 3x2 atlas. The front's picture is in the
    top-left cell (left half red, right half green, so a mirrored or turned copy shows), the back in the top-right
    cell (flat grey). Face order puts the back first unless front_first."""
    folder.mkdir(parents=True)
    (folder / "textures").mkdir()
    atlas = Image.new("RGB", (600, 400), GREY)
    cell = Image.new("RGB", (200, 200), (255, 0, 0))
    cell.paste((0, 255, 0), (100, 0, 200, 200))
    atlas.paste(cell, (0, 0))
    atlas.save(folder / "textures" / "Screen.png")
    Image.new("RGB", (8, 8), (128, 128, 255)).save(folder / "textures" / "Screen_normal.png")

    stage = Usd.Stage.CreateNew(str(folder / "sign.usd"))
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Sign")
    stage.SetDefaultPrim(root.GetPrim())
    mat = UsdShade.Material.Define(stage, "/Sign/_materials/ScreenMat")
    ps = UsdShade.Shader.Define(stage, "/Sign/_materials/ScreenMat/Principled_BSDF")
    ps.CreateIdAttr("UsdPreviewSurface")
    mat.CreateSurfaceOutput().ConnectToSource(ps.CreateOutput("surface", Sdf.ValueTypeNames.Token))
    for name, f in (("Image_Texture", "./textures/Screen.png"), ("Normal_Texture", "./textures/Screen_normal.png")):
        tex = UsdShade.Shader.Define(stage, f"/Sign/_materials/ScreenMat/{name}")
        tex.CreateIdAttr("UsdUVTexture")
        tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(f)
    pole = UsdGeom.Cube.Define(stage, "/Sign/Pole")
    UsdGeom.XformCommonAPI(pole).SetScale((0.05, 0.05, 0.5))

    z0, z1 = 1.0, 1.0 + H
    front = (_quad(-W / 2, W / 2, z0, z1, 0.0, True), [(0, 0.5), (1 / 3, 0.5), (1 / 3, 1), (0, 1)])
    back = (_quad(-W / 2, W / 2, z0, z1, 0.01, False), [(2 / 3, 0.5), (1, 0.5), (1, 1), (2 / 3, 1)])
    quads = [front, back] if front_first else [back, front]
    mesh = UsdGeom.Mesh.Define(stage, "/Sign/Screen")
    mesh.CreatePointsAttr([p for q, _ in quads for p in q])
    mesh.CreateFaceVertexCountsAttr([4, 4])
    mesh.CreateFaceVertexIndicesAttr(list(range(8)))
    st = UsdGeom.PrimvarsAPI(mesh.GetPrim()).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                                                           UsdGeom.Tokens.faceVarying)
    st.Set([uv for _, q in quads for uv in q])
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)
    stage.GetRootLayer().Save()
    (folder / "asset.json").write_text(json.dumps({
        "usd": "sign.usd", "name": "signage", "category": "fixture", "tags": ["digital signage"], "license": "CC0-1.0",
        "screen": {"variant_set": "Screen", "variants": ["On", "Off"], "default": "On",
                   "materials": [{"name": "ScreenMat", "image": "./textures/Screen.png", "off": "ScreenMat_Off"}]},
    }))
    return folder / "sign.usd"


@pytest.fixture
def lib(tmp_path):
    make_signage(tmp_path / "lib" / "sign")
    recs = build_index(tmp_path / "lib", tmp_path / "cache")
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    yield recs
    service.set_provider(None)


def poster(path: Path, size=(300, 600)) -> Path:
    """Portrait image: top half blue, bottom half yellow."""
    im = Image.new("RGB", size, (0, 0, 255))
    im.paste((255, 255, 0), (0, size[1] // 2, size[0], size[1]))
    im.save(path)
    return path


def test_index_reads_screens(lib):
    (r,) = lib
    assert [s["material"] for s in r.screens] == ["ScreenMat"]
    assert Path(r.screens[0]["image"]).is_file() and r.screens[0]["variant_set"] == "Screen"
    assert r.summary()["screens"] == ["ScreenMat"]
    d = record_to_dict(r)
    assert record_from_dict(d).screens == r.screens
    d.pop("screens")  # scenes saved before screens existed: read from asset.json
    assert record_from_dict(d).screens == r.screens


@pytest.mark.parametrize("front_first", [False, True])
def test_compose_draws_the_image_into_the_front_cell(tmp_path, front_first):
    make_signage(tmp_path / "lib" / "sign", front_first=front_first)
    recs = build_index(tmp_path / "lib", tmp_path / "cache")
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    try:
        d = tmp_path / "s"
        service.create_scene({"name": "s", "room": {"width": 4, "depth": 3, "height": 2.6},
                              "objects": [{"id": "sign", "query": "digital signage", "position": [2, 1.5]}]}, d)
        img = poster(tmp_path / "poster.png")
        (tmp_path / "poster.source.json").write_text(json.dumps({"title": "Safety", "license": "CC0-1.0"}))
        st = service.set_screen(d, "sign", {"image": str(img), "background": "#ffffff"})
        assert st["issues"] == []
        o = next(x for x in service.status(d)["objects"] if x["id"] == "sign")
        assert o["screens"] == ["ScreenMat"] and o["screen"]["image"] == str(img.resolve())
        service.compose_scene(d)
    finally:
        service.set_provider(None)

    tex = Image.open(d / "screens" / "sign_ScreenMat.png").convert("RGB")
    assert tex.size == (600, 400)
    # the 0.8 x 0.5 m front, seen from the front, is the 200 x 200 top-left cell (stretched across);
    # the 1:2 poster fits in the middle 0.25 m of 0.8 m (contain), white around it
    assert tex.getpixel((20, 100)) == (255, 255, 255) and tex.getpixel((180, 100)) == (255, 255, 255)
    assert tex.getpixel((100, 30)) == (0, 0, 255)  # top of the poster at the top of the cell (v = 1)
    assert tex.getpixel((100, 170)) == (255, 255, 0)
    assert tex.getpixel((500, 100)) == GREY  # the back keeps the original
    assert tex.getpixel((300, 300)) == GREY

    stage = Usd.Stage.Open(str(d / "scene.usda"))
    shader = stage.GetPrimAtPath("/World/Objects/sign/Asset/_materials/ScreenMat/Image_Texture")
    assert shader.GetAttribute("inputs:file").Get().path == str((d / "screens/sign_ScreenMat.png").resolve())
    normal = stage.GetPrimAtPath("/World/Objects/sign/Asset/_materials/ScreenMat/Normal_Texture")
    assert normal.GetAttribute("inputs:file").Get().path == "./textures/Screen_normal.png"  # other textures stay
    cd = stage.GetPrimAtPath("/World/Objects/sign").GetCustomDataByKey("agentic_sim_world_gen:screen")
    assert cd["screens"]["ScreenMat"]["title"] == "Safety" and cd["screens"]["ScreenMat"]["size_m"] == "[0.8, 0.5]"

    rep = service.license_report(d)
    assert [s["title"] for s in rep["screens"]] == ["Safety"] and not rep["warnings"]
    assert "## Screen images" in (d / "LICENSES.md").read_text()


def test_mirrored_uvs_keep_the_picture_readable(tmp_path):
    """If the front's UVs run right to left, the picture is flipped in the texture so it reads right."""
    usd = make_signage(tmp_path / "lib" / "sign", front_first=True)
    stage = Usd.Stage.Open(str(usd))
    st = UsdGeom.PrimvarsAPI(stage.GetPrimAtPath("/Sign/Screen")).GetPrimvar("st")
    uv = list(st.Get())
    uv[:4] = [(1 / 3, 0.5), (0, 0.5), (0, 1), (1 / 3, 1)]
    st.Set(uv)
    stage.GetRootLayer().Save()

    from agentic_sim_world_gen.usd.screen import render_texture, screen_faces

    composed = Usd.Stage.Open(str(usd))
    root = composed.GetDefaultPrim()
    sides = screen_faces(composed, root, composed.GetPrimAtPath("/Sign/_materials/ScreenMat"))
    img = Image.new("RGB", (160, 100), (255, 0, 255))
    img.paste((0, 255, 255), (80, 0, 160, 100))  # left magenta, right cyan
    img.save(tmp_path / "lr.png")
    render_texture(tmp_path / "lib/sign/textures/Screen.png", tmp_path / "lr.png", sides, "stretch", "#000000",
                   tmp_path / "out.png")
    tex = Image.open(tmp_path / "out.png").convert("RGB")
    # u = 0 is now the right edge of the screen: cyan on the texture's left
    assert tex.getpixel((20, 100)) == (0, 255, 255) and tex.getpixel((180, 100)) == (255, 0, 255)


def test_screen_issues(lib, tmp_path):
    d = tmp_path / "s"
    img = poster(tmp_path / "p.png")
    service.create_scene({"name": "s", "room": {"width": 4, "depth": 3, "height": 2.6}, "objects": [
        {"id": "a", "query": "digital signage", "position": [1, 1.5], "variants": {"Screen": "Off"},
         "screen": {"image": str(img)}},
        {"id": "b", "query": "digital signage", "position": [2.5, 1.5],
         "screen": {"image": {"ScreenMat": str(tmp_path / "missing.png"), "Other": str(img)}}},
    ]}, d)
    kinds = sorted((i["kind"], i["objects"][0]) for i in service.status(d)["issues"])
    assert kinds == [("bad_screen", "b"), ("bad_screen", "b"), ("screen_off", "a")]
    with pytest.raises(FileNotFoundError):
        service.set_screen(d, "a", {"image": str(tmp_path / "nope.png")})
    service.set_variants(d, "a", {"Screen": "On"})
    service.set_screen(d, "b", None)
    st = service.status(d)
    assert "screen" not in next(x for x in st["objects"] if x["id"] == "b")
    # b shows the asset's own picture again: fine only where it fits (room.place)
    assert [(i["kind"], i["objects"][0]) for i in st["issues"]] == [("screen_content", "b")]
    # warnings for an image without a license
    assert any("screen image p.png" in w for w in service.license_report(d)["warnings"])


@pytest.mark.parametrize("place, variants, places, kind", [
    (None, {}, None, "screen_content"),  # unknown place: asks for room.place
    ("factory", {}, None, "screen_content"),  # a shop ad in a factory
    ("factory", {"Screen": "Off"}, None, None),  # dark screen
    ("convenience_store", {}, None, None),  # default: shops, cafés, restaurants
    ("factory", {}, ["factory"], None),  # asset.json screen.places
    ("cafe", {}, ["factory"], "screen_content"),
    ("drugstore", {}, None, None),  # every kind of shop is in the default
    ("home_center", {}, ["shop"], None),  # made for any shop
    ("drugstore", {}, ["convenience_store"], "screen_content"),  # made for one kind of shop
    ("factory", {}, ["shop"], "screen_content"),
    ("office", {}, ["any"], None),  # an exit sign suits every place
    (None, {}, ["any"], None),
])
def test_built_in_picture_must_fit_the_place(tmp_path, place, variants, places, kind):
    make_signage(tmp_path / "lib" / "sign")
    if places:
        side = tmp_path / "lib" / "sign" / "asset.json"
        meta = json.loads(side.read_text())
        meta["screen"]["places"] = places
        side.write_text(json.dumps(meta))
    recs = build_index(tmp_path / "lib", tmp_path / "cache")
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    try:
        d = tmp_path / "s"
        room = {"width": 4, "depth": 3, "height": 2.6, **({"place": place} if place else {})}
        service.create_scene({"name": "s", "room": room, "objects": [
            {"id": "sign", "query": "digital signage", "position": [2, 1.5], "variants": variants}]}, d)
        issues = service.status(d)["issues"]
        assert [i["kind"] for i in issues] == ([kind] if kind else [])
        if kind and place is None:
            service.set_room(d, place="convenience_store")
            assert service.status(d)["issues"] == []
    finally:
        service.set_provider(None)


def test_no_screen_on_other_assets(tmp_path):
    from test_layout import rec

    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None:
                                        [rec("box", "box", 0.5, 0.5, 0.5)]})())
    try:
        d = tmp_path / "s"
        service.create_scene({"name": "s", "room": {"width": 4, "depth": 3, "height": 2.6}, "objects": [
            {"id": "a", "query": "box", "position": [1, 1.5], "screen": {"image": "x.png"}}]}, d)
        assert [i["kind"] for i in service.status(d)["issues"]] == ["no_screen"]
        with pytest.raises(ValueError, match="no screen"):
            service.set_screen(d, "a", {"image": "x.png"})
    finally:
        service.set_provider(None)


def make_picture(folder: Path) -> Path:
    """A printed shop picture (poster / board): asset.json `picture` with a Blank variant."""
    usd = make_signage(folder)
    meta = json.loads((folder / "asset.json").read_text())
    meta.pop("screen")
    meta["picture"] = {"variant_set": "Picture", "variants": ["Default", "Blank"], "default": "Default", "off": "Blank",
                       "materials": [{"name": "ScreenMat", "image": "./textures/Screen.png", "off": "ScreenMat_Blank",
                                      "blank_image": "./textures/Screen_Blank.png"}],
                       "places": ["convenience_store"]}
    (folder / "asset.json").write_text(json.dumps(meta))
    return usd


def test_printed_picture(tmp_path):
    """Posters and boards (asset.json `picture`): checked against room.place like screens, Picture=Blank is plain."""
    make_picture(tmp_path / "lib" / "sign")
    recs = build_index(tmp_path / "lib", tmp_path / "cache")
    (r,) = recs
    assert [(s["kind"], s["material"], s["on"], s["off_variant"]) for s in r.screens] == [
        ("picture", "ScreenMat", "Default", "Blank")]
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    try:
        d = tmp_path / "s"
        service.create_scene({"name": "s", "room": {"width": 4, "depth": 3, "height": 2.6, "place": "factory"},
                              "objects": [{"id": "sign", "query": "digital signage", "position": [2, 1.5]}]}, d)
        (i,) = service.status(d)["issues"]
        assert i["kind"] == "screen_content" and '{"Picture": "Blank"} (plain)' in i["detail"]
        service.set_variants(d, "sign", {"Picture": "Blank"})
        assert service.status(d)["issues"] == []
        img = poster(tmp_path / "p.png")
        (tmp_path / "p.source.json").write_text(json.dumps({"title": "Safety", "license": "CC0-1.0"}))
        (i,) = service.set_screen(d, "sign", {"image": str(img)})["issues"]
        assert i["kind"] == "screen_off" and "makes the picture blank" in i["detail"]
        service.set_variants(d, "sign", {})
        assert service.status(d)["issues"] == []
        service.compose_scene(d)
    finally:
        service.set_provider(None)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    shader = stage.GetPrimAtPath("/World/Objects/sign/Asset/_materials/ScreenMat/Image_Texture")
    assert shader.GetAttribute("inputs:file").Get().path == str((d / "screens/sign_ScreenMat.png").resolve())


@pytest.mark.parametrize("place, first", [("drugstore", "b_any"), ("bookstore", "a_book"), (None, "a_book")])
def test_candidates_with_a_picture_for_another_place_go_last(tmp_path, place, first):
    """A bookstore's checkout sign should not be picked for a drugstore when an equally good asset fits."""
    make_signage(tmp_path / "lib" / "a_book")
    make_signage(tmp_path / "lib" / "b_any")
    side = tmp_path / "lib" / "a_book" / "asset.json"
    meta = json.loads(side.read_text())
    meta["screen"]["places"] = ["bookstore"]
    side.write_text(json.dumps(meta))
    recs = sorted(build_index(tmp_path / "lib", tmp_path / "cache"), key=lambda r: r.uri)
    assert "a_book" in recs[0].uri
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    try:
        d = tmp_path / "s"
        room = {"width": 4, "depth": 3, "height": 2.6, **({"place": place} if place else {})}
        service.create_scene({"name": "s", "room": room, "objects": [
            {"id": "sign", "query": "digital signage", "position": [2, 1.5]}]}, d)
        s = service.load(d)
        assert first in s.placements[0].asset.uri
        assert len(s.placements[0].candidates) == 2  # moved, not dropped
    finally:
        service.set_provider(None)


def test_a_much_better_match_with_another_places_picture_stays_first(tmp_path):
    """A convenience-store gondola in a liquor store: its header can be blanked, so it should not fall behind
    a rack that only matches "shelf" (the picture costs PICTURE_PENALTY of the score, not the whole rank)."""
    make_signage(tmp_path / "lib" / "a_gondola")
    make_signage(tmp_path / "lib" / "b_rack")
    for name, tags, places in (("a_gondola", ["gondola shelf"], ["convenience_store"]), ("b_rack", ["shelf"], None)):
        side = tmp_path / "lib" / name / "asset.json"
        meta = json.loads(side.read_text())
        meta["tags"] = tags
        if places:
            meta["screen"]["places"] = places
        else:
            meta.pop("screen")
        side.write_text(json.dumps(meta))
    recs = sorted(build_index(tmp_path / "lib", tmp_path / "cache"), key=lambda r: r.uri, reverse=True)
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    try:
        d = tmp_path / "s"
        service.create_scene({"name": "s", "room": {"width": 4, "depth": 3, "height": 2.6, "place": "liquor_store"},
                              "objects": [{"id": "g", "query": "gondola shelf", "position": [2, 1.5]}]}, d)
        s = service.load(d)
        assert [c["uri"].split("/")[-2] for c in s.placements[0].candidates] == ["a_gondola", "b_rack"]
        (i,) = [i for i in service.status(d)["issues"] if i["kind"] == "screen_content"]
        assert "convenience_store" in i["detail"]
    finally:
        service.set_provider(None)


def test_shown_pictures_and_their_wording(tmp_path):
    """Status lists the asset's own pictures the scene shows (with asset.json's text / lang / audience),
    view_pictures shows them once, and a blanked one drops out."""
    folder = tmp_path / "lib" / "header"
    make_picture(folder)
    meta = json.loads((folder / "asset.json").read_text())
    meta["picture"]["places"] = ["apparel_store"]
    meta["picture"]["text"] = ["メンズ トップス", "MEN'S TOPS"]
    meta["picture"]["lang"] = "ja"
    meta["picture"]["audience"] = "men"
    (folder / "asset.json").write_text(json.dumps(meta))
    recs = build_index(tmp_path / "lib", tmp_path / "cache")
    assert recs[0].screens[0]["text"] == ["メンズ トップス", "MEN'S TOPS"] and recs[0].screens[0]["audience"] == ["men"]
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    try:
        d = tmp_path / "s"
        service.create_scene({"name": "s", "room": {"width": 4, "depth": 3, "height": 2.6, "place": "apparel_store"},
                              "objects": [{"id": "a", "query": "wall shelf", "position": [1, 1.5]},
                                          {"id": "b", "query": "wall shelf", "position": [3, 1.5]}]}, d)
        st = service.status(d)
        assert st["issues"] == []
        (p,) = st["pictures"]
        assert p["objects"] == ["a", "b"] and p["lang"] == "ja" and p["blank"] == {"Picture": "Blank"}
        assert not p["viewed"] and "MEN'S TOPS" in st["pictures_hint"]
        ((short, img),) = service.picture_images(d)
        assert short["objects"] == ["a", "b"] and img[:4] == b"\x89PNG"
        st = service.status(d)
        assert st["pictures"][0]["viewed"] and "pictures_hint" not in st
        service.set_variants(d, "a", {"Picture": "Blank"})
        assert service.status(d)["pictures"][0]["objects"] == ["b"]
        service.set_variants(d, "b", {"Picture": "Blank"})
        assert "pictures" not in service.status(d)
    finally:
        service.set_provider(None)
