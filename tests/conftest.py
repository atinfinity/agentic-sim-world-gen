"""Offline defaults for every test."""

import pytest


@pytest.fixture(autouse=True)
def _no_pbr_downloads(tmp_path, monkeypatch):
    """Floor / wall words search the bundled MDL materials only (the CC0 PBR catalog would download textures);
    tests of agentic_sim_world_gen.pbr turn it back on with their own index. The cache goes to a temporary directory."""
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_PBR", "0")
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_CACHE", str(tmp_path / "_cache"))


@pytest.fixture(autouse=True)
def _own_preferences(tmp_path, monkeypatch):
    """Remembered preferences go to a file per test, never ~/.config/agentic-sim-world-gen."""
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_PREFERENCES", str(tmp_path / "_prefs" / "preferences.json"))


@pytest.fixture(autouse=True)
def _no_polyhaven_downloads(monkeypatch):
    """Poly Haven model files are never downloaded by the default test run; tests of
    agentic_sim_world_gen.polyhaven_models fake the site."""
    from agentic_sim_world_gen import polyhaven_models

    def refuse(url, timeout):
        raise polyhaven_models.FetchError(f"tests do not download ({url})")

    monkeypatch.setattr(polyhaven_models, "_get", refuse)


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    """create_scene opens the preview page: never a real browser from a test."""
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_BROWSER", "true")


@pytest.fixture(autouse=True)
def _auto_fix_marker(request, monkeypatch):
    """Tests of what a check reports keep the issue: @pytest.mark.no_auto_fix turns the automatic fixes off."""
    if request.node.get_closest_marker("no_auto_fix"):
        monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_AUTO_FIX", "0")
