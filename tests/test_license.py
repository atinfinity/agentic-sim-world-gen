"""License normalization and report."""

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.license import normalize, source_page


def rec(name, lic, uri, credits=()):
    return AssetRecord(id=name, source="fake", uri=uri, name=name, bbox_min=(-0.2, -0.2, 0), bbox_max=(0.2, 0.2, 0.5),
                       license=lic, credits=list(credits))


HF = "https://huggingface.co/datasets/nvidia/simready-assets/resolve/abc/x/y.usd"


def test_normalize():
    assert normalize("CC-BY 4.0") == normalize("cc by 4.0") == normalize("CC-BY-4.0") == "CC-BY-4.0"
    assert normalize("CC0") == "CC0-1.0"
    assert normalize(None) == "UNKNOWN"
    assert source_page(HF) == "https://huggingface.co/datasets/nvidia/simready-assets"


def test_report(tmp_path):
    cat = {"a": [rec("a.usd", "CC-BY 4.0", HF)], "b": [rec("b.usd", "CC0", "/local/b.usd")],
           "c": [rec("c.usd", "CC-BY-NC-4.0", HF)], "d": [rec("d.usd", None, HF)]}
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: cat.get(q, [])})())
    try:
        objs = [{"id": k, "query": k, "position": [0.5 + i, 0.5]} for i, k in enumerate(cat)]
        service.create_scene({"name": "lic", "room": {"width": 5, "depth": 2}, "objects": objs}, tmp_path / "s")
        r = service.license_report(tmp_path / "s")
    finally:
        service.set_provider(None)
    assert r["licenses"] == {"CC-BY-4.0": 1, "CC0-1.0": 1, "CC-BY-NC-4.0": 1, "UNKNOWN": 1}
    assert r["assets"]["CC-BY-4.0"][0]["attribution"] == \
        '"a.usd" by nvidia (https://huggingface.co/datasets/nvidia/simready-assets), CC BY 4.0'
    assert r["assets"]["CC0-1.0"][0]["attribution"] is None
    assert len(r["warnings"]) == 2 and "non-commercial" in r["warnings"][0]
    md = (tmp_path / "s" / "LICENSES.md").read_text()
    assert "## CC BY 4.0 (1)" in md and "Warnings" in md


FONT = {"title": "Noto Sans JP", "author": "Google / Adobe", "url": "https://fonts.google.com/noto/specimen/Noto+Sans+JP",
        "license": "SIL Open Font License 1.1", "license_url": "https://openfontlicense.org/open-font-license-official-text/"}
PHOTO = {"title": "File:Slice of Lemon.jpg", "author": "Garitzko", "license": "Public domain",
         "url": "https://commons.wikimedia.org/wiki/File:Slice_of_Lemon.jpg"}


def test_report_lists_material_credits_once(tmp_path):
    """Fonts and photos used in the textures (asset.json image_credits) are listed once, with the objects using them."""
    cat = {"tea": [rec("tea.usd", "CC0", "/local/tea.usd", [FONT])],
           "lemon": [rec("lemon.usd", "CC0", "/local/lemon.usd", [FONT, PHOTO])]}
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: cat.get(q, [])})())
    try:
        objs = [{"id": k, "query": k, "position": [0.5 + i, 0.5]} for i, k in enumerate(cat)]
        service.create_scene({"name": "cr", "room": {"width": 5, "depth": 2}, "objects": objs}, tmp_path / "s")
        r = service.license_report(tmp_path / "s")
    finally:
        service.set_provider(None)
    assert [c["title"] for c in r["credits"]] == ["Noto Sans JP", "File:Slice of Lemon.jpg"]
    assert r["credits"][0]["used_by"] == ["tea", "lemon"] and r["credits"][1]["used_by"] == ["lemon"]
    assert not r["warnings"]
    md = (tmp_path / "s" / "LICENSES.md").read_text()
    assert ('- "Noto Sans JP" by Google / Adobe (https://fonts.google.com/noto/specimen/Noto+Sans+JP), '
            "[SIL Open Font License 1.1](https://openfontlicense.org/open-font-license-official-text/) — used by tea, lemon") in md
