"""Argument slips forgiven by the MCP server and agentic-sim-world-gen's error messages passed through (no network)."""

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agentic_sim_world_gen import mcp_server
from agentic_sim_world_gen.mcp_server import fix_arguments
from agentic_sim_world_gen.scene.spec import Fill, Taste


def test_aliases():
    assert fix_arguments("add_objects", {"scene_dir": "d", "objects": [1]}) == {"scene_dir": "d", "objs": [1]}
    assert fix_arguments("set_assumptions", {"items": []}) == {"assumptions": []}
    assert fix_arguments("kind_availability", {"kinds": ["toy"]}) == {"queries": ["toy"]}
    assert fix_arguments("kind_availability", {"queries": "toy"}) == {"queries": ["toy"]}
    # the right name wins when both are given
    assert fix_arguments("add_objects", {"objs": [1], "objects": [2]}) == {"objs": [1], "objects": [2]}


def test_set_array_loose_fields():
    a = fix_arguments("set_array", {"scene_dir": "d", "object_id": "s", "count": "2", "rows": "2", "spacing": "0.1"})
    assert a == {"scene_dir": "d", "object_id": "s", "array": {"count": "2", "rows": "2", "spacing": "0.1"}}
    assert fix_arguments("set_array", {"object_id": "s", "array": None}) == {"object_id": "s", "array": None}


def test_double_quoted_string():
    assert fix_arguments("set_tint", {"tint": '"#4a2418"'})["tint"] == "#4a2418"
    assert fix_arguments("set_tint", {"tint": '"a" and "b"'})["tint"] == '"a" and "b"'


def test_models():
    assert Fill(on=["table_w"]).on == "table_w"
    with pytest.raises(ValueError, match="one object per fixture"):
        Fill(on=["a", "b"])
    assert Taste(style="colorful, cheerful toy store").style == ["colorful", "cheerful toy store"]


def call(name, args):
    return anyio.run(lambda: mcp_server.mcp.call_tool(name, args))


def test_errors_reach_the_model(tmp_path):
    # a ValueError from agentic_sim_world_gen (no scene here) is reported with its text, not as a bare crash
    with pytest.raises(ToolError) as e:
        call("set_tint", {"scene_dir": str(tmp_path / "none"), "object_id": "x", "tint": "#ffffff"})
    assert str(e.value) != "Error executing tool set_tint" and "set_tint:" in str(e.value)
    # a schema error names the parameters
    with pytest.raises(ToolError) as e:
        call("set_array", {"scene_dir": "d"})
    assert "set_array takes: scene_dir, object_id, array" in str(e.value)


def test_set_fill_loose_fields_and_remove_rows():
    """set_fill's fields at the top level, set_assumptions' remove given as rows."""
    a = fix_arguments("set_fill", {"scene_dir": "d", "object_id": "can", "on": "shelf", "levels": [0, 1]})
    assert a == {"scene_dir": "d", "object_id": "can", "fill": {"on": "shelf", "levels": [0, 1]}}
    assert fix_arguments("set_fill", {"object_id": "can", "fill": None}) == {"object_id": "can", "fill": None}
    a = fix_arguments("set_assumptions", {"assumptions": [], "remove": [{"item": "register"},
                                                                         {"item": "position", "object": "desk"}, "size"]})
    assert a["remove"] == ["register", "desk:position", "size"]
