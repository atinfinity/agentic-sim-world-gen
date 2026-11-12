import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("build_site", Path(__file__).parents[1] / "scripts" / "build_site.py")
build_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_site)


def test_alerts_become_admonitions():
    out = build_site.convert("> [!WARNING]\n> line one\n> line two\n\ntext\n", ja=True)
    assert out.startswith('!!! warning "警告"\n\n    line one\n    line two\n\ntext')


def test_links_stay_in_the_site_or_go_to_github():
    text = "English | [日本語](README.ja.md) | [Project page](https://x/)\n[a](docs/use-ssh.md#2-x) [b](LICENSE) [c](examples/photos) [d](README.ja.md) [e](https://y/) [f](docs/images/w.gif)\n"
    en = build_site.convert(text, ja=False)
    assert "Project page" not in en
    assert "[a](ja/use-ssh/#2-x)" in en and "[f](ja/images/w.gif)" in en and "[d](ja/)" in en
    assert "[b](https://github.com/atinfinity/agentic-sim-world-gen/blob/main/LICENSE)" in en
    assert "[c](https://github.com/atinfinity/agentic-sim-world-gen/tree/main/examples/photos)" in en
    assert "[e](https://y/)" in en
    ja = build_site.convert(text, ja=True)
    assert "[a](use-ssh.md#2-x)" in ja and "[f](images/w.gif)" in ja and "[d](index.md)" in ja


def test_readmes_convert():
    root = Path(__file__).parents[1]
    for name, ja in (("README.md", False), ("README.ja.md", True)):
        out = build_site.convert((root / name).read_text(encoding="utf-8"), ja=ja)
        assert "[!" not in out and "](docs/" not in out
