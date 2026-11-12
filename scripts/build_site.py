"""Project page: put together the English and the Japanese site under build/ and build them with Zensical.

    python scripts/build_site.py            # assemble build/site_en (README.md, docs/en/) and build/site_ja (README.ja.md, docs/)
    python scripts/build_site.py --build    # ... and build: English (zensical.en.toml) -> site/, Japanese (zensical.toml) -> site/ja/
    python scripts/build_site.py --serve    # ... and serve site/ at http://127.0.0.1:8000/agentic-sim-world-gen/

Each site has its top page (from the README) and the four guides in its language: docs/en/*.md and docs/*.md, with
docs/images shared. Nothing under build/ is committed: edit the READMEs and docs/. GitHub alerts (> [!WARNING]) become
admonitions; README links to the guides stay inside the site, links to other files of the repository go to GitHub.
The language switch opens the same page in the other language (docs/javascripts/lang.js).
"""

from __future__ import annotations

import argparse
import functools
import http.server
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
REPO = "https://github.com/atinfinity/agentic-sim-world-gen"
BASE = "/agentic-sim-world-gen/"  # the path of the GitHub Pages site
EN_DIR = ROOT / "build" / "site_en"
JA_DIR = ROOT / "build" / "site_ja"
SHARED = ("images", "javascripts", "videos")  # folders of docs/ copied into both sites

ALERTS = {  # GitHub alert -> admonition type, title in English, title in Japanese
    "NOTE": ("note", "Note", "メモ"),
    "TIP": ("tip", "Tip", "ヒント"),
    "IMPORTANT": ("info", "Important", "重要"),
    "WARNING": ("warning", "Warning", "警告"),
    "CAUTION": ("danger", "Caution", "注意"),
}
LANG_SWITCH = re.compile(r"^(English \| \[日本語\]\(README\.ja\.md\)|\[English\]\(README\.md\) \| 日本語)( \|.*)?$")  # the theme has its own


def _alerts(text: str, ja: bool) -> str:
    out: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^> \[!(\w+)\]\s*$", lines[i])
        if m and m.group(1) in ALERTS:
            kind, en, jp = ALERTS[m.group(1)]
            out.append(f'!!! {kind} "{jp if ja else en}"')
            out.append("")
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                out.append("    " + lines[i][1:].lstrip())
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out) + "\n"


def _page_url(rest: str) -> str:
    """docs-relative path -> URL of that page on a site (getting-started.md -> getting-started/)."""
    return rest[: -len(".md")] + "/" if rest.endswith(".md") and not rest.startswith("images/") else rest


def _link(target: str, ja: bool) -> str:
    """A README link as seen from the top page of the Japanese site (served at ja/) or the English one (the root)."""
    if re.match(r"^[a-z]+:", target) or target.startswith("#"):
        return target
    path, _, frag = target.partition("#")
    frag = "#" + frag if frag else ""
    if path == "README.md":
        return ("../" if ja else "index.md") + frag
    if path == "README.ja.md":
        return ("index.md" if ja else "ja/") + frag
    if path.startswith("docs/en/"):  # English guides
        rest = path[len("docs/en/") :]
        return ("../" + _page_url(rest) if ja else rest) + frag
    if path.startswith("docs/"):  # Japanese guides and the shared images
        rest = path[len("docs/") :]
        if ja or rest.split("/")[0] in SHARED:
            return rest + frag
        return "ja/" + _page_url(rest) + frag
    kind = "tree" if (ROOT / path).is_dir() else "blob"
    return f"{REPO}/{kind}/main/{path}{frag}"


def convert(text: str, *, ja: bool) -> str:
    text = "\n".join(line for line in text.splitlines() if not LANG_SWITCH.match(line))
    text = _alerts(text, ja)
    return re.sub(r"\]\(([^)\s]+)\)", lambda m: "](" + _link(m.group(1), ja) + ")", text)


def assemble() -> None:
    for out, readme, guides, ja in ((EN_DIR, "README.md", DOCS / "en", False), (JA_DIR, "README.ja.md", DOCS, True)):
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        (out / "index.md").write_text(convert((ROOT / readme).read_text(encoding="utf-8"), ja=ja), encoding="utf-8")
        for page in sorted(guides.glob("*.md")):
            text = page.read_text(encoding="utf-8")
            if not ja:  # docs/en/x.md refers to ../images/; in the site the images sit next to it
                text = text.replace("](../images/", "](images/")
            (out / page.name).write_text(text, encoding="utf-8")
        for folder in SHARED:
            shutil.copytree(DOCS / folder, out / folder)
        print(f"{readme} + {guides.relative_to(ROOT)}/*.md -> {out.relative_to(ROOT)}")


def build(strict: bool) -> None:
    shutil.rmtree(ROOT / "site", ignore_errors=True)
    for config in ("zensical.en.toml", "zensical.toml"):  # English first: it writes site/, the Japanese one site/ja/
        subprocess.run(["zensical", "build", "--clean", "-f", config, *(["--strict"] if strict else [])], cwd=ROOT, check=True)


def serve(port: int) -> None:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path: str) -> str:
            path = "/" + path[len(BASE) :] if path.startswith(BASE) else "/__not_found__"
            return super().translate_path(path)

    handler = functools.partial(Handler, directory=str(ROOT / "site"))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"http://127.0.0.1:{port}{BASE}  (Ctrl+C to stop; run again after editing)")
        httpd.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="build both sites into site/")
    ap.add_argument("--strict", action="store_true", help="fail on warnings (with --build)")
    ap.add_argument("--serve", action="store_true", help="build, then serve site/ locally")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    assemble()
    if args.build or args.serve:
        build(args.strict)
    if args.serve:
        try:
            serve(args.port)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
