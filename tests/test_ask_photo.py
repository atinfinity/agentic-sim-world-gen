"""A vague request gets one invitation to show a photo that is close; a concrete one does not."""

import pytest

from agentic_sim_world_gen.scene.questions import PHOTO_INVITE, plan_questions


@pytest.mark.parametrize("request_", ["ワールドを作って", "リビングを作りたい", "オフィスを作って", "カフェを作って"])
def test_vague_request_invites_a_photo(request_):
    out = plan_questions(request_)
    assert out["ask_photo"]["say"] == PHOTO_INVITE
    assert "ask_photo" in out["hint"]


@pytest.mark.parametrize("request_", [
    "8畳の北欧風のリビング。窓は南、ソファとテレビ",
    "倉庫。ラック 3 列、通路 3 m、ドックは南、20×30 m",
    "10人のオフィス。島型、会議スペースあり、8×10 m",
    "コンビニを作って。おまかせで",  # left to us: do not ask
    "この写真 room.jpg みたいな書斎",  # a photo is already there
    "Pinterest の参考画像みたいなリビング",  # a reference is already named
])
def test_concrete_or_shown_request_does_not(request_):
    assert "ask_photo" not in plan_questions(request_)


def test_asked_once():
    assert "ask_photo" not in plan_questions("リビングを作りたい", known=["reference"])
