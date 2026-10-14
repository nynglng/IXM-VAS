"""Entry point for task processes.

    ixm-vas-worker.exe <task> <mode>              (installed)
    python -m ixm_vas.worker <task> <mode>        (checkout)

A console program of its own because the window is built without a console:
a task reports to the window over its stdout pipe (protocol.py), and a
windowed executable has no stdout to give it. The window starts this hidden.
"""

import sys

from ixm_vas.protocol import EXIT_ERROR
from ixm_vas.tasks import shopify_open_all_stores

TASKS = {
    "shopify": shopify_open_all_stores.main,
}


def main(argv=None) -> int:
    # Frozen, there is no -u and no PYTHONIOENCODING: set both here so every
    # line reaches the window as it is printed, em dashes included.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except (AttributeError, ValueError):
        pass
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in TASKS:
        print(f"usage: ixm-vas-worker <task> <mode>   tasks: {', '.join(TASKS)}")
        return EXIT_ERROR
    return TASKS[args[0]](args[1:])


if __name__ == "__main__":
    sys.exit(main())
