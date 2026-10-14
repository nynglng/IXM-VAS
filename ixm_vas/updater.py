"""Versions come from this repo's GitHub Releases; the app updates itself from them.

RELEASING
---------
Bump VERSION and push to main. The Release workflow
(.github/workflows/release.yml) builds IXM-VAS-Setup.exe and publishes it as
release v<VERSION>. Every open IXM-VAS sees it within a minute and locks
behind UPDATE NOW.

Why the latest release and not the VERSION file itself: VERSION changes the
moment you push, but its installer takes the workflow several minutes to
build. Demanding an update that cannot be downloaded yet would lock every VA
out with nothing to install.

CHECKING
--------
One HEAD request to github.com/<repo>/releases/latest, not followed: it
redirects to .../releases/tag/v<version> (or to .../releases when there is
none yet). That is the website, not the GitHub API, so the API's limit of 60
anonymous calls an hour -- one VA checking every minute would use all of it --
does not apply.

UPDATING
--------
Download IXM-VAS-Setup.exe from the release, check it against the .sha256
published beside it, start it with /UPDATE=1, and exit. The setup waits for
this process to end (packaging/installer.iss), replaces the program and starts
the new version. The VA's data is outside the program folder (paths.DATA), so
their Shopify login survives.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from . import paths

OWNER = "nynglng"
REPO = "IXM-VAS"
RELEASES = f"https://github.com/{OWNER}/{REPO}/releases"
ASSET = "IXM-VAS-Setup.exe"

CHECK_TIMEOUT_SEC = 15
DOWNLOAD_TIMEOUT_SEC = 60          # per read, not for the whole download
CHUNK = 256 * 1024

HEADERS = {"User-Agent": f"{REPO}-updater", "Cache-Control": "no-cache"}
TAG_RE = re.compile(r"/releases/tag/(v?(\d+(?:\.\d+)*))$")


class UpdateError(RuntimeError):
    pass


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
    remote: Optional[str]       # None: nothing released yet
    tag: Optional[str]
    update_required: bool


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def latest_release() -> Tuple[Optional[str], Optional[str]]:
    """(version, tag) of the latest release, or (None, None) if there is none."""
    req = urllib.request.Request(f"{RELEASES}/latest", method="HEAD", headers=HEADERS)
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=CHECK_TIMEOUT_SEC) as resp:
            location = resp.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        location = e.headers.get("Location", "")
    m = TAG_RE.search(urllib.parse.unquote(location or ""))
    return (m.group(2), m.group(1)) if m else (None, None)


def check() -> CheckResult:
    remote, tag = latest_release()
    local = paths.read_version()
    return CheckResult(local, remote, tag, is_newer(remote, local))


# ============================================================
# UPDATING
# ============================================================
def _open(url: str, timeout: float):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout)


def download_installer(tag: str, on_step: Callable[[str], None] = lambda msg: None) -> Path:
    """Fetch the release's setup into paths.DOWNLOADS and verify it."""
    if not paths.FROZEN:
        raise UpdateError("This copy runs from source, not an install. Update it with git pull.")

    url = f"{RELEASES}/download/{tag}/{ASSET}"
    try:
        with _open(url + ".sha256", CHECK_TIMEOUT_SEC) as resp:
            expected = resp.read().decode("ascii", "replace").split()[0].lower()
    except (urllib.error.URLError, IndexError) as e:
        raise UpdateError(f"couldn't read the release's checksum ({e})") from e

    paths.DOWNLOADS.mkdir(parents=True, exist_ok=True)
    for old in paths.DOWNLOADS.glob("*.exe"):
        try:
            old.unlink()            # earlier setups; one still running stays put
        except OSError:
            pass
    target = paths.DOWNLOADS / f"IXM-VAS-Setup-{tag}.exe"
    partial = target.with_suffix(".part")

    digest = hashlib.sha256()
    with _open(url, DOWNLOAD_TIMEOUT_SEC) as resp, open(partial, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        last_mb = -1
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            mb = done // (1024 * 1024)
            if mb != last_mb:
                last_mb = mb
                of = f" of {total / 1048576:.0f} MB" if total else " MB"
                on_step(f"Downloading the update… {done / 1048576:.0f}{of}")

    if digest.hexdigest() != expected:
        partial.unlink(missing_ok=True)
        raise UpdateError("the download didn't match the release's checksum. Try again.")
    partial.replace(target)
    return target


def run_installer(setup: Path) -> None:
    """Start the setup. The caller exits right after; the setup waits for it."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(setup), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1"],
                     close_fds=True, creationflags=flags)
