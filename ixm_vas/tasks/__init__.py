"""Tasks the app runs. Each runs in its own process through ixm_vas.worker
and reports to the window through ixm_vas.protocol.

The window never imports a task, so it never imports Playwright: a crash in a
task cannot take the window down, and a stuck task can be killed with its
Chrome without touching the window.
"""
