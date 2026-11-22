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


@pytest.fixture(autouse=True)
def _no_knee_scans(monkeypatch):
    """A chair in front of a desk has both measured before solving, which fetches their USD: tests' fake assets
    have none. Tests of the tuck put the grids in themselves."""
    from agentic_sim_world_gen.layout import knee

    monkeypatch.setattr(knee, "scan", lambda uri, cache_dir: None)


@pytest.fixture(autouse=True)
def _no_layer_downloads(request, monkeypatch):
    """The shelf and hook scans (assets.shelves.scan) fetch the selected asset's USD: the tests' fake assets
    point at example.com, which the default run reached over a hundred times. Refused here as a failed fetch, before
    _no_network (local_path gives None, as the 404 did); tests of the fetcher give it their own session, and
    network tests keep it."""
    if request.node.get_closest_marker("network"):
        return
    from agentic_sim_world_gen.assets import remote_layers

    class Refuse:
        def get(self, url, timeout=None):
            raise OSError(f"tests do not download ({url})")

    monkeypatch.setattr(remote_layers._Fetcher, "session", lambda self: Refuse())


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """Nothing but this machine (the preview server, a live Isaac Sim) is reached from the default run:
    a connection elsewhere fails the test instead of passing it on a 404. Network tests keep the network."""
    if request.node.get_closest_marker("network"):
        return
    import ipaddress
    import socket

    connect = socket.socket.connect

    def local_only(self, address):
        host = address[0] if isinstance(address, tuple) else None
        if host is not None:
            try:
                local = ipaddress.ip_address(host.split("%")[0]).is_loopback
            except ValueError:
                local = host == "localhost"
            if not local:
                raise OSError(f"tests do not reach the network ({host})")
        return connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)
