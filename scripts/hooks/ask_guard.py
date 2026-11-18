#!/usr/bin/env python3
"""Stop hook for development sessions in this repository: a turn that ends by handing a decision back to the user.

An agent that closes its report with "let me know if you want an issue for this" or "merge it if it looks fine"
leaves the work waiting until the user next looks, often for hours, while the change itself takes minutes. The rule
(CLAUDE.md 開発の進め方) is to file the issue and start on it without asking; only what the user alone can decide
(a public push, a name, money, deleting their files) is asked, and the rest goes on meanwhile.

The turn's last message (``last_assistant_message``) ending with such a request blocks the stop once, quoting it;
``stop_hook_active`` lets the second stop through, so a question that really is the user's goes out unchanged.
Only the standard library (`python3 -I scripts/hooks/ask_guard.py`).
"""
from __future__ import annotations

import json
import re
import sys

TAIL = 300  # characters of the message's end looked at: the closing lines, not a quote earlier in the report
ASK = re.compile(
    r"(?:言って|教えて|決めて|指示して|指示を|返事を)(?:ください|もらえれば|いただければ)"
    r"|(?:よければ|よろしければ)[^。\n]{0,30}言って"
    r"|(?:しますか|しましょうか|ですか|でしょうか|ますか)[？?。]"
    r"|指示を待って|返事を待って|判断を待って"
    r"|(?i:\b(?:let me know|tell me) (?:if|whether|which|when)\b|\b(?:shall|should) I\b[^.\n]*\?"
    r"|\bwould you like me to\b|\bwaiting for your\b)")
QUOTED = re.compile(r"「[^「」\n]*」|`[^`\n]*`")  # a phrase quoted or in code is talked about, not asked
REASON = ("The turn is about to end by handing a decision back to the user (\"{quote}\"). File the issues you "
          "found and start on them without asking (CLAUDE.md 開発の進め方). If it is something only the user can "
          "decide (a public push, a name, money, deleting their files), finish the other work you can do first and "
          "end with just that question; otherwise go ahead and report the result. Write in the user's language.")


def ask(text: str) -> str | None:
    """The sentence asking for a decision in the message's closing lines, or None."""
    tail = text.rstrip()[-TAIL:]
    bare = QUOTED.sub(lambda q: "\0" * len(q.group()), tail)  # same length: positions stay those of tail
    m = None
    for m in ASK.finditer(bare):
        pass
    if m is None:
        return None
    start = max(tail.rfind(c, 0, m.start()) for c in "。\n？?！!.") + 1
    if tail[m.end() - 1] in "？?。":
        end = m.end()
    else:
        ends = [i + 1 for i in (tail.find(c, m.end()) for c in "。\n？?.") if i >= 0]
        end = min(ends, default=len(tail))
    return tail[start:end].strip().rstrip("。.")


def check(event: dict) -> dict | None:
    if not isinstance(event, dict) or event.get("stop_hook_active"):
        return None
    text = event.get("last_assistant_message")
    quote = ask(text) if isinstance(text, str) else None
    if not quote:
        return None
    return {"decision": "block", "reason": REASON.format(quote=quote[-120:])}


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    out = check(event)
    if out:
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
