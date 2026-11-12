"""Write the project page's top pages from the READMEs: README.md -> docs/index.md, README.ja.md -> docs/ja/index.md.

Run before `zensical build` / `zensical serve` (the GitHub Pages workflow does). The written pages are not committed;
edit the READMEs. GitHub alerts (> [!WARNING]) become admonitions, links to docs/ stay inside the site and links to
other files of the repository go to GitHub.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/atinfinity/agentic-sim-world-gen"

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


def _link(target: str, depth: int) -> str:
    if re.match(r"^[a-z]+:", target) or target.startswith("#"):
        return target
    path, _, frag = target.partition("#")
    frag = "#" + frag if frag else ""
    up = "../" * depth
    if path == "README.md":
        return up + "index.md" + frag
    if path == "README.ja.md":
        return up + "ja/index.md" + frag
    if path.startswith("docs/"):
        return up + path[len("docs/") :] + frag
    kind = "tree" if (ROOT / path).is_dir() else "blob"
    return f"{REPO}/{kind}/main/{path}{frag}"


def convert(text: str, *, ja: bool, depth: int) -> str:
    text = "\n".join(line for line in text.splitlines() if not LANG_SWITCH.match(line))
    text = _alerts(text, ja)
    return re.sub(r"\]\(([^)\s]+)\)", lambda m: "](" + _link(m.group(1), depth) + ")", text)


def main() -> None:
    pages = [("README.md", "docs/index.md", False, 0), ("README.ja.md", "docs/ja/index.md", True, 1)]
    for src, dst, ja, depth in pages:
        out = ROOT / dst
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(convert((ROOT / src).read_text(encoding="utf-8"), ja=ja, depth=depth), encoding="utf-8")
        print(f"{src} -> {dst}")


if __name__ == "__main__":
    main()
