"""Smaller MCP tool definitions.

The SDK lists each tool with its pydantic JSON schema, and the nested models (Room, Surface, Opening, Site, ...)
made the 52 tools about 240k characters, read again after every compaction. `compact` rewrites what is listed:

- a tool's description is the first paragraph of its docstring; the rest comes from the `tool_help` tool, one
  tool at a time (`section`: reading the whole tools.md, 100 KB, six times in one job filled the context).
  tools.md (`reference`, written by scripts/make_tool_reference.py) stays as the page for people;
- an argument that uses a nested model gets a one-line type (`{wall, offset?: num, ...}`) as its description
  instead of the JSON schema, with the field descriptions of the models in HINTED;
- titles and $defs go.

Only the listing changes: arguments are still validated against the full models (the SDK validates with the
function's own argument model), and validation errors name the bad field plus "<tool> takes: ..." and
the type of each bad argument (`argument_help`).
"""

from __future__ import annotations

import inspect
import json
from typing import Any

HELP_TOOL = "tool_help"
ARG_HELP_CHARS = 700  # one bad argument's type in a validation error
ARG_HELP_MAX = 3

# Models whose fields get a short hint in the one-line type (the ones written in most scenes).
HINTED = {"ObjectSpec", "Layout", "Array", "Fill", "Opening", "Room"}
HINT_CHARS = 60
TYPES = {"string": "str", "number": "num", "integer": "int", "boolean": "bool"}


def _hint(text: str | None) -> str:
    if not text:
        return ""
    text = text.split("\n")[0]
    return text if len(text) <= HINT_CHARS else text[:HINT_CHARS].rsplit(" ", 1)[0] + "…"


def render(schema: dict[str, Any], defs: dict[str, Any], seen: set[str] | None = None, model: str | None = None) -> str:
    """One-line type of a JSON schema; each $ref model is written out once, later only by name."""
    seen = set() if seen is None else seen
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in seen or name not in defs:
            return name
        seen.add(name)
        return render(defs[name], defs, seen, name)
    if "anyOf" in schema:
        opts = [o for o in schema["anyOf"] if o.get("type") != "null"]
        return "|".join(render(o, defs, seen, model) for o in opts) if opts else "null"
    if "enum" in schema:
        return "|".join(map(str, schema["enum"]))
    if "const" in schema:
        return str(schema["const"])
    kind = schema.get("type")
    if kind == "array":
        if "prefixItems" in schema:
            return "[" + ",".join(render(i, defs, seen, model) for i in schema["prefixItems"]) + "]"
        return "[" + render(schema.get("items", {}), defs, seen, model) + "]"
    if kind == "object" or "properties" in schema:
        props = schema.get("properties")
        if props:
            need = set(schema.get("required", []))
            parts = []
            for key, sub in props.items():
                typ = render(sub, defs, seen, model)
                part = key + ("" if key in need else "?")
                if typ not in ("str", ""):
                    part += f": {typ}"
                hint = _hint(sub.get("description")) if model in HINTED else ""
                parts.append(f"{part} ({hint})" if hint else part)
            return "{" + ", ".join(parts) + "}"
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict) and extra:
            return "{str: " + render(extra, defs, seen, model) + "}"
        return "{}"
    return TYPES.get(kind, "")


def compact_parameters(params: dict[str, Any]) -> dict[str, Any]:
    defs = params.get("$defs", {})
    seen: set[str] = set()
    props = {}
    for key, sub in params.get("properties", {}).items():
        sub = {k: v for k, v in sub.items() if k != "title"}
        opts = [o for o in sub.get("anyOf", []) if o.get("type") != "null"]
        if len(opts) == 1 and len(sub["anyOf"]) == 2 and sub.get("default", 0) is None:  # optional: null is implied
            sub = {**{k: v for k, v in sub.items() if k not in ("anyOf", "default")}, **opts[0]}
        if "$ref" in json.dumps(sub):
            text = sub.get("description")
            sub = {"description": (text + " " if text else "") + render(sub, defs, seen)}
        props[key] = sub
    out: dict[str, Any] = {"type": "object", "properties": props}
    if params.get("required"):
        out["required"] = params["required"]
    return out


def split_doc(doc: str | None) -> tuple[str, str]:
    """(summary, details) of a tool docstring: the first paragraph, and the rest."""
    text = inspect.cleandoc(doc or "")
    head, _, rest = text.partition("\n\n")
    return head, rest.strip()


