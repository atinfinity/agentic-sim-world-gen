"""The listed MCP tool definitions stay small (no network)."""

import json
from pathlib import Path

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agentic_sim_world_gen import mcp_schema
from agentic_sim_world_gen.mcp_server import mcp

ROOT = Path(__file__).resolve().parents[1]
TOTAL = 75_000  # characters of every listed description + input schema
PER_TOOL = {"create_scene": 13_000, "set_room": 7_000}
OTHER = 5_000
DESCRIPTION = 1_700  # set_room's summary is the longest


def listed():
    return anyio.run(mcp.list_tools)


def size(tool):
    return len(tool.description or "") + len(json.dumps(tool.input_schema, ensure_ascii=False))


def test_tool_list_is_small():
    tools = listed()
    total = sum(size(t) for t in tools)
    assert total <= TOTAL, f"tool list {total} chars > {TOTAL}: put details in the docstring after a blank line"
    for t in tools:
        assert size(t) <= PER_TOOL.get(t.name, OTHER), t.name
        assert len(t.description) <= DESCRIPTION, t.name
        assert "$defs" not in t.input_schema and not any("title" in p for p in t.input_schema["properties"].values()), t.name


def test_details_point_to_reference():
    tools = {t.name: t for t in listed()}
    assert 'tool_help("set_room")' in tools["set_room"].description  # one tool's notes, not all of tools.md
    assert "tool_help" not in tools["get_scene"].description  # a short docstring is listed whole
    # nested models become one-line types with hints for the common ones
    spec = tools["create_scene"].input_schema["properties"]["spec"]["description"]
    assert "against_wall?" in spec and "(object id; stand at its front" in spec


def test_reference_is_up_to_date():
    text = (ROOT / ".claude/skills/world-gen/tools.md").read_text(encoding="utf-8")
    assert text == mcp_schema.reference(mcp._tool_manager), "run scripts/make_tool_reference.py"
    assert "## fit_photos" in text and "corner:<sw|se|nw|ne>" in text and "## Surface" in text


def test_validation_still_uses_full_models():
    # the listing lost the nested schema, but a wrong nested value is still refused with its field named
    with pytest.raises(ToolError) as e:
        anyio.run(lambda: mcp.call_tool("create_scene", {"spec": {"room": {"width": "wide"}, "objects": []}}))
    assert "width" in str(e.value) and "create_scene takes: spec" in str(e.value)


def test_tool_help_is_one_section():
    # tools.md is 110 KB and was read 6 times in one photo job; tool_help returns one tool
    text = anyio.run(lambda: mcp.call_tool("tool_help", {"name": "set_array"})).content[0].text
    assert text.startswith("## set_array") and "back_to_back" in text and "## set_room" not in text
    assert 'model=...)): Array' in text
    fields = mcp_schema.section(mcp._tool_manager, "mcp__agentic-sim-world-gen__set_array", "Array")
    assert fields.startswith("## Array") and "- `rows`?: int" in fields
    sizes = [len(mcp_schema.section(mcp._tool_manager, t.name)) for t in mcp._tool_manager.list_tools()]
    assert max(sizes) < 12_000 and sorted(sizes)[len(sizes) // 2] < 1_500
    with pytest.raises(ValueError, match="did you mean .*set_array"):
        mcp_schema.section(mcp._tool_manager, "set_arrays")
    with pytest.raises(ValueError, match="its models: Array"):
        mcp_schema.section(mcp._tool_manager, "set_array", "Room")


def test_validation_error_says_the_right_shape():
    # the error itself carries the bad argument's type, so the next call needs no reference
    with pytest.raises(ToolError) as e:
        anyio.run(lambda: mcp.call_tool("set_array", {"scene_dir": "x", "object_id": "a", "array": {"rows": "many"}}))
    text = str(e.value)
    assert "array: {count?: int" in text and "rows?: int" in text and 'tool_help("set_array")' in text
    assert "errors.pydantic.dev" not in text
    assert len(text) < 1_500


def test_render():
    defs = {"P": {"properties": {"x": {"type": "number"}, "n": {"type": "string"}}, "required": ["x"]}}
    s = {"anyOf": [{"type": "array", "items": {"$ref": "#/$defs/P"}}, {"type": "null"}]}
    assert mcp_schema.render(s, defs) == "[{x: num, n?}]"
    assert mcp_schema.render({"enum": ["a", "b"]}, {}) == "a|b"
