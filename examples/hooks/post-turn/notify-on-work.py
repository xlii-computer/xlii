#!/usr/bin/env python3
"""Example post-turn hook: ping your phone when a turn did real work.

Fires `xlii notify` (the send-only OMEMO rail) whenever a turn called tools or
changed files — so a long `xlii code` run buzzes your phone when it's done.

Install:
    cp examples/hooks/post-turn/notify-on-work.py \
       <project>/.xlii/hooks/post-turn/notify-on-work.py
    chmod +x <project>/.xlii/hooks/post-turn/notify-on-work.py
Requires the [daemon] extra + a configured ~/.config/xlii/notify.toml (guide §13).

Hooks are observers: this reads the event JSON on stdin and never aborts the turn
(a failure here is swallowed).
"""

import json
import subprocess
import sys

try:
    event = json.load(sys.stdin)
except Exception:
    sys.exit(0)

data = event.get("data", {})
tool_calls = data.get("tool_calls", 0)
dirty = [d for d in data.get("dirty", []) if d != "__rescan__"]

# Only notify when the turn actually did something worth a phone buzz.
if not tool_calls and not dirty:
    sys.exit(0)

summary = f"xlii: turn done — {tool_calls} tool call(s)"
if dirty:
    summary += f", {len(dirty)} file(s) changed"

try:
    subprocess.run(["xlii", "notify", summary], timeout=20, check=False)
except Exception:
    # A post-turn hook must never fail the turn it reports on, so a missing xlii or a timeout is ignored.
    pass
