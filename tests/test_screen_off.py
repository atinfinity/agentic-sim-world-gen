"""Monitors and TVs switched off by default in a classroom / office / lab: compose paints their screen black
(validate.screen_dark, ObjectSpec.screen_on, set_screen "off" / "on"). Offline, with a lit TV (an emissive texture)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pxr")
pytest.importorskip("PIL")
from PIL import Image  # noqa: E402
from pxr import Usd  # noqa: E402

from agentic_sim_world_gen import service  # noqa: E402
from agentic_sim_world_gen.assets.localize import screened_remote  # noqa: E402
from agentic_sim_world_gen.assets.providers.local_folder import build_index  # noqa: E402
from agentic_sim_world_gen.assets.record import AssetRecord  # noqa: E402
from agentic_sim_world_gen.layout.validate import screen_dark  # noqa: E402
from agentic_sim_world_gen.scene.spec import ObjectSpec, ResolvedScene  # noqa: E402

from test_detected_screen import make_tv  # noqa: E402
from test_screen import make_signage, poster  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    yield
    service.set_provider(None)


def _scene(tmp_path, place, query="computer monitor", lib=None):
    if lib is None:
        lib = tmp_path / "lib"
        make_tv(lib / "tv", "mdl")
    recs = build_index(lib, tmp_path / "cache")
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    d = tmp_path / "s"
    room = {"width": 4, "depth": 3, "height": 2.6, **({"place": place} if place else {})}
    service.create_scene({"name": "s", "room": room,
                          "objects": [{"id": "tv", "query": query, "position": [2, 1.5]}]}, d)
    return d


def _screen(d):
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    return stage.GetPrimAtPath("/World/Objects/tv").GetCustomDataByKey("agentic_sim_world_gen:screen")


def _front(tex: Path):
    """Mean color of the front's cell (top left) and of the back's (top right) in the screen's texture."""
    from PIL import ImageStat

    im = Image.open(tex).convert("RGB")
    w, h = im.size
    cell = lambda x0: ImageStat.Stat(im.crop((x0 + 10, 10, x0 + w // 3 - 10, h // 2 - 10))).mean  # noqa: E731
    return cell(0), cell(2 * w // 3)


def test_a_monitor_in_a_classroom_is_off(tmp_path):
    d = _scene(tmp_path, "school")
    res = service.compose_scene(d)
    assert res["screens_off"] == ["tv"] and "set_screen" in res["screens_off_hint"]
    assert not [w for w in res.get("warnings", []) if "screen" in w]
    data = _screen(d)
    assert data["off"] == "place" and data["detected"]
    # a found screen: its whole lit texture black (the LG monitor's screen is 4 face groups, each on half of it)
    tex = Path(data["screens"]["ScreenMat"]["texture"])
    assert tex == (d / "screens" / "off.png").resolve() and Image.open(tex).convert("RGB").getextrema() == ((0, 0),) * 3
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    mat = "/World/Objects/tv/Asset/_materials/ScreenMat"
    assert stage.GetPrimAtPath(f"{mat}/Shader").GetAttribute("inputs:emissive_color_texture").Get().path == str(tex)
    # the base color on the same atlas: a copy, black on the screen's faces only (the unused bottom keeps its grey)
    base = Path(data["screens"]["ScreenMat"]["base_texture"])
    assert stage.GetPrimAtPath(f"{mat}/Image_Texture").GetAttribute("inputs:file").Get().path == str(base)
    front, back = _front(base)
    im = Image.open(base).convert("RGB")
    assert max(front) < 1 and max(back) < 1 and min(im.getpixel((300, 350))) > 30
    rep = service.license_report(d)
    assert not rep.get("screens")  # the black image is no credited picture


@pytest.mark.parametrize("place", [None, "home", "convenience_store"])
def test_elsewhere_the_screen_stays_as_it_is(tmp_path, place):
    d = _scene(tmp_path, place)
    res = service.compose_scene(d)
    assert "screens_off" not in res and not _screen(d)


def test_on_keeps_it_lit_and_off_turns_it_off_anywhere(tmp_path):
    d = _scene(tmp_path, "office")
    service.set_screen(d, "tv", "on")
    assert ResolvedScene.load(d / "scene.resolved.json").obj("tv").screen_on is True
    assert "screens_off" not in service.compose_scene(d) and not _screen(d)
    service.set_screen(d, "tv", None)  # back to the place's default
    assert service.compose_scene(d)["screens_off"] == ["tv"]

    service.set_room(d, place="home")
    service.compose_scene(d)
    assert not _screen(d)
    service.set_screen(d, "tv", "off")
    res = service.compose_scene(d)
    assert "screens_off" not in res  # asked for, not by the place
    assert _screen(d)["off"] == "screen_on"
    with pytest.raises(ValueError, match="off"):
        service.set_screen(d, "tv", "dark")


def test_an_image_wins_over_off(tmp_path):
    d = _scene(tmp_path, "school")
    service.set_screen(d, "tv", {"image": str(poster(tmp_path / "p.png"))})
    service.compose_scene(d)
    data = _screen(d)
    assert "off" not in data
    front, _ = _front(Path(data["screens"]["ScreenMat"]["texture"]))
    assert max(front) > 20  # the poster, not black


def test_declared_signage_in_an_office_is_off_and_no_issue(tmp_path):
    """A declared screen (a signage asset) asked for as a monitor: painted black, no screen_content issue."""
    make_signage(tmp_path / "lib" / "sign")
    d = _scene(tmp_path, "office", query="wall monitor", lib=tmp_path / "lib")
    res = service.compose_scene(d)
    assert res["screens_off"] == ["tv"] and not [i for i in res["issues"] if i["kind"] == "screen_content"]
    data = _screen(d)
    assert data["off"] == "place" and "detected" not in data
    front, back = _front(Path(data["screens"]["ScreenMat"]["texture"]))
    assert max(front) < 1 and min(back) > 30  # a declared atlas: the front black, the panel's back as it was
    # the same signage asked for as signage shows its café ad in the office: still an issue
    d2 = tmp_path / "s2"
    service.create_scene({"name": "s2", "room": {"width": 4, "depth": 3, "height": 2.6, "place": "office"},
                          "objects": [{"id": "tv", "query": "digital signage", "position": [2, 1.5]}]}, d2)
    assert [i for i in service.status(d2)["issues"] if i["kind"] == "screen_content"]


def _rec(uri="https://huggingface.co/datasets/LGElectronics/simready-assets/resolve/e965976d90f025237ad9ea8881f5f82755f26d9d/monitor_x/m.usd", **kw):
    kw.setdefault("name", "m")
    return AssetRecord(id=kw["name"], source="simready-central", uri=uri, bbox_min=(0, 0, 0), bbox_max=(0.6, 0.2, 0.5),
                       **kw)


@pytest.mark.parametrize("query,klass,place,dark", [
    ("computer monitor", None, "school", True),
    ("monitor", None, "office", True),
    ("television", None, "lab", True),
    ("laptop", None, "office", True),
    ("pc", "computer monitor", "school", True),  # the asset's class names it
    ("computer monitor", None, "home", None),
    ("computer monitor", None, None, None),
    ("display case", None, "school", None),
    ("projection screen", None, "school", None),
    ("TVs", None, "office", True),  # plural (review)
    ("tvs", None, "office", True),
    ("computer monitors", None, "school", True),
    ("TV stand", None, "office", None),  # furniture for a screen (review)
    ("monitor arm", None, "office", None),
    ("television cabinet", None, "lab", None),
    ("monitor desk", None, "school", None),
    ("wall mount for tv", None, "office", None),
    ("desk", "monitor stand", "office", None),
])
def test_screen_dark(query, klass, place, dark):
    o = ObjectSpec(id="m", query=query)
    assert screen_dark(o, _rec(semantic_class=klass), place) is dark


def test_screen_furniture_in_alt_queries_is_no_screen():
    o = ObjectSpec(id="m", query="low cabinet", alt_queries=["TV stand", "television cabinet", "monitor desk"])
    assert screen_dark(o, _rec(), "office") is None
    o = ObjectSpec(id="m", query="screen", alt_queries=["flat screen TV"])
    assert screen_dark(o, _rec(), "office") is True


def test_preview_surface_emission_goes_black(tmp_path):
    """A TV whose UsdPreviewSurface emits from the image node: that node's file is the emissive one."""
    lib = tmp_path / "lib"
    make_tv(lib / "tv", "preview")
    d = _scene(tmp_path, "office", lib=lib)
    service.compose_scene(d)
    tex = Path(_screen(d)["screens"]["ScreenMat"]["texture"])
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    f = stage.GetPrimAtPath("/World/Objects/tv/Asset/_materials/ScreenMat/Image_Texture").GetAttribute("inputs:file")
    assert f.Get().path == str(tex) and tex.name == "off.png"


def test_screen_dark_choices():
    a = _rec()
    assert screen_dark(ObjectSpec(id="m", query="monitor", screen_on=True), a, "office") is None
    assert screen_dark(ObjectSpec(id="m", query="vase", screen_on=False), a, "home") is True
    poster_rec = _rec(screens=[{"kind": "picture", "material": "P", "image": "p.png"}])
    assert screen_dark(ObjectSpec(id="m", query="monitor", screen_on=False), poster_rec, "office") is None


def test_hf_monitor_is_fetched_only_when_asked(tmp_path):
    """A Hugging Face monitor: fetched (like a tinted one) for set_screen "off", never just for the place's default
    (review): that one is taken from the cache only."""
    from types import SimpleNamespace as NS

    def scene(place, **kw):
        objs = [ObjectSpec(id="m", query="computer monitor", **kw), ObjectSpec(id="d", query="desk")]
        return NS(spec=NS(objects=objs, room=NS(place=place)),
                  placements=[NS(object_id="m", asset=_rec()), NS(object_id="d", asset=_rec(name="d"))])

    assert screened_remote(scene("school")) == set()
    assert screened_remote(scene("school"), by_place=True) == {"m"}
    assert screened_remote(scene("home"), by_place=True) == set()
    assert screened_remote(scene("home", screen_on=False)) == {"m"}
    assert screened_remote(scene("school", screen_on=False), by_place=True) == set()
    assert screened_remote(scene("school", screen_on=True), by_place=True) == set()


HF = "https://huggingface.co/datasets/x/y/resolve/" + "0" * 40 + "/tv/sign.usd"


def _remote(d):
    s = service.load(d)
    p = s.placements[0]
    local = p.asset.uri
    p.candidates[p.selected]["uri"] = HF
    s.save(d / "scene.resolved.json")
    return local


def test_place_off_takes_a_remote_monitor_from_the_cache_only(tmp_path, monkeypatch):
    from agentic_sim_world_gen.assets import localize

    d = _scene(tmp_path, "school")
    local = _remote(d)
    calls = []

    def not_cached(s, cache_dir=None, only=None, cached_only=False):
        calls.append((only, cached_only))
        return {}

    monkeypatch.setattr(localize, "localize", not_cached)
    res = service.compose_scene(d)
    assert calls == [({"tv"}, True)]  # nothing downloaded for the default
    assert res["screens_off_not_applied"] == ["tv"] and "set_screen" in res["screens_off_not_applied_hint"]
    assert "screens_off" not in res and not [w for w in res.get("warnings", []) if "screen" in w]

    monkeypatch.setattr(localize, "localize", lambda s, cache_dir=None, only=None, cached_only=False: {HF: local})
    res = service.compose_scene(d)  # in the cache: switched off
    assert res["screens_off"] == ["tv"] and "screens_off_not_applied" not in res

    calls.clear()
    monkeypatch.setattr(localize, "localize", not_cached)
    service.set_screen(d, "tv", "off")  # asked for: fetched
    service.compose_scene(d)
    assert calls == [({"tv"}, False)]


def test_off_needs_a_switchable_screen(tmp_path):
    lib = tmp_path / "lib"
    _plain(lib / "tv")  # no lit material, no screen picture
    d = _scene(tmp_path, "office", lib=lib)
    before = (d / "scene.resolved.json").read_text()
    r = service.set_screen(d, "tv", "off")
    assert r["ok"] is False and "no screen that lights up or shows a picture" in r["error"]
    assert (d / "scene.resolved.json").read_text() == before


def test_off_on_a_poster_is_refused(tmp_path):
    from agentic_sim_world_gen.service import _no_switch

    assert "printed pictures" in _no_switch(_rec(screens=[{"kind": "picture", "material": "P", "image": "p.png"}]))
    assert _no_switch(None) and _no_switch(_rec()) is None  # a Hugging Face asset: known only in compose


def test_shown_pictures_and_the_changes_skip_or_show_off(tmp_path):
    from agentic_sim_world_gen.preview.changes import diff, snapshot

    make_signage(tmp_path / "lib" / "sign")
    d = _scene(tmp_path, "office", query="wall monitor", lib=tmp_path / "lib")
    assert service.shown_pictures(service.load(d)) == []  # painted black: no picture to read
    old = snapshot(service.load(d), [])
    service.set_screen(d, "tv", "on")
    assert [x["objects"] for x in service.shown_pictures(service.load(d))] == [["tv"]]
    new = snapshot(service.load(d), [])
    assert any("画面 → つける" in line for line in diff(old, new))
    service.set_screen(d, "tv", None)
    assert any("画面 → 場所の既定" in line for line in diff(new, snapshot(service.load(d), [])))


def test_a_poster_on_a_switched_off_signage_is_still_checked():
    """validate: off skips only the screens; a declared printed picture that stays visible is still checked."""
    from agentic_sim_world_gen.layout.validate import _screen_content

    a = _rec(screens=[{"kind": "screen", "material": "S", "image": "s.png"},
                      {"kind": "picture", "material": "P", "image": "p.png"}])
    o = ObjectSpec(id="m", query="monitor")
    assert [i.kind for i in _screen_content(o, a, "office")] == ["screen_content"]
    a = _rec(screens=[{"kind": "screen", "material": "S", "image": "s.png"}])
    assert _screen_content(o, a, "office") == []


def _plain(folder: Path) -> None:
    """A TV with neither a lit material nor a screen picture: its material and texture are not named for a screen
    (make_tv "none" is a printed screen: ScreenMat, Screen.png)."""
    import shutil

    from pxr import Sdf, UsdShade

    make_tv(folder, "none")
    shutil.copy(folder / "textures" / "Screen.png", folder / "textures" / "Body.png")
    usd = next(folder.glob("*.usd*"))
    stage = Usd.Stage.Open(str(usd))
    mat = UsdShade.Material.Define(stage, "/Sign/_materials/BodyMat")
    sh = UsdShade.Shader.Define(stage, "/Sign/_materials/BodyMat/Image_Texture")
    sh.CreateInput("file", Sdf.ValueTypeNames.Asset).Set("./textures/Body.png")
    stage.RemovePrim("/Sign/_materials/ScreenMat")
    UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath("/Sign/Screen")).Bind(mat)
    stage.GetRootLayer().Save()


def _deco(folder: Path) -> None:
    """A constant purple light behind the panel, like the LG monitor's `deco_lighting_on` (MDL, intensity 10000)."""
    from pxr import Gf, Sdf, UsdShade

    usd = next(folder.glob("*.usd*"))
    stage = Usd.Stage.Open(str(usd))
    UsdShade.Material.Define(stage, "/Sign/_materials/Deco")
    sh = UsdShade.Shader.Define(stage, "/Sign/_materials/Deco/MDL")
    sh.SetSourceAsset("OmniPBR.mdl", "mdl")
    sh.CreateInput("enable_emission", Sdf.ValueTypeNames.Bool).Set(True)
    sh.CreateInput("emissive_intensity", Sdf.ValueTypeNames.Float).Set(10000.0)
    sh.CreateInput("emissive_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.006, 0.002, 0.05))
    ps = UsdShade.Shader.Define(stage, "/Sign/_materials/Deco/Principled_BSDF")
    ps.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.006, 0.002, 0.05))
    stage.GetRootLayer().Save()


def test_the_other_lights_of_a_switched_off_monitor_go_off(tmp_path):
    """Seen in Isaac Sim: the LG monitor stayed purple, its glow is a constant emission behind the panel (deco_lighting_on);
    off switches off every light of the asset, the lit screen's texture is black as well."""
    lib = tmp_path / "lib"
    make_tv(lib / "tv", "mdl")
    _deco(lib / "tv")
    d = _scene(tmp_path, "school", lib=lib)
    res = service.compose_scene(d)
    assert res["screens_off"] == ["tv"] and not [w for w in res.get("warnings", []) if "screen" in w]
    data = _screen(d)
    assert sorted(data["lights_off"]) == ["Deco", "ScreenMat"] and data["screens"]["ScreenMat"]["texture"]
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    mdl = stage.GetPrimAtPath("/World/Objects/tv/Asset/_materials/Deco/MDL")
    assert mdl.GetAttribute("inputs:enable_emission").Get() is False
    assert mdl.GetAttribute("inputs:emissive_intensity").Get() == 0
    ps = stage.GetPrimAtPath("/World/Objects/tv/Asset/_materials/Deco/Principled_BSDF")
    assert tuple(ps.GetAttribute("inputs:emissiveColor").Get()) == (0, 0, 0)
    del stage, mdl, ps  # the layer stays open while a stage holds it
    service.set_screen(d, "tv", "on")  # on: the asset as it is
    service.compose_scene(d)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    assert stage.GetPrimAtPath("/World/Objects/tv/Asset/_materials/Deco/MDL").GetAttribute(
        "inputs:enable_emission").Get() is True


def test_a_light_but_no_lit_screen_goes_off_by_place(tmp_path):
    lib = tmp_path / "lib"
    _plain(lib / "tv")
    _deco(lib / "tv")
    d = _scene(tmp_path, "office", lib=lib)
    res = service.compose_scene(d)
    assert res["screens_off"] == ["tv"] and _screen(d)["lights_off"] == ["Deco"]
    assert not [w for w in res.get("warnings", []) if "screen" in w]


def test_off_on_a_monitor_with_nothing_lit_is_a_note_not_an_error(tmp_path):
    """The NVIDIA monitor (no lit material) with set_screen "off": a Hugging Face asset is known only in compose,
    so the "off" is saved; compose leaves a note and no warning (it wrote an error before)."""
    lib = tmp_path / "lib"
    _plain(lib / "tv")
    d = _scene(tmp_path, "home", lib=lib)
    s = ResolvedScene.load(d / "scene.resolved.json")
    s.obj("tv").screen_on = False  # set_screen refuses a local one (_no_switch)
    s.save(d / "scene.resolved.json")
    res = service.compose_scene(d)
    data = _screen(d)
    assert data["off"] == "screen_on" and "dark already" in data["note"] and "error" not in data
    assert not [w for w in res.get("warnings", []) if "screen" in w]
    d2 = _scene(tmp_path / "b", "school", lib=lib)  # by place: nothing written
    assert "screens_off" not in service.compose_scene(d2) and not _screen(d2)


def test_an_isaac_failure_says_why():
    import subprocess

    from agentic_sim_world_gen.service import _isaac_failure

    proc = subprocess.CompletedProcess([], 2, "", "Warning: in _ReportErrors at line 1\n"
                                       "python: can't open file '/x/scripts/isaac/check_scene.py': [Errno 2]\n")
    err = _isaac_failure(proc)
    assert err["error"].startswith("Isaac Sim exited with 2: python: can't open file") and "check_scene" in err["error"]
    assert "EULA" in _isaac_failure(subprocess.CompletedProcess([], 1, "EULA not accepted", ""))["error"]


def test_a_printed_screen_goes_black(tmp_path):
    """Monitors that rendered purple: NVIDIA's office monitor shows a login picture as its base color
    (no light). Off paints the screen's faces black (screen.printed_screens)."""
    from pxr import Usd as U

    lib = tmp_path / "lib"
    make_tv(lib / "tv", "none")  # ScreenMat: Screen.png as the base color, no emission
    d = _scene(tmp_path, "lab", lib=lib)
    res = service.compose_scene(d)
    assert res["screens_off"] == ["tv"] and not [w for w in res.get("warnings", []) if "screen" in w]
    data = _screen(d)
    base = Path(data["screens"]["ScreenMat"]["base_texture"])
    front, back = _front(base)
    assert max(front) < 1 and max(back) < 1
    stage = U.Stage.Open(str(d / "scene.usda"))
    file = stage.GetPrimAtPath("/World/Objects/tv/Asset/_materials/ScreenMat/Image_Texture").GetAttribute("inputs:file")
    assert file.Get().path == str(base)


def test_uninstance_makes_an_instanced_material_editable():
    """NVIDIA's SimReady monitors are instanced: their materials are proxies that take no override."""
    from pxr import UsdShade

    from agentic_sim_world_gen.usd.screen import printed_screens, uninstance

    stage = Usd.Stage.CreateInMemory()
    UsdShade.Material.Define(stage, "/Proto/Looks/screen_a")
    inst = stage.DefinePrim("/World/Asset/Body")
    inst.GetReferences().AddInternalReference("/Proto")
    inst.SetInstanceable(True)
    mat = "/World/Asset/Body/Looks/screen_a"
    assert stage.GetPrimAtPath(mat).IsInstanceProxy()
    assert uninstance(stage.GetPrimAtPath("/World/Asset")) == 1
    assert not stage.GetPrimAtPath(mat).IsInstanceProxy()
    assert printed_screens(stage.GetPrimAtPath("/World/Asset")) == []  # named for a screen but no texture
