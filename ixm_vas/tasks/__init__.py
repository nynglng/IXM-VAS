"""Tasks the app runs. Each is a module run as its own process:

    python -m ixm_vas.tasks.<name> <mode>

and reports to the window through ixm_vas.protocol. The window never imports
a task, so it never imports Playwright -- which is what lets an update replace
Playwright while the window is open.
"""
