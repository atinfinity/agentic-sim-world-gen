"""Assets made for another line of work go after the others for the room's place."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.domain import domains_of, off_place
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.scene.spec import ObjectSpec


def rec(name, cls, caption, tags):
    return AssetRecord(id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
                       bbox_min=(-0.5, -1.0, 0.0), bbox_max=(0.5, 1.0, 0.6), semantic_class=cls, caption=caption,
                       tags=tags, license="CC-BY-4.0")


HOSPITAL_BED = rec("sm_bed_hospital", "hospital bed", "White wheeled hospital bed with split safety rails",
                   ["hospital bed", "medical bed", "patient bed", "bed"])
HOME_BED = rec("bed_wood", "bed", "Light wood single bed with headboard", ["bed", "single bed", "bedroom"])
# SimReady's sm_chair_hospital_b01 is a plain task chair: the file name alone says nothing
TASK_CHAIR = rec("sm_chair_hospital_b01", "chair", "Teal swivel task chair", ["office chair", "task chair"])
# a local diaper stack table is tagged "pallet display"
DIAPER_STACK = rec("diaper_stack_platform", "fixture", "紙おむつの山積み台 1200mm（什器）の 3D モデル。",
                   ["fixture", "diaper stack platform", "山積み", "店舗什器", "stack platform", "pallet display",
                    "diaper display", "drugstore"])
# SimReady's robot test rig is class "articulation test bench": not a seat for a shoe store
TEST_BENCH = rec("articulation_test_bench", "articulation test bench", "Gray articulation test bench with mechanical controls",
                 ["articulation test bench", "test rig", "robotics", "manipulation", "control panel"])
RADIATOR = rec("radiator", "radiator", "Rectangular automotive cooling radiator", ["radiator", "engine cooling"])

RC_CAR_BOX = rec("toy_car_box", "box", "ラジコンカーの箱の 3D モデル。",
                 ["box", "toy car", "toy vehicle", "rc car", "toy box", "boxed toy", "toy store"])


def test_domains_of_whole_words():
    assert domains_of("Silver utility cart with push handle") == set()  # cart is not car
    assert domains_of("car radiator, engine cooling") == {"automotive"}
    assert domains_of("Blue heavy-duty steel shelving") == set()
    assert domains_of("heavy duty, pallet rack") == {"industrial"}
    assert domains_of("double-sided retail gondola shelving") == {"retail"}
    # toy cars are toys (the RC car box went after the drink cases in a toy store)
    assert domains_of("toy car, rc car, toy vehicle, die-cast car, mini car, remote control car") == set()
    assert domains_of("toy car | car battery") == {"automotive"}


def test_off_place():
    assert off_place(TEST_BENCH, "shoe_store", ["bench"]) == "lab"
    assert off_place(TEST_BENCH, "lab", ["bench"]) is None
    assert off_place(HOSPITAL_BED, "home", ["bed"]) == "medical"
    assert off_place(HOSPITAL_BED, "hospital", ["bed"]) is None
    assert off_place(HOSPITAL_BED, "home", ["hospital bed"]) is None  # asked for
    assert off_place(HOSPITAL_BED, None, ["bed"]) is None
    assert off_place(HOSPITAL_BED, "outdoor", ["bed"]) is None
    assert off_place(TASK_CHAIR, "home", ["chair"]) is None
    assert off_place(RADIATOR, "home", ["radiator heater"]) == "automotive"
    assert off_place(DIAPER_STACK, "drugstore", ["diaper stack display table"]) is None
    assert off_place(DIAPER_STACK, "supermarket", ["stack display table"]) is None
    assert off_place(DIAPER_STACK, "home", ["table"]) is not None  # still a shop fixture in a home
    assert off_place(RADIATOR, "home_center", ["radiator"]) is None
    assert off_place(RC_CAR_BOX, "toy_store", ["toy box"]) is None
    assert off_place(RC_CAR_BOX, "home", ["toy box"]) is None


class Fake:
    name = "fake"

    def search(self, query, *, limit=10, size=None):
        return [HOSPITAL_BED, HOME_BED][:limit]


@pytest.fixture
def fake():
    service.set_provider(Fake())
    yield
    service.set_provider(None)


def test_candidates_put_off_place_last(fake):
    o = ObjectSpec(id="bed", query="bed")
    home = service._find_candidates(o, 4, "home")
    assert [c["name"] for c in home] == ["bed_wood", "sm_bed_hospital"]
    assert home[1]["match"]["off_place"] == "medical" and "off_place" not in home[0]["match"]
    assert service._one_line({**home[1], "index": 1}).endswith("off-place:medical")
    # kept, not dropped, and the order is the plain match order elsewhere
    assert [c["name"] for c in service._find_candidates(o, 4, "hospital")] == ["sm_bed_hospital", "bed_wood"]
    assert [c["name"] for c in service._find_candidates(o, 4)] == ["sm_bed_hospital", "bed_wood"]


GROW_RACK = rec("strawberry_growing_rack", "rack", "Strawberry growing rack, three tiers with LED bars",
                ["growing rack", "vertical farm", "plant factory", "strawberry"])


def test_agriculture():
    assert domains_of("plant factory, growing rack") == {"agriculture"}
    assert off_place(GROW_RACK, "home", ["bookshelf"]) == "agriculture"
    assert off_place(GROW_RACK, "convenience_store", ["shelf"]) == "agriculture"
    assert off_place(GROW_RACK, "home_center", ["shelf"]) is None  # garden goods
    assert off_place(GROW_RACK, "plant_factory", ["rack"]) is None
    assert off_place(HOSPITAL_BED, "plant_factory", ["bed"]) == "medical"
