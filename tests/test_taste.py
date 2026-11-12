"""Taste check: the odd one out among the chosen assets, with a fake image embedder (no model)."""

import dataclasses
import json

import pytest

from agentic_sim_world_gen import service, taste
from agentic_sim_world_gen.preview import page
from tests.test_layout import rec

# a thumbnail's bytes say its look; the fake embedder turns the look into a vector
LOOK = {"white": [1.0, 0.0, 0.0], "dark": [0.0, 1.0, 0.0], "wood": [0.2, 0.2, 1.0]}


class Fake:
    name = "fake/embedder"

    def __init__(self):
        self.calls = 0

    def images(self, blobs):
        self.calls += len(blobs)
        out = []
        for b in blobs:
            look, _, n = b.decode().partition(":")
            v = list(LOOK[look]) + [0.05 * int(n or 0)]  # a little spread inside a look
            out.append(taste._norm(v))
        return out


@pytest.fixture
def catalog(tmp_path):
    def thumb(name, look, n):
        p = tmp_path / "thumbs" / f"{name}.png"
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(f"{look}:{n}".encode())
        return p.as_uri()

    def r(name, cls, w, d, h, look, n):
        x = rec(name, cls, w, d, h)
        return dataclasses.replace(x, thumbnail_url=thumb(name, look, n))

    cat = {
        "desk": [r("desk_white", "desk", 1.2, 0.6, 0.74, "white", 1), r("desk_dark", "desk", 1.2, 0.6, 0.74, "dark", 1)],
        "office chair": [r("chair_white", "office chair", 0.55, 0.55, 0.85, "white", 2)],
        "bookshelf": [r("shelf_dark", "bookcase", 0.8, 0.35, 1.8, "dark", 2),
                      r("shelf_white", "bookcase", 0.8, 0.35, 1.8, "white", 3)],
        "cabinet": [r("cabinet_white", "cabinet", 0.6, 0.45, 0.7, "white", 4)],
    }
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: cat.get(q, [])})())
    yield cat
    service.set_provider(None)


OBJECTS = [
    {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},
    {"id": "chair", "query": "office chair", "layout": {"in_front_of": "desk"}},
    {"id": "shelf", "query": "bookshelf", "layout": {"against_wall": "west"}},
    {"id": "cabinet", "query": "cabinet", "layout": {"against_wall": "east"}},
]


def make(tmp_path):
    return service.create_scene({"name": "t", "room": {"width": 4, "depth": 4, "height": 2.6}, "objects": OBJECTS},
                                tmp_path / "t")


def index_of(st, oid, name):
    o = next(o for o in st["objects"] if o["id"] == oid)
    return next(i for i, c in enumerate(o["candidates"]) if c["name"] == name)


def test_fits_and_outliers():
    white = [[1, 0, 0.1 * k] for k in range(4)]
    _, low = taste.outliers(white)
    assert low == []  # a coherent set has no outlier
    f, low = taste.outliers(white + [[0, 1, 0]])
    assert low == [4] and f[4] < min(f[:4])
    # never more than a third of the set
    _, low = taste.outliers(white[:2] + [[0, 1, 0], [0, 0.9, 0.1], [0.1, 0, 1], [0, 0.1, 1]])
    assert len(low) <= 2


def test_cache_round_trip(tmp_path):
    c = taste.Cache("some/model", tmp_path)
    assert c.get("https://x/a.png") is None
    c.put("https://x/a.png", [0.25, -1.5, 3.0])
    assert c.get("https://x/a.png") == pytest.approx([0.25, -1.5, 3.0])
    assert taste.Cache("other/model", tmp_path).get("https://x/a.png") is None


