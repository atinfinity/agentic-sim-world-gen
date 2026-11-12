"""The interior-finish example: office_interior.json."""

import json
from pathlib import Path

import pytest

from agentic_sim_world_gen.scene.looks import LIGHTING, fixture_grid
from agentic_sim_world_gen.scene.spec import SceneSpec

EXAMPLES = Path(__file__).parent.parent / "examples"
CEILING = {"ceiling cassette air conditioner", "ceiling air diffuser", "sprinkler head", "smoke detector",
           "emergency light", "ceiling access hatch"}
# half the footprint of the ceiling equipment plus a little room, against the light points
CLEAR = {"ceiling cassette air conditioner": 0.6, "ceiling air diffuser": 0.3, "ceiling access hatch": 0.3}


def _spec(name):
    return SceneSpec.model_validate(json.loads((EXAMPLES / name).read_text()))


@pytest.mark.parametrize("name", ["office_interior.json"])
def test_ceiling_equipment_between_the_lights(name):
    spec = _spec(name)
    room = spec.room
    lights = fixture_grid(room.width, room.depth, LIGHTING[room.lighting]["spacing"])
    found = set()
    for o in spec.objects:
        if o.query not in CEILING:
            continue
        found.add(o.query)
        assert o.layout and o.layout.ceiling, o.id  # z = room height - asset height
        x, y = o.position
        near = min(((x - lx) ** 2 + (y - ly) ** 2) ** 0.5 for lx, ly in lights)
        assert near >= CLEAR.get(o.query, 0.15), (o.id, near)
    assert found == CEILING


@pytest.mark.parametrize("name", ["office_interior.json"])
def test_wall_pieces(name):
    spec = _spec(name)
    for o in spec.objects:
        if o.query in ("wood slat wall panel", "pegboard wall panel"):
            assert o.layout.against_wall and o.layout.gap == 0.0, o.id  # flush with the wall
        if o.query in ("tile backsplash", "emergency exit sign"):
            assert o.layout is None and o.z and o.z > 0.5, o.id  # hung on the wall at a height
    sign = next(o for o in spec.objects if o.query == "emergency exit sign")
    door = next(x for x in spec.room.openings if x.kind == "door")
    assert door.wall == "south" and abs(sign.position[0] - door.offset) < 0.01  # above the entrance


def test_office_interior():
    spec = _spec("office_interior.json")
    assert spec.room.place == "office"
    q = [o.query for o in spec.objects]
    assert q.count("glass partition") == 5 and q.count("glass partition door") == 1
    assert q.count("office partition panel") == 4 and q.count("desk") == 8
    rack = next(o for o in spec.objects if o.id == "rack")
    assert rack.layout.on == "raised0"  # the rack stands on the raised floor

