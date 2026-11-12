"""Requests to outside services name the tool and where it lives."""
from agentic_sim_world_gen import REPO_URL, UA, USER_AGENT, __version__
from agentic_sim_world_gen.assets.providers.simready_central import SimReadyCentralProvider


def test_user_agent_names_the_tool_and_repo():
    assert USER_AGENT.startswith(f"agentic-sim-world-gen/{__version__}") and REPO_URL in USER_AGENT
    assert SimReadyCentralProvider().session.headers["User-Agent"] == UA["User-Agent"]


def test_search_workers_default():
    from agentic_sim_world_gen import service

    assert service.SEARCH_WORKERS == 4
