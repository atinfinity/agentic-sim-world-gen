# Contributing

Thanks for your interest. Issues and pull requests are welcome in English or Japanese (日本語でも大丈夫です).
This is a personal project maintained in spare time: there is no promise of a reply, a review or a release date.

## Setup

```bash
uv sync --extra usd --extra mcp --extra dxf --extra ifc --extra video --extra hf --extra dev
uv run pytest -q              # offline tests (about 2 minutes)
uv run pytest -q -m network   # optional: calls the real SimReady Central / Poly Haven APIs
```

CI runs the tests twice: with the extras the README installs (`usd`, `hf`, `mcp`, `dev`) and with all but `taste`.
A test that needs another extra skips without it (`pytest.importorskip`).

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

## Project page

The project page (<https://atinfinity.github.io/agentic-sim-world-gen/>) is built with [Zensical](https://zensical.org/)
and published by `.github/workflows/pages.yml` on every push to `main`. There are two sites with the same pages: English
(`zensical.en.toml`: `README.md` and the guides in `docs/en/`) and Japanese under `/ja/` (`zensical.toml`:
`README.ja.md` and the guides in `docs/`). `scripts/build_site.py` puts them together under `build/`, so edit the
READMEs and `docs/`. When you change a guide, change both languages. To look at it locally:

```bash
uv pip install zensical==0.1.1
python scripts/build_site.py --serve   # http://127.0.0.1:8000/agentic-sim-world-gen/
```

## GitHub Actions

The actions used by `.github/workflows/` are pinned to commits in `.github/workflows/actions.lock`
([workflow lockfile](https://gh.io/actions-lockfile)), made by the GitHub CLI extension
[gh-actions-lock](https://github.com/github/gh-actions-lock). Do not edit it by hand: after you add or change a
`uses:` (or add a workflow), run

```bash
gh extension install github/gh-actions-lock   # once
gh actions-lock            # pins full version tags (@v7 -> @v7.0.1) and updates actions.lock
gh actions-lock --verify   # check
```

and commit the workflows together with `actions.lock`.
