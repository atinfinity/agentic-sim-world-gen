"""A zone the room does not have is refused: objects, layout options, in_zone items and the SDG camera."""

import json
from pathlib import Path

import pytest
from test_plan import l_room
from test_room_site import ROOM as SITE_ROOM

from agentic_sim_world_gen.scene.spec import SceneSpec, check_zones, drop_unknown_zones

EX = Path(__file__).resolve().parents[1] / "examples"


def _plan(zone: str | None = None) -> SceneSpec:  # an L-shaped flat: zones "ldk" and "bed"
    return SceneSpec.model_validate({"room": l_room(), "objects": [{"id": "desk", "query": "desk", "zone": zone}]})


def _site(zone: str | None = "outside") -> SceneSpec:  # a shop with a road, sidewalk and parking around it
    return SceneSpec.model_validate({"room": SITE_ROOM, "objects": [
        {"id": "vending", "query": "vending machine", "zone": zone}]})


def _yard(zone: str | None = None) -> SceneSpec:  # an outdoor site: its areas are the zones
    d = json.loads((EX / "outdoor_yard.json").read_text(encoding="utf-8"))
    if zone is not None:
        d["objects"][0]["zone"] = zone
    return SceneSpec.model_validate(d)  # loading never refuses a zone: an old scene still opens


def test_known_zones_are_kept():
    s = _plan("bed")
    check_zones(s)
    assert s.room.zone_ids() == {"ldk", "bed"}
    site = _site()
    check_zones(site)
    assert site.room.zone_ids() == {"road", "walk", "parking", "outside"}
    check_zones(_site("walk"))  # an area of the site
    yard = _yard()
    check_zones(yard)
    assert yard.room.zone_ids() == {"road", "walk", "drive", "apron", "lawn"}
    check_zones(_plan(""))  # "" is no zone


def test_unknown_object_zone_is_refused():
    with pytest.raises(ValueError, match=r"zone = 'x'; the room's zones are \['bed', 'ldk'\]"):
        check_zones(_plan("x"))
    with pytest.raises(ValueError, match="unknown zone"):  # "outside" only with a site around the room
        check_zones(_plan("outside"))
    with pytest.raises(ValueError, match="unknown zone"):  # an outdoor site has its areas, not "outside"
        check_zones(_yard("outside"))


def test_message_says_what_the_room_is():
    rect = SceneSpec.model_validate({"room": {"width": 4, "depth": 3},
                                     "objects": [{"id": "desk", "query": "desk", "zone": "office"}]})
    with pytest.raises(ValueError, match=r"objects\[desk\]\.zone = 'office'; the room has no zones \(a rectangle"):
        check_zones(rect)
    yard = _yard("garden")
    yard.room.areas = []
    with pytest.raises(ValueError, match=r"'garden';.*; the site has no areas"):
        check_zones(yard)


def test_in_zone_item_and_sdg_camera():
    s = _plan()
    s.requirements = [{"text": "寝室に置く", "checks": [{"kind": "in_zone", "object": "desk", "target": "bedroom"}]}]
    s = SceneSpec.model_validate(s.model_dump())
    with pytest.raises(ValueError, match=r"requirements\[寝室に置く\] in_zone = 'bedroom'"):
        check_zones(s)
    s.requirements[0].checks[0].target = None  # no target could only come out unknown
    with pytest.raises(ValueError, match=r"in_zone has no target"):
        check_zones(s)
    s.requirements[0].checks[0].target = "bed"
    check_zones(s)
    s = SceneSpec.model_validate({**s.model_dump(), "sdg": {"camera": {"zone": "hall"}}})
    with pytest.raises(ValueError, match=r"sdg\.camera\.zone = 'hall'"):
        check_zones(s)
    site = SceneSpec.model_validate({**_site().model_dump(), "sdg": {"camera": {"zone": "outside"}}})
    with pytest.raises(ValueError, match=r"sdg\.camera\.zone = 'outside'"):  # the camera stays in the room's polygons
        check_zones(site)


def test_drop_unknown_zones():
    s = _site()
    s.room.site = None  # set_room(site=false)
    assert drop_unknown_zones(s) == ["vending: outside"]
    assert s.objects[0].zone is None
    check_zones(s)