def test_check_flags_the_odd_one_with_alternatives(tmp_path, catalog, monkeypatch):
    monkeypatch.setattr(taste, "MIN_KIND", 1)  # the fake catalog has 1-2 of each kind
    monkeypatch.setattr(taste, "MODEL", Fake.name)
    monkeypatch.setattr(taste, "unavailable", lambda: None)
    st = make(tmp_path)
    assert st["taste"]["checked"] is False  # nothing embedded yet: status does not load the model
    e = Fake()
    r = service.check_taste(st["scene_dir"], embedder=e)["taste"]
    assert [o["object"] for o in r["outliers"]] == ["shelf"], r
    o = r["outliers"][0]
    assert o["asset"] == "shelf_dark" and o["fit"] < r["median_fit"]
    assert [a["name"] for a in o["alternatives"]] == ["shelf_white"]
    assert o["alternatives"][0]["index"] == index_of(st, "shelf", "shelf_white")
    assert "hint" in r
    # nothing was swapped
    sel = next(x for x in service.status(st["scene_dir"])["objects"] if x["id"] == "shelf")
    assert sel["candidates"][sel["selected"]]["name"] == "shelf_dark"
    # status reads the cache now, without the embedder
    n = e.calls
    st2 = service.status(st["scene_dir"])
    assert [x["object"] for x in st2["taste"]["outliers"]] == ["shelf"]
    assert e.calls == n
    # the preview lists it, with the better candidate as a clickable card (the user's choice)
    html = (tmp_path / "t" / "preview.html").read_text(encoding="utf-8")
    assert "テイストの注意" in html and "data-obj='shelf'" in html and "shelf_white" in html
    # a second check embeds nothing new
    service.check_taste(st["scene_dir"], embedder=e)
    assert e.calls == n
    # after the user picks the alternative the set is coherent
    service.select_asset(st["scene_dir"], "shelf", o["alternatives"][0]["index"])
    assert service.status(st["scene_dir"])["taste"]["outliers"] == []


def test_skipped_without_the_model(tmp_path, catalog, monkeypatch):
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_TASTE", "0")
    st = make(tmp_path)
    assert "AGENTIC_SIM_WORLD_GEN_TASTE=0" in st["taste"]["skipped"]
    assert "AGENTIC_SIM_WORLD_GEN_TASTE=0" in service.check_taste(st["scene_dir"])["taste"]["skipped"]
    monkeypatch.delenv("AGENTIC_SIM_WORLD_GEN_TASTE")
    real = taste.importlib.util.find_spec
    monkeypatch.setattr(taste.importlib.util, "find_spec", lambda n: None if n == "torch" else real(n))
    why = service.check_taste(st["scene_dir"])["taste"]["skipped"]
    assert "torch" in why and "[taste]" in why


def test_model_not_in_hf_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(taste.importlib.util, "find_spec", lambda n: object())
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    assert "not in the Hugging Face cache" in taste.unavailable()
    (tmp_path / "hub" / ("models--" + taste.MODEL.replace("/", "--")) / "snapshots" / "abc").mkdir(parents=True)
    assert taste.unavailable() is None


def test_few_objects_say_nothing(tmp_path, catalog, monkeypatch):
    monkeypatch.setattr(taste, "unavailable", lambda: None)
    st = service.create_scene({"name": "t", "room": {"width": 4, "depth": 4, "height": 2.6}, "objects": OBJECTS[:2]},
                              tmp_path / "t")
    assert "taste" not in st
    assert service.check_taste(st["scene_dir"], embedder=Fake())["taste"]["outliers"] == []


def test_closeness_only_reorders():
    c = taste.closeness([[1, 0], [0, 1], [0.7, 0.7]], [[1, 0.1], [0.9, 0]])
    assert abs(sum(c)) < 1e-9 and c[0] > c[2] > c[1]
    assert taste.closeness([[1, 0]], []) == [0.0]


def test_render_without_taste_is_unchanged(tmp_path, catalog):
    st = make(tmp_path)
    s = service.load(st["scene_dir"])
    html = page.render(s, tmp_path / "t", [], {}, "0", taste={"skipped": "x"})
    assert "テイストの注意" not in html
    json.dumps(st["taste"])  # status stays JSON


