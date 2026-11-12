# Contributing

Thanks for your interest. Issues and pull requests are welcome in English or Japanese (日本語でも大丈夫です).
This is a personal project maintained in spare time: there is no promise of a reply, a review or a release date.

## Setup

```bash
uv sync --extra usd --extra mcp --extra dxf --extra ifc --extra video --extra hf --extra dev
uv run pytest -q              # offline tests (about 2 minutes)
uv run pytest -q -m network   # optional: calls the real SimReady Central / Poly Haven APIs
```

Checking a scene in Isaac Sim (`check`) needs an NVIDIA RTX GPU and Isaac Sim 6.1 on Linux; everything else
(search, layout, USD, previews) runs without a GPU. Pull requests that cannot be tested on Isaac Sim are fine:
say so in the description.

If you change an MCP tool's signature or docstring, regenerate the reference:
`uv run python scripts/make_tool_reference.py` (a test checks that `docs/tools.md` is up to date).

## Pull requests

- One topic per pull request. Add or update tests under `tests/`.
- For a change you can see (layout, preview, rendering), attach a screenshot or GIF to the description.
- **Never commit asset files** (USD, textures, MDL, meshes) of a third party, or any material you may not
  redistribute (including non-commercial assets and photos of people). Refer to assets by URL or by a `local:`
  folder instead.
- By submitting a pull request you agree that your contribution is licensed under the Apache License 2.0
  (see LICENSE). There is no CLA and no sign-off (DCO) requirement.

## Reporting problems

Use the issue templates. Please include the request text, the asset sources (`AGENTIC_SIM_WORLD_GEN_SOURCES`),
the version and the OS. Do not attach photos or videos that show people, or screenshots of assets you may not
share. Security problems: see SECURITY.md.
