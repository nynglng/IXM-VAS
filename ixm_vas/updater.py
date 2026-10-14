"""Versions come from the GitHub repo; the app updates itself from it.

THE RULE
--------
The VERSION file on the repo's main branch is the version every VA must be on.
The window checks it every minute. When it is newer than the VERSION file the
app is running from, the window locks behind an UPDATE NOW button until the
VA updates. So releasing is: bump VERSION, push. Commits that do not bump it
reach nobody.

HOW A CHECK STAYS FRESH AND CHEAP
---------------------------------
raw.githubusercontent.com/.../main/VERSION is CDN-cached for ~5 minutes, and
the GitHub API allows 60 unauthenticated calls an hour per IP -- one VA
checking every minute would use all of it, two in one office would run out.
So a check is two plain HTTPS requests that neither limit applies to:

  1. GET github.com/<repo>.git/info/refs?service=git-upload-pack
     The git smart-HTTP ref list (~0.5 KB), the same request `git fetch` starts
     with. Always live, and it gives the commit main points at.
  2. GET raw.githubusercontent.com/<repo>/<that commit>/VERSION
     A commit-pinned URL, so the CDN can never serve a stale copy. Only fetched
     when main has moved since the last check.

AN UPDATE
---------
Downloads the zip of that exact commit, writes its files over the install,
deletes files the previous version installed that the new one dropped (from
.update/manifest.txt), reinstalls requirements if they changed, and restarts
the app. Anything under paths.PROTECTED -- the VA's Chrome profile, reports,
the venv -- is never written or deleted, so an update never signs anyone out.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Optional, Set, Tuple

from . import paths

OWNER = "nynglng"
REPO = "IXM-VAS"
BRANCH = "main"

REFS_URL = f"https://github.com/{OWNER}/{REPO}.git/info/refs?service=git-upload-pack"
VERSION_URL = "https://raw.githubusercontent.com/" + OWNER + "/" + REPO + "/{sha}/VERSION"
ZIP_URL = f"https://codeload.github.com/{OWNER}/{REPO}/zip/" + "{sha}"

CHECK_TIMEOUT_SEC = 15
DOWNLOAD_TIMEOUT_SEC = 180
PIP_TIMEOUT_SEC = 900

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class UpdateError(RuntimeError):
    pass


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": f"{REPO}-updater",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ============================================================
# VERSION CHECK
# ============================================================
def parse_version(text: Optional[str]) -> Optional[Tuple[int, ...]]:
    """'1.2.10' -> (1, 2, 10). Anything else -> None (never forces an update)."""
    if not text or not re.fullmatch(r"\d+(\.\d+)*", text.strip()):
        return None
    return tuple(int(x) for x in text.strip().split("."))


def is_newer(remote: Optional[str], local: str) -> bool:
    r, l = parse_version(remote), parse_version(local)
    if r is None or l is None:
        return False
    width = max(len(r), len(l))
    return r + (0,) * (width - len(r)) > l + (0,) * (width - len(l))


@dataclass
class CheckResult:
    local: str
    remote: Optional[str]       # None: main has no VERSION file
    sha: str
    update_required: bool


class Checker:
    """Remembers the last commit seen, so VERSION is fetched only when main moves.

    Used from one thread at a time.
    """

    def __init__(self) -> None:
        self._sha: Optional[str] = None
        self._remote: Optional[str] = None

    def check(self) -> CheckResult:
        sha = remote_head()
        if sha != self._sha:
            self._remote = fetch_remote_version(sha)
            self._sha = sha
        local = paths.read_version()
        return CheckResult(local, self._remote, sha, is_newer(self._remote, local))


def remote_head() -> str:
    """The commit main points at, from the git smart-HTTP ref advertisement."""
    body = _get(REFS_URL, CHECK_TIMEOUT_SEC).decode("ascii", "replace")
    m = re.search(r"([0-9a-f]{40}) refs/heads/" + re.escape(BRANCH) + r"(?![\w/.-])", body)
    if not m:
        raise UpdateError(f"branch {BRANCH!r} not found in {OWNER}/{REPO}")
    return m.group(1)


def fetch_remote_version(sha: str) -> Optional[str]:
    try:
        return _get(VERSION_URL.format(sha=sha), CHECK_TIMEOUT_SEC).decode("utf-8").strip()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


# ============================================================
# APPLYING AN UPDATE
# ============================================================
def _is_safe(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return (bool(parts) and not PurePosixPath(rel).is_absolute() and ".." not in parts
            and ":" not in parts[0] and parts[0] not in paths.PROTECTED)


def _manifest(root: Path) -> Path:
    return root / paths.UPDATE_DIR.name / "manifest.txt"


def read_manifest(root: Path) -> Set[str]:
    try:
        text = _manifest(root).read_text(encoding="utf-8-sig")   # setup.ps1 may add a BOM
        return {ln.strip() for ln in text.splitlines() if ln.strip()}
    except OSError:
        return set()


def write_manifest(root: Path, files: Set[str]) -> None:
    path = _manifest(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sorted(files)) + "\n", encoding="utf-8")


def _replace(src_tmp: Path, dest: Path) -> None:
    """os.replace, retried: Explorer or an antivirus can hold a file for a moment."""
    for attempt in range(5):
        try:
            os.replace(src_tmp, dest)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.5 * (attempt + 1))


def apply_zip(zip_path: Path, root: Path) -> bool:
    """Write a GitHub source zip over the install at root.

    Returns True when requirements.txt changed. Refuses an archive that does
    not look like this app, or that would write into a protected folder.
    """
    old_requirements = (root / "requirements.txt").read_bytes() \
        if (root / "requirements.txt").exists() else b""

    with zipfile.ZipFile(zip_path) as zf:
        members = [n for n in zf.namelist() if not n.endswith("/")]
        tops = {n.split("/", 1)[0] for n in members}
        if len(tops) != 1:
            raise UpdateError("the downloaded archive has an unexpected layout")
        top = tops.pop() + "/"
        files = {n[len(top):]: n for n in members if n.startswith(top) and n != top}

        if "VERSION" not in files or "ixm_vas/app.py" not in files:
            raise UpdateError("the downloaded archive is not IXM-VAS")
        unsafe = [rel for rel in files if not _is_safe(rel)]
        if unsafe:
            raise UpdateError(f"the archive tries to write outside the app: {unsafe[:3]}")

        for rel, member in files.items():
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".ixm-new")
            with zf.open(member) as src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out)
            _replace(tmp, dest)

    new_files = set(files)
    for rel in read_manifest(root) - new_files:
        if _is_safe(rel):
            try:
                (root / rel).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass        # a leftover file is harmless; never fail an update on it
    write_manifest(root, new_files)

    new_requirements = (root / "requirements.txt").read_bytes() \
        if (root / "requirements.txt").exists() else b""
    return new_requirements != old_requirements


def console_python() -> str:
    """python.exe next to the running interpreter (which is pythonw.exe when
    started from the shortcut): pip and the tasks want a real stdout."""
    exe = Path(sys.executable)
    candidate = exe.with_name("python.exe")
    return str(candidate if candidate.exists() else exe)


def install_requirements() -> None:
    proc = subprocess.run(
        [console_python(), "-m", "pip", "install", "--disable-pip-version-check",
         "-r", str(paths.REQUIREMENTS_FILE)],
        capture_output=True, text=True, timeout=PIP_TIMEOUT_SEC, creationflags=NO_WINDOW,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:])
        raise UpdateError(f"installing requirements failed:\n{tail}")
    if not paths.chrome_installed():
        # Only without Google Chrome do the tasks fall back to Playwright's
        # Chromium, which has to match the Playwright version just installed.
        subprocess.run([console_python(), "-m", "playwright", "install", "chromium"],
                       capture_output=True, timeout=PIP_TIMEOUT_SEC, creationflags=NO_WINDOW)


def install(sha: str, on_step: Callable[[str], None] = lambda msg: None) -> None:
    """Download commit sha and install it over this copy of the app."""
    if paths.is_git_checkout():
        raise UpdateError("This copy is a git checkout, not an install. Update it with git pull.")

    paths.UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = paths.UPDATE_DIR / f"{sha}.zip"
    try:
        on_step("Downloading the new version...")
        zip_path.write_bytes(_get(ZIP_URL.format(sha=sha), DOWNLOAD_TIMEOUT_SEC))

        on_step("Installing...")
        if apply_zip(zip_path, paths.ROOT):
            on_step("Installing requirements (this can take a few minutes)...")
            install_requirements()
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass


def relaunch() -> None:
    """Start a fresh copy of the app. The caller exits right after."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([sys.executable, str(paths.LAUNCHER)], cwd=str(paths.ROOT),
                     creationflags=flags, close_fds=True)
