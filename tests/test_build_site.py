import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("build_site", Path(__file__).parents[1] / "scripts" / "build_site.py")
build_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_site)


def test_alerts_become_admonitions():
    out = build_site.convert("> [!WARNING]\n> line one\n> line two\n\ntext\n", ja=True, depth=1)
    assert out.startswith('!!! warning "警告"\n\n    line one\n    line two\n\ntext')


def test_links_stay_in_the_site_or_go_to_github():
    text = "English | [日本語](README.ja.md) | [Project page](https://x/)\n[a](docs/use-ssh.md#2-x) [b](LICENSE) [c](examples/photos) [d](README.ja.md) [e](https://y/)\n"
    out = build_site.convert(text, ja=False, depth=0)
    assert "Project page" not in out
    assert "[a](use-ssh.md#2-x)" in out
    assert "[b](https://github.com/atinfinity/agentic-sim-world-gen/blob/main/LICENSE)" in out
    assert "[c](https://github.com/atinfinity/agentic-sim-world-gen/tree/main/examples/photos)" in out
    assert "[d](ja/index.md)" in out and "[e](https://y/)" in out
    assert "[a](../use-ssh.md#2-x)" in build_site.convert(text, ja=True, depth=1)


def test_readmes_convert():
    root = Path(__file__).parents[1]
    for name, ja in (("README.md", False), ("README.ja.md", True)):
        out = build_site.convert((root / name).read_text(encoding="utf-8"), ja=ja, depth=int(ja))
        assert "[!" not in out and "](docs/" not in out
