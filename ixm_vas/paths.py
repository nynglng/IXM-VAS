"""Where everything lives.

ROOT is the folder holding VERSION. Setup installs the app to
%LOCALAPPDATA%\\IXM-VAS; while developing it is the git checkout itself.

Everything that belongs to the VA -- their Chrome profile, their reports --
sits next to the code but is gitignored, and the updater refuses to write into
it, so an update can never sign anyone out or wipe a report.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VERSION_FILE = ROOT / "VERSION"
REQUIREMENTS_FILE = ROOT / "requirements.txt"
LAUNCHER = ROOT / "IXM-VAS.pyw"
ICON_ICO = ROOT / "assets" / "ixm-vas.ico"

# The VA's own, never in the repo.
CHROME_PROFILE = ROOT / "chrome-profile"
REPORTS = ROOT / "reports"
UPDATE_DIR = ROOT / ".update"
CRASH_LOG = REPORTS / "crash.log"

# Top-level names an update must never create, overwrite or delete.
PROTECTED = {"chrome-profile", "reports", ".update", ".venv", ".git"}


def read_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def is_git_checkout() -> bool:
    """A developer's clone. Self-update would overwrite uncommitted work, so
    the updater refuses here and says to git pull instead."""
    return (ROOT / ".git").exists()


def chrome_installed() -> bool:
    """Google Chrome, as opposed to Playwright's bundled Chromium."""
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", "")),
    ]
    return any((base / "Google" / "Chrome" / "Application" / "chrome.exe").exists()
               for base in candidates)
