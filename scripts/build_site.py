"""Project page: write the top pages from the READMEs and build the two sites with Zensical.

    python scripts/build_site.py            # README.ja.md -> docs/index.md, README.md -> build/site_en/index.md
    python scripts/build_site.py --build    # ... and build: English (zensical.en.toml) -> site/, Japanese -> site/ja/
    python scripts/build_site.py --serve    # ... and serve site/ at http://127.0.0.1:8000/agentic-sim-world-gen/

The English site is the top page only, so its menu stays English; the Japanese one (zensical.toml) has the top page
and the guides in docs/. The written top pages are not committed: edit the READMEs. GitHub alerts (> [!WARNING])
become admonitions; links to docs/ stay inside the site, links to other files of the repository go to GitHub.
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
REPO = "https://github.com/atinfinity/agentic-sim-world-gen"
BASE = "/agentic-sim-world-gen/"  # the path of the GitHub Pages site
EN_DIR = ROOT / "build" / "site_en"

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


def _link(target: str, ja: bool) -> str:
    """A README link as seen from the top page of the Japanese site (docs/index.md) or the English one (site root)."""
    if re.match(r"^[a-z]+:", target) or target.startswith("#"):
        return target
    path, _, frag = target.partition("#")
    frag = "#" + frag if frag else ""
    if path == "README.md":
        return ("../" if ja else "index.md") + frag
    if path == "README.ja.md":
        return ("index.md" if ja else "ja/") + frag
    if path.startswith("docs/"):
        rest = path[len("docs/") :]
        if ja:
            return rest + frag
        return "ja/" + (rest[: -len(".md")] + "/" if rest.endswith(".md") else rest) + frag  # pages of the Japanese site
    kind = "tree" if (ROOT / path).is_dir() else "blob"
    return f"{REPO}/{kind}/main/{path}{frag}"


def convert(text: str, *, ja: bool) -> str:
    text = "\n".join(line for line in text.splitlines() if not LANG_SWITCH.match(line))
    text = _alerts(text, ja)
    return re.sub(r"\]\(([^)\s]+)\)", lambda m: "](" + _link(m.group(1), ja) + ")", text)


def write_pages() -> None:
    for src, out, ja in (("README.ja.md", ROOT / "docs" / "index.md", True), ("README.md", EN_DIR / "index.md", False)):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(convert((ROOT / src).read_text(encoding="utf-8"), ja=ja), encoding="utf-8")
        print(f"{src} -> {out.relative_to(ROOT)}")


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
    write_pages()
    if args.build or args.serve:
        build(args.strict)
    if args.serve:
        try:
            serve(args.port)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
