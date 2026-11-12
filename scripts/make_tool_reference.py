"""Write .claude/skills/world-gen/tools.md: the MCP tools' full docstrings and argument types.

The tool list shows only each docstring's first paragraph; run this after changing a tool:
    .venv/bin/python scripts/make_tool_reference.py
tests/test_mcp_schema.py fails while the file is out of date.
"""

from pathlib import Path

from agentic_sim_world_gen import mcp_schema
from agentic_sim_world_gen.mcp_server import mcp

OUT = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "world-gen" / "tools.md"

if __name__ == "__main__":
    OUT.write_text(mcp_schema.reference(mcp._tool_manager), encoding="utf-8")
    print(OUT, OUT.stat().st_size)
