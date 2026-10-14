"""Where everything lives: installed (built with PyInstaller) or a checkout.

INSTALLED: the program is in %LOCALAPPDATA%\\Programs\\IXM-VAS and every update
replaces it wholesale. The VA's own data -- their Chrome profile, reports --
is in %LOCALAPPDATA%\\IXM-VAS, which no install, update or uninstall touches,
so an update never signs anyone out.

CHECKOUT: everything is the repo folder; the VA data in it is gitignored.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(__file__).resolve().parent.parent

if FROZEN:
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    APP_DIR = Path(sys.executable).resolve().parent
    DATA = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "IXM-VAS"
else:
    BUNDLE = APP_DIR = DATA = ROOT

VERSION_FILE = BUNDLE / "VERSION"
ICON_ICO = BUNDLE / "assets" / "ixm-vas.ico"
WORKER_EXE = APP_DIR / "ixm-vas-worker.exe"

# The VA's own.
CHROME_PROFILE = DATA / "chrome-profile"
REPORTS = DATA / "reports"
DOWNLOADS = DATA / "updates"
CRASH_LOG = DATA / "crash.log"


def read_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def chrome_installed() -> bool:
    """Google Chrome, which every task drives (Playwright's channel="chrome")."""
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", "")),
    ]
    return any((base / "Google" / "Chrome" / "Application" / "chrome.exe").exists()
               for base in candidates)
