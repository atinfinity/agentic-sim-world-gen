"""The default run reaches nothing but this machine (tests/conftest.py)."""

import socket

import pytest


def test_elsewhere_is_refused():
    with socket.socket() as s, pytest.raises(OSError, match="do not reach the network"):
        s.connect(("93.184.215.14", 80))


def test_this_machine_is_reached():
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        with socket.socket() as c:
            c.connect(srv.getsockname())


def test_layer_fetch_gives_none(tmp_path):
    from agentic_sim_world_gen.assets import remote_layers

    assert remote_layers._Fetcher(tmp_path).local_path("https://example.com/a.usd") is None
