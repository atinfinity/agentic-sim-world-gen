"""AGENTIC_SIM_WORLD_GEN_SOURCES: adding to the defaults and showing what is searched."""

import pytest

from agentic_sim_world_gen import service


@pytest.mark.parametrize("value, names", [
    ("simready-central,isaac,polyhaven", ["simready-central", "isaac", "polyhaven"]),
    ("local:/a,simready-central,isaac", ["local:/a", "simready-central", "isaac"]),
    ("+local:/a", ["local:/a", "simready-central", "isaac", "polyhaven"]),
    ("+local:/a, +local:/b", ["local:/a", "local:/b", "simready-central", "isaac", "polyhaven"]),
    ("default,local:/a", ["simready-central", "isaac", "polyhaven", "local:/a"]),
    ("isaac,+local:/a", ["isaac", "local:/a", "simready-central", "polyhaven"]),
    ("local:/a", ["local:/a"]),  # a folder alone stays alone (offline trials use it)
])
def test_source_names(value, names):
    assert service.source_names(value) == names


def test_sources_view_names_left_out_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_SOURCES", f"local:{tmp_path},isaac")
    service.set_provider(None)
    try:
        v = service.sources_view()
        assert v["used"] == [f"local:{tmp_path}", "isaac"]
        assert v["left_out"] == ["simready-central", "polyhaven"] and "+local:" in v["note"]
        monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_SOURCES", f"+local:{tmp_path}")
        service.set_provider(None)
        v = service.sources_view()
        assert "left_out" not in v and v["used"][0] == f"local:{tmp_path}"
    finally:
        service.set_provider(None)

