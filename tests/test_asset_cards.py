"""Asset cards: the index, and what ranking, the front and the candidates take from it."""

import importlib.util
import json
from pathlib import Path

import pytest

from agentic_sim_world_gen.assets import cards
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.assets.rerank import score
from agentic_sim_world_gen.layout.frame import asset_front_deg
from agentic_sim_world_gen.scene.spec import record_to_dict

ROOT = Path(__file__).resolve().parents[1]


def rec(name, cls=None, caption=None, tags=()):
    return AssetRecord(id=name, source="t", uri=f"https://x/Assets/{name.title()}/{name}.usd?v=1", name=f"{name}.usd",
                       bbox_min=(0, 0, 0), bbox_max=(0.4, 0.4, 1.6), semantic_class=cls, caption=caption, tags=list(tags))


@pytest.fixture
def card_file(tmp_path, monkeypatch):
    f = tmp_path / "cards.jsonl"
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_ASSET_CARDS", str(f))

    def write(*rows):
        f.write_text("".join(json.dumps(r) + "\n" for r in rows))
        cards._load.cache_clear()
    return write


def build():
    spec = importlib.util.spec_from_file_location("build_asset_cards", ROOT / "scripts" / "build_asset_cards.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_key_is_the_same_wherever_the_asset_is():
    a = "https://s3/Assets/Isaac/6.1/Isaac/Props/Pallet/Wooden_Pallet_A01/SM_Wooden_Pallet_A01_01.usd?versionId=3"
    assert cards.key(a) == cards.key("/data/isaac/Isaac/Props/Pallet/Wooden_Pallet_A01/SM_Wooden_Pallet_A01_01.usd")
    assert cards.key(a) == "wooden_pallet_a01/sm_wooden_pallet_a01_01"


def test_no_card_changes_nothing(card_file):
    lamp = rec("lamp_a", "lamp", "Tall white lamp")
    before = score(lamp, "lamp")
    card_file({"key": "other/other", "kind": "robot"})
    assert score(lamp, "lamp").score == before.score
    card_file({"key": cards.key(lamp.uri), "physics": {"stable": True}})  # a card without a kind
    assert score(lamp, "lamp").score == before.score


def test_card_kind_corrects_the_label_both_ways(card_file):
    robot = rec("uv_tower", "lamp", "White lamp tower on a wheeled base")  # labelled a lamp
    stand = rec("stand_b", "stand", "Slim black pole with a shade")  # a floor lamp nobody labelled
    desk_lamp = rec("lamp_c", "lamp", "Black lamp")
    assert not score(robot, "lamp").weak and score(stand, "floor lamp").weak
    card_file({"key": cards.key(robot.uri), "kind": "UV disinfection robot"},
              {"key": cards.key(stand.uri), "kind": "floor lamp", "also": ["standing lamp"]},
              {"key": cards.key(desk_lamp.uri), "kind": "desk lamp"})
    m = score(robot, "lamp")
    assert m.weak and "card" in m.matched[0]
    assert not score(robot, "disinfection robot").weak and not score(robot, "robot").weak
    m = score(stand, "floor lamp")
    assert not m.weak and m.matched == ["card: a floor lamp"]
    assert not score(stand, "lamp").weak and not score(stand, "standing lamp").weak
    assert score(desk_lamp, "floor lamp").weak and not score(desk_lamp, "lamp").weak  # another kind of lamp


def test_colors_and_materials_count_as_words(card_file):
    shelf = rec("shelf_a", "bookshelf", "Bookshelf with five boards")
    plain = score(shelf, "wooden bookshelf").score
    card_file({"key": cards.key(shelf.uri), "materials": ["wooden"], "colors": ["brown"]})
    assert score(shelf, "wooden bookshelf").score > plain
    assert score(shelf, "metal bookshelf").score == plain


def test_front_from_the_card_after_the_known_ones(card_file):
    a, rack = rec("cabinet_a", "cabinet"), rec("sm_rack_x", "rack")
    assert asset_front_deg(a) == 0
    card_file({"key": cards.key(a.uri), "front_deg": 270}, {"key": cards.key(rack.uri), "front_deg": 90})
    assert asset_front_deg(a) == 270
    assert asset_front_deg(rack) == 270  # KNOWN_FRONT_PATTERNS, verified in Isaac Sim, stays
    a.front_deg = 180.0
    assert asset_front_deg(a) == 180


def test_physics_issue():
    assert cards.physics_issue(None) is None and cards.physics_issue({"kind": "x"}) is None
    assert cards.physics_issue({"physics": {"stable": True, "sink_m": 0.0, "colliders": 3, "resolved": True}}) is None
    assert cards.physics_issue({"physics": {"stable": False, "moved_m": 0.31}}) == "moves 0.31 m alone"
    assert cards.physics_issue({"physics": {"sink_m": 0.04, "stable": True}}) == "sinks 4 cm"
    assert cards.physics_issue({"physics": {"resolved": True, "colliders": 0}}) == "no collider"
    # a curtain on a ceiling rail falls over on the floor of the drop test: not its fault
    hung = {"mount": "ceiling", "physics": {"stable": False, "moved_m": 0.54, "sink_m": 1.9, "colliders": 1}}
    assert cards.physics_issue(hung) is None
    assert cards.physics_issue({**hung, "mount": "floor"}) == "sinks 190 cm"
    assert cards.physics_issue({"mount": "wall", "physics": {"colliders": 0}}) == "no collider"
    assert cards.brief({"kind": "pallet", "physics": {"exploded": True}}) == {"kind": "pallet", "issue": "blows up alone"}


def test_a_failed_drop_goes_behind_and_shows(card_file, monkeypatch):
    from agentic_sim_world_gen import service
    from agentic_sim_world_gen.scene.spec import ObjectSpec

    a, b = rec("desk_a", "desk", "Desk"), rec("desk_b", "desk", "Desk")

    class P:
        def search(self, q, limit=10, size=None):
            return [a, b]

    monkeypatch.setattr(service, "get_provider", lambda: P())
    card_file({"key": cards.key(a.uri), "physics": {"stable": False, "moved_m": 0.4}})
    got = service._find_candidates(ObjectSpec(id="d", query="desk"), 5)
    assert [c["id"] for c in got] == ["desk_b", "desk_a"]
    assert got[1]["match"]["card"] == {"issue": "moves 0.40 m alone"} and "card" not in got[0]["match"]
    assert "card:moves 0.40 m alone" in service._one_line({**got[1], "index": 1})


def test_build_script(tmp_path, card_file):
    b = build()
    assert b.physics_of({"resolved": True, "rigid_bodies": 2, "colliders": 5, "stable": False,
                         "max_displacement_m": 0.12, "settled_min_z": -0.043}) == {
        "resolved": True, "bodies": 2, "colliders": 5, "stable": False, "moved_m": 0.12, "sink_m": 0.043}
    rows = [{"key": cards.key(r.uri), "record": record_to_dict(r)} for r in (rec("a"), rec("b"), rec("c"))]
    scene = b.drop_scene(rows)
    pos = [o.position for o in scene.spec.objects]
    assert len({tuple(p[:2]) for p in pos}) == 3 and all(1 < p[0] < scene.spec.room.width - 1 for p in pos)
    assert scene.placements[2].asset.name == "c.usd"
    assert b.check_look({"key": "k", "kind": "desk", "front_deg": 45, "colour": []}) == [
        "unknown field colour", "front_deg: a multiple of 90"]
    (tmp_path / "drop.jsonl").write_text(json.dumps({"key": "x/a", "physics": {"stable": True}, "by": {"drop": "d"}}) + "\n")
    (tmp_path / "look.jsonl").write_text(json.dumps({"key": "x/a", "kind": "desk", "colors": ["white"]}) + "\n")
    out = tmp_path / "out.jsonl.gz"
    b.merge(type("A", (), {"out": tmp_path, "cards": str(out), "by": "tester"}))
    card = cards.read(out)["x/a"]
    assert card["kind"] == "desk" and card["physics"] == {"stable": True}
    assert card["by"]["drop"] == "d" and card["by"]["look"].startswith("tester, ")
