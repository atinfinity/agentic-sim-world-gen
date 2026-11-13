# Contributing

Thanks for your interest. Issues and pull requests are welcome in English or Japanese (日本語でも大丈夫です).
This is a personal project maintained in spare time: there is no promise of a reply, a review or a release date.

The guide for developers is on the project page and in the repository:
[docs/en/contributing.md](docs/en/contributing.md) (日本語：[docs/contributing.md](docs/contributing.md)).
It covers the setup and tests, where things are in the code, pull requests, reporting problems, the project page
and GitHub Actions.

In short:

- `uv sync --extra usd --extra mcp --extra dxf --extra ifc --extra video --extra hf --extra dev`, then `uv run pytest -q`.
- One topic per pull request, with tests; a screenshot or GIF for a change you can see.
- **Never commit asset files** of a third party, or any material you may not redistribute.
- Contributions are licensed under the Apache License 2.0 (see LICENSE). No CLA, no sign-off.
