"""How a task process talks to the app window.

Every task runs as its own process (see app.Worker) and prints as it goes.
A line starting with @@ is a JSON message for the window; every other line is
plain log text, shown as-is in the log panel.
"""

from __future__ import annotations

import json
from typing import Optional

PREFIX = "@@"

# Exit codes shared by every task.
EXIT_OK = 0
EXIT_SOME_FAILED = 1      # the run finished, but some items need attention
EXIT_ERROR = 2
EXIT_LOGGED_OUT = 3       # no session; nothing was touched
EXIT_LOGIN_ABORTED = 4    # the VA closed the login window, or it timed out
EXIT_PROFILE_BUSY = 5     # another Chrome is already using the profile


def emit(kind: str, **data) -> None:
    print(PREFIX + json.dumps({"kind": kind, **data}, ensure_ascii=False), flush=True)


def parse(line: str) -> Optional[dict]:
    if not line.startswith(PREFIX):
        return None
    try:
        msg = json.loads(line[len(PREFIX):])
    except ValueError:
        return None
    return msg if isinstance(msg, dict) and "kind" in msg else None