def test_wider_pool_finds_what_the_scene_candidates_miss(tmp_path, catalog, monkeypatch):
    """The scene keeps 6 candidates; check_taste measures against a wider search and may suggest from it."""
    monkeypatch.setattr(taste, "MIN_KIND", 1)  # the fake catalog has 1-2 of each kind
    monkeypatch.setattr(taste, "MODEL", Fake.name)
    monkeypatch.setattr(taste, "unavailable", lambda: None)
    shelf = catalog["bookshelf"]
    darks = [dataclasses.replace(shelf[0], id=f"shelf_dark{k}", name=f"shelf_dark{k}", uri=f"https://example.com/d{k}.usd")
             for k in range(2, 8)]
    catalog["bookshelf"] = [shelf[0], *darks, shelf[1]]  # the white one is 8th: not among the scene's 6
    st = make(tmp_path)
    o = next(x for x in st["objects"] if x["id"] == "shelf")
    assert "shelf_white" not in [c["name"] for c in o["candidates"]]
    r = service.check_taste(st["scene_dir"], embedder=Fake())["taste"]
    assert [x["object"] for x in r["outliers"]] == ["shelf"], r
    a = r["outliers"][0]["alternatives"][0]
    assert a["name"] == "shelf_white" and a["index"] == len(o["candidates"])  # added at the end
    st2 = service.status(st["scene_dir"])
    sel = next(x for x in st2["objects"] if x["id"] == "shelf")
    assert sel["candidates"][sel["selected"]]["name"] == "shelf_dark"  # not swapped
    assert sel["candidates"][a["index"]]["name"] == "shelf_white"
    assert st2["taste"]["outliers"][0]["alternatives"][0]["index"] == a["index"]
    pool = json.loads((tmp_path / "t" / taste.POOL_FILE).read_text())
    assert len(pool["shelf"]["candidates"]) == 8
    # a changed query makes that pool stale: status asks for check_taste again
    service.research_object(st["scene_dir"], "shelf", query="bookshelf", alt_queries=["bookcase"], select=False)
    assert service.status(st["scene_dir"])["taste"]["checked"] is False


def test_too_few_of_its_kind_is_not_flagged():
    """With only 2 bookcases to compare against, the dark one is context but not flagged."""
    v = {"d": [1, 0, 0], "c": [0.9, 0.1, 0], "w": [0, 1, 0], "w2": [0, 0.95, 0.1], "x": [0.95, 0, 0.05]}
    v = {k: taste._norm(x) for k, x in v.items()}
    items = [taste.Item("desk", "desk", 0, [(0, "d", "d", False)]),
             taste.Item("chair", "chair", 0, [(0, "c", "c", False)]),
             taste.Item("cab", "cabinet", 0, [(0, "x", "x", False)]),
             taste.Item("shelf", "shelf", 0, [(0, "w", "w", False), (1, "w2", "w2", False), (2, "d2", "x", False)])]
    assert [o["object"] for o in taste.analyze(items, v, min_kind=1, residual=False)["outliers"]] == ["shelf"]
    assert taste.analyze(items, v, min_kind=6, residual=False)["outliers"] == []


def test_select_a_pool_suggestion_by_uri(tmp_path, catalog, monkeypatch):
    monkeypatch.setattr(taste, "MODEL", Fake.name)
    monkeypatch.setattr(taste, "unavailable", lambda: None)
    st = make(tmp_path)
    service.check_taste(st["scene_dir"], embedder=Fake())
    pool = json.loads((tmp_path / "t" / taste.POOL_FILE).read_text())
    uri = next(c["uri"] for c in pool["desk"]["candidates"] if c["name"] == "desk_dark")
    n = len(service.status(st["scene_dir"])["objects"][0]["candidates"])
    st2 = service.select_asset(st["scene_dir"], "desk", uri=uri)
    d = next(x for x in st2["objects"] if x["id"] == "desk")
    assert d["candidates"][d["selected"]]["name"] == "desk_dark" and len(d["candidates"]) == n
    with pytest.raises(KeyError):
        service.select_asset(st["scene_dir"], "desk", uri="https://example.com/nope.usd")
    # an alternative with index null is rendered as a card the preview can select by uri
    s = service.load(st["scene_dir"])
    t = {"outliers": [{"object": "desk", "label": "desk", "asset": "desk_dark", "fit": 0.1, "median_fit": 0.5,
                       "alternatives": [{"index": None, "uri": uri, "name": "desk_dark", "fit": 0.9}]}]}
    html = page.render(s, tmp_path / "t", [], {}, "0", taste=t)
    assert f"data-uri='{uri}'" in html