def compact(tools: Any) -> None:
    """Shrink every registered tool's listed description and input schema in place."""
    for tool in tools.list_tools():
        head, rest = split_doc(tool.fn.__doc__)
        tool.description = head + (f'\n(More: {HELP_TOOL}("{tool.name}").)' if rest else "")
        tool.parameters = compact_parameters(tool.parameters)


def reference(tools: Any) -> str:
    """tools.md: every tool's whole docstring and full argument schema, for what the listing leaves out."""
    lines = [
        "# agentic-sim-world-gen MCP tools: details",
        "",
        "Generated by `scripts/make_tool_reference.py` from the tool docstrings in `src/agentic_sim_world_gen/mcp_server.py`;",
        "do not edit by hand. The tool list carries only the first paragraph and a one-line type per argument;",
        "read a tool's section here when you need the rest.",
        "",
    ]
    models: dict[str, Any] = {}
    for tool in sorted(tools.list_tools(), key=lambda t: t.name):
        part, used = _tool_lines(tool)
        lines += part
        models.update(used)
    lines += ["# Models", "", "The nested argument types named above.", ""]
    for name in sorted(models):
        lines += _model_lines(name, models[name])
    return "\n".join(lines)


def _fields(schema: dict[str, Any]) -> list[str]:
    need = set(schema.get("required", []))
    out = []
    for key, sub in schema.get("properties", {}).items():
        typ = render(sub, {}, set())
        text = (sub.get("description") or "").replace("\n", " ")
        out.append(f"- `{key}`{'' if key in need else '?'}: {typ}" + (f" — {text}" if text else ""))
    return out


def _tool_lines(tool: Any) -> tuple[list[str], dict[str, Any]]:
    """One tool's section (whole docstring, argument types) and the nested models its arguments name."""
    head, rest = split_doc(tool.fn.__doc__)
    lines = [f"## {tool.name}", "", head, ""]
    if rest:
        lines += [rest, ""]
    params = tool.fn_metadata.arg_model.model_json_schema(by_alias=True)
    defs = params.get("$defs", {})
    lines += _fields(params) + [""]
    return lines, {k: v for k, v in defs.items() if k in _refs(params, defs)}


def _model_lines(name: str, model: dict[str, Any]) -> list[str]:
    lines = [f"## {name}", ""]
    if model.get("description"):
        lines += [inspect.cleandoc(model["description"]), ""]
    return lines + _fields(model) + [""]


def section(tools: Any, name: str, model: str | None = None) -> str:
    """What tool_help returns: one tool's section of the reference, or one of its nested models
    (`model`). A few thousand characters instead of the whole tools.md."""
    by_name = {t.name: t for t in tools.list_tools()}
    name = name.removeprefix("mcp__agentic-sim-world-gen__")
    if name not in by_name:
        import difflib

        near = difflib.get_close_matches(name, by_name, n=5, cutoff=0.5)
        raise ValueError(f"no tool '{name}'" + (f"; did you mean {', '.join(near)}" if near else "")
                         + f". Tools: {', '.join(sorted(by_name))}")
    lines, models = _tool_lines(by_name[name])
    if model is not None:
        if model not in models:
            raise ValueError(f"{name} has no model '{model}'; its models: {', '.join(sorted(models)) or 'none'}")
        return "\n".join(_model_lines(model, models[model])).strip()
    if models:
        lines.append(f"Models named above (fields: {HELP_TOOL}(\"{name}\", model=...)): {', '.join(sorted(models))}")
    return "\n".join(lines).strip()


def argument_help(tool: Any, args: list[str]) -> str:
    """For a validation error: the type of each bad argument, so the next call can be right without
    reading the reference."""
    params = tool.fn_metadata.arg_model.model_json_schema(by_alias=True)
    defs, props = params.get("$defs", {}), params.get("properties", {})
    out = []
    for key in list(dict.fromkeys(args))[:ARG_HELP_MAX]:
        if key not in props:
            continue
        typ = render(props[key], defs, set())
        if len(typ) > ARG_HELP_CHARS:
            typ = typ[:ARG_HELP_CHARS].rsplit(",", 1)[0] + ", …}"
        text = _hint(props[key].get("description"))
        out.append(f"{key}: {typ or 'str'}" + (f" — {text}" if text else ""))
    return "\n".join(out)


def _refs(params: dict[str, Any], defs: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    todo = [params.get("properties", {})]
    while todo:
        node = todo.pop()
        text = json.dumps(node)
        for name in defs:
            if f'"#/$defs/{name}"' in text and name not in found:
                found.add(name)
                todo.append(defs[name])
    return found
