"""
Shopify: open every collaborator store.

Port of ShopifyPartners_OpenAllStores.py (the HTTP version) for the VA app.
The opening itself is unchanged -- same endpoints, parser, pacing and report.
What changed is who drives it and how strict it is about logging in:

  * It runs as a worker process under the IXM-VAS window and reports to it over
    stdout (ixm_vas/protocol.py), instead of from a terminal.
  * LOGIN IS STRICT. The terminal script opened a login window by itself when
    it found no session and then carried on. Here a run with no session stops
    before touching anything and turns the indicator red. Signing in is its own
    step ("login" mode), started from the indicator, and the window keeps the
    run button disabled until a check has seen a live session.
  * Each VA signs in to their own Chrome profile (chrome-profile/ next to the
    app, gitignored). That matters: "opened" is granted per USER, not per
    organization, so every VA has to open the stores for their own account.

Modes -- ixm-vas-worker shopify <mode>  (or python -m ixm_vas.worker shopify <mode>)
  check   headless: is the profile signed in to dev.shopify.com?
  login   visible Chrome on the dashboard; waits while the VA signs in, then
          closes Chrome by itself
  run     headless: open every collaborator store, write the report

HOW OPENING WORKS
-----------------
The dashboard's row click is just an HTTP redirect:

    GET  /dashboard/<ORG>/stores/<STORE_ID>/collaborator_login
    302  -> https://admin.shopify.com/store/<HANDLE>

and the stores table is server-rendered, every row carrying its own
collaborator_login link. So both halves are plain HTTP through the profile's
cookies (context.request) -- no tabs, no clicking.

We stop at the 302 and never follow it. The redirect IS the grant, and its
Location names the store's admin handle. admin.shopify.com sits behind a
Cloudflare challenge that answers 403 to any non-interactive fetch, so
following it would report every success as a failure.

No cache, by design: every run opens every openable store. "Opened" is per
user while the store list is per organization, so a cache would happily report
stores as open for an account that can reach none of them. A full run is ~2
minutes; a wrong answer costs more.

When Shopify redesigns the dashboard, parse_rows() stops matching and the run
stops with a "dashboard markup changed" error instead of opening a partial
list. The fix is made in the original script (its --discover mode records the
new endpoints) and ported here.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from playwright.async_api import async_playwright

from ixm_vas import paths
from ixm_vas.protocol import (
    EXIT_ERROR, EXIT_LOGGED_OUT, EXIT_LOGIN_ABORTED, EXIT_OK, EXIT_PROFILE_BUSY,
    EXIT_SOME_FAILED, emit,
)


# ============================================================
# CONFIG
# ============================================================
# The partner org id is NOT hardcoded: it is read from the signed-in session,
# so this works for whichever partner account the VA signs in with.
DASHBOARD_ROOT = "https://dev.shopify.com/dashboard"
ORG_ID_RE = re.compile(r'/dashboard/(\d+)\b')


def list_url(org_id: str, page: int) -> str:
    # Sorted by name so the order cannot shift mid-walk: the default sort is
    # "last accessed", which every collaborator_login rewrites -- ours or
    # another account's. The 2026-09 redesign dropped both the old sort value
    # (NAME_DESC) and the Collaborations filter (store_type=), and ignores
    # them silently; rows are now filtered on the Type column instead.
    return (f"{DASHBOARD_ROOT}/{org_id}/stores?"
            f"direction=asc&page={page}&sort=name")

MAX_PAGES = 500          # safety cap; we stop at the first empty page anyway
INVENTORY_BATCH = 8      # list pages fetched concurrently

# Measured: 10 parallel / 0.05s pacing made Shopify start answering
# collaborator_login with no redirect at all -- 114 of 595 stores came back as
# bogus "no access", including ones that had opened fine minutes earlier.
# 6 / 0.15s completes all of them cleanly. Do not raise these without re-testing.
MAX_PARALLEL = 6         # concurrent collaborator_login requests
PACING_SEC = 0.15        # gap between launches
REQUEST_TIMEOUT_MS = 30000

# A throttled response looks identical to a refusal, so never trust the first
# non-redirect answer: back off and ask again before calling it a failure.
OPEN_RETRIES = 3
RETRY_BACKOFF_SEC = 2.0

# Status values (column 3) we refuse to touch. "Access expired" means the
# merchant REVOKED collaborator access -- it is not a stale opening that a
# re-run could fix. These rows carry no collaborator_login link at all; they
# need a fresh access request to the client.
SKIP_STATUSES = {"expired", "access expired"}

# The Type column (column 2). The list is every store the org can see,
# including its own dev stores ("Dev") and transfer stores ("Client
# transfer"). Those open through /stores/<id>/login, not collaborator_login,
# and are not this task's business.
COLLABORATION_TYPE = "collaboration"

# Signing in: how long the login window waits, and how often it looks.
# Generous, because a VA may need a 2FA code from someone else.
LOGIN_WAIT_SEC = 600
LOGIN_POLL_SEC = 3

CHROME_PROFILE = paths.CHROME_PROFILE
REPORT_DIR = paths.REPORTS

BROWSER_ARGS = ["--disable-blink-features=AutomationControlled"]


def slugify_account(account: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', account.lower()).strip('-') or "unknown"


def report_path(org_id: str, account: str = "") -> Path:
    """One report per org+account, overwritten each run -- not a dated log pile.

    Per ACCOUNT as well as per org: two people in one partner org see the same
    store list, but "opened" is granted per user, so their results are not
    interchangeable.
    """
    who = f"-{slugify_account(account)}" if account else ""
    return REPORT_DIR / f"open-report-{org_id}{who}.txt"


# ============================================================
# HTML PARSING
# ============================================================
# Validated against every page of a real walk on 2026-09-30, after the
# 2026-09 redesign. The table is server-rendered; the stable parts are:
#   - every store <tr>, openable or not, carries
#     data-component-name="stores-index-row" and data-row-id="<store id>"
#   - an openable row's primary <a> carries data-row-primary-link="true" and
#     the collaborator_login href
#   - under the name, the domain is the <span class="... text-ui-text-tertiary">
# Column order (7 <td>): name+domain | TYPE | STATUS | organization | PLAN |
#                        last accessed | actions
#
# Rows are matched on stores-index-row anywhere in the page, never by where
# they sit. Each row has a <tbody> of its own, and the page also carries a
# hidden <tbody data-state-when="loading"> of blank skeleton rows and, past
# the last page, a <tbody data-state-when="empty"> reading "No stores yet".
# Reading only the first <tbody> once cut a run to one store per page and
# turned the empty-state text into a "store".
ROW_RE = re.compile(r'<tr\b[^>]*data-component-name="stores-index-row"[^>]*>.*?</tr>', re.S)
ROW_ID_RE = re.compile(r'data-row-id="(\d+)"')
LINK_RE = re.compile(
    r'<a\b[^>]*data-row-primary-link="true"[^>]*href="([^"]*collaborator_login)"[^>]*>(.*?)</a>',
    re.S,
)
# Both searched within the first <td> only; other cells use the same classes.
NAME_RE = re.compile(r'<span class="[^"]*\bfont-medium\b[^"]*">(.*?)</span>', re.S)
DOMAIN_RE = re.compile(r'<span class="[^"]*\btext-ui-text-tertiary\b[^"]*">(.*?)</span>', re.S)
TD_RE = re.compile(r'<td\b[^>]*>(.*?)</td>', re.S)
STORE_ID_RE = re.compile(r'/stores/(\d+)/collaborator_login')

TYPE_TD_INDEX = 1
STATUS_TD_INDEX = 2
ORG_TD_INDEX = 3
PLAN_TD_INDEX = 4

LOGIN_WALL_RE = re.compile(r'(accounts\.shopify\.com|"login"|Log in to your account)', re.I)

MARKUP_CHANGED = ("Shopify changed its dashboard, so the store list can no longer be "
                  "read. Nothing was opened. Tell your admin -- this needs a fix in "
                  "the app, not a re-run.")


def strip_tags(fragment: str) -> str:
    """Collapse an HTML fragment to readable text."""
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment))).strip()


@dataclass
class Store:
    store_id: str                 # numeric partner id -- the stable key
    name: str
    domain: str
    status: str                   # "Active" / "Access expired" / ...
    plan: str                     # "Basic" / "Frozen" / ...
    login_path: str               # /dashboard/<org>/stores/<id>/collaborator_login
                                  # empty => the dashboard offers no way to open it
    store_type: str = ""          # "Collaboration" / "Dev" / "Client transfer"

    # filled in by the open phase
    handle: Optional[str] = None  # admin.shopify.com/store/<handle>
    outcome: str = ""
    final_url: str = ""
    http_status: Optional[int] = None
    attempts: int = 1


def parse_rows(page_html: str) -> List[Store]:
    """Parse every store row on a list page -- openable or not, of any type.

    Collaborations the dashboard offers no way into ("Access expired" with
    Re-request access, "Request sent" with Cancel request) have no
    collaborator_login href. They cannot be opened, but they are the ones
    where access actually lapsed, so they are parsed and carried through with
    an empty login_path and reported separately. Keeping only the
    collaborations is the caller's job; see is_collaboration().
    """
    stores: List[Store] = []
    for block in ROW_RE.findall(page_html):
        cells = TD_RE.findall(block)
        tds = [strip_tags(t) for t in cells]
        first = cells[0] if cells else block

        def col(i: int) -> str:
            return tds[i] if len(tds) > i else ""

        link = LINK_RE.search(block)
        if link:
            sid = STORE_ID_RE.search(link.group(1))
            if not sid:
                continue
            store_id, name, login_path = sid.group(1), strip_tags(link.group(2)), html.unescape(link.group(1))
        else:
            row_id = ROW_ID_RE.search(block)
            if not row_id:
                continue
            name_m = NAME_RE.search(first)
            store_id, login_path = row_id.group(1), ""
            name = strip_tags(name_m.group(1)) if name_m else col(ORG_TD_INDEX)

        domain = DOMAIN_RE.search(first)
        stores.append(Store(
            store_id=store_id,
            name=name,
            domain=strip_tags(domain.group(1)) if domain else "",
            status=col(STATUS_TD_INDEX),
            plan=col(PLAN_TD_INDEX),
            login_path=login_path,
            store_type=col(TYPE_TD_INDEX),
        ))
    return stores


def check_nothing_dropped(page_html: str, rows: List[Store], page_num: int) -> None:
    """Refuse a page carrying login links for stores parse_rows() did not return.

    Every collaborator_login href belongs to some row, so one naming a store
    that was not parsed means the parser is reading only part of the page.
    That is exactly how a run once found 33 of 626 stores, opened those, and
    reported "every store opened". A short inventory has to stop the run, not
    pass for a quieter success.
    """
    missing = set(STORE_ID_RE.findall(page_html)) - {s.store_id for s in rows}
    if missing:
        raise RuntimeError(
            f"{MARKUP_CHANGED}\n  (stores list page {page_num} links {len(missing)} "
            f"store(s) the parser did not return; it parsed {len(rows)}.)"
        )


def is_collaboration(store: Store) -> bool:
    """A collaborator store, as opposed to one the org itself owns.

    A collaborator_login link settles it whatever the Type column says, so a
    renamed label can never stop an openable store from being opened.
    """
    return bool(store.login_path) or store.store_type.strip().lower() == COLLABORATION_TYPE


# ============================================================
# OUTCOME CLASSIFICATION
# ============================================================
OPENED = "OPENED"
OPENED_INACTIVE = "OPENED_INACTIVE"
FAILED_NO_ACCESS = "FAILED_NO_ACCESS"
FAILED_UNKNOWN = "FAILED_UNKNOWN"
SKIPPED_EXPIRED = "SKIPPED_EXPIRED"

ADMIN_STORE_RE = re.compile(r'https://admin\.shopify\.com/store/([a-z0-9\-]+)(/[^?#]*)?', re.I)

# Plans on which the store exists and is reachable but is dormant -- no
# analytics to scrape. Taken from the plan column, not from the handoff.
DORMANT_PLANS = {"frozen", "inactive", "paused", "cancelled"}


def classify(http_status: Optional[int], location: str, plan: str) -> Tuple[str, Optional[str]]:
    """Judge the handoff by its 302, NOT by loading the admin page.

    collaborator_login answers 302 with Location: admin.shopify.com/store/<handle>.
    That redirect IS the grant -- the dashboard only issues it for a store this
    partner may enter, and the Location names the handle we want.

    We deliberately do not follow it. admin.shopify.com sits behind a Cloudflare
    JS challenge that answers 403 to any non-interactive fetch (a real browser
    tab gets the same 403 before solving it). Following the redirect would turn
    every success into a bogus "no access".
    """
    m = ADMIN_STORE_RE.match(location or "")
    if m and http_status in (301, 302, 303, 307, 308):
        handle = m.group(1)
        if plan.strip().lower() in DORMANT_PLANS:
            return OPENED_INACTIVE, handle
        return OPENED, handle

    # Redirected somewhere else -- typically back into the dashboard -- or
    # answered inline instead of redirecting. Either way the grant was refused.
    if location or http_status == 200:
        return FAILED_NO_ACCESS, None

    if http_status is not None and http_status >= 400:
        return FAILED_NO_ACCESS, None

    return FAILED_UNKNOWN, None


# ============================================================
# BROWSER / SESSION
# ============================================================
class NotLoggedIn(RuntimeError):
    """No usable session. Kept apart from other failures because the window
    answers it differently: red indicator, sign in, not an error dialog."""


def profile_in_use() -> bool:
    """Is a Chrome process already running on our profile?

    Chrome allows one process per user-data-dir; a second launch hands its
    window to the first and exits, which Playwright reports as an unhelpful
    "browser closed". On Windows a running Chrome holds <profile>/lockfile
    open and undeletable; one left by a crash deletes fine, which is also what
    Chrome itself would do with it.
    """
    lock = CHROME_PROFILE / "lockfile"
    if not lock.exists():
        return False
    try:
        lock.unlink()
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def kill_headless_leftovers() -> bool:
    """End headless Chrome left running on OUR profile by a killed worker.

    Normally the worker's own shutdown closes Chrome, but a worker killed from
    Task Manager can leave its headless Chrome behind, holding the profile
    with no window the VA could close. Only headless processes on this exact
    profile are touched, so nothing the VA can see is ever closed.
    """
    if sys.platform != "win32":
        return False
    script = (
        "$p = $env:IXM_PROFILE; "
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' or Name='chromium.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($p) "
        "-and $_.CommandLine.Contains('--headless') -and -not $_.CommandLine.Contains('--type=') } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30,
            env=dict(os.environ, IXM_PROFILE=str(CHROME_PROFILE)),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:
        return False
    killed = [ln for ln in out.split() if ln.isdigit()]
    if killed:
        print(f"  ended {len(killed)} leftover background Chrome process(es) on the profile")
    return bool(killed)


async def launch_persistent(p, headless: bool):
    """The VA's profile in their installed Google Chrome.

    Chrome only -- no fallback to Playwright's bundled Chromium. The installed
    app does not ship one (it would add ~150 MB), and a profile signed in
    under one browser is not reliably readable by the other.
    """
    if not paths.chrome_installed():
        raise RuntimeError(
            "Google Chrome is not installed. IXM-VAS signs in to Shopify through "
            "Chrome: install it from https://www.google.com/chrome/ and try again."
        )
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    return await p.chromium.launch_persistent_context(
        user_data_dir=str(CHROME_PROFILE),
        channel="chrome",
        headless=headless,
        args=BROWSER_ARGS,
        no_viewport=not headless,
    )


async def open_profile(p, headless: bool):
    """Launch Chrome on the VA's profile, or None if the profile is taken."""
    if profile_in_use():
        if kill_headless_leftovers():
            await asyncio.sleep(2)
        if profile_in_use():
            emit("profile_busy")
            print("Chrome is already open on your IXM-VAS profile. Close that Chrome "
                  "window, then try again.")
            return None
    try:
        context = await launch_persistent(p, headless=headless)
    except Exception:
        if profile_in_use():
            emit("profile_busy")
            print("Chrome is already open on your IXM-VAS profile. Close that Chrome "
                  "window, then try again.")
            return None
        raise
    context.set_default_timeout(REQUEST_TIMEOUT_MS)
    return context


async def close_quietly(context) -> None:
    try:
        await context.close()
    except Exception:
        pass


ACCOUNT_EMAIL_RE = re.compile(r'[\w.\-+]+@[\w.\-]+\.\w+')

# Signed-out sessions are bounced to Shopify's login service. Verified against
# an empty profile: /dashboard answers 403 and lands on
# accounts.shopify.com/session-service/login?...
LOGIN_REDIRECT_RE = re.compile(
    r'accounts\.shopify\.com|/session-service/login|/auth/login', re.I
)


def extract_account(body: str) -> str:
    """Pull the signed-in email out of the dashboard chrome.

    It sits in the account block immediately above the logout link, so of all
    the addresses on the page the right one is the last before /dashboard/logout.
    """
    hits = list(ACCOUNT_EMAIL_RE.finditer(body or ""))
    if not hits:
        return ""
    cut = body.find("/dashboard/logout")
    if cut != -1:
        before = [h for h in hits if h.start() < cut]
        if before:
            return before[-1].group(0)
    return hits[0].group(0)


async def discover_session(context, headers: Dict[str, str]) -> Tuple[str, str]:
    """Return (org_id, account_email) for whoever is signed in.

    This is the login gate. It runs before any store is touched, so a
    signed-out session stops the run immediately rather than quietly opening
    nothing and reporting success.
    """
    resp = await context.request.get(DASHBOARD_ROOT, headers=headers, timeout=REQUEST_TIMEOUT_MS)
    body = await resp.text()

    if LOGIN_REDIRECT_RE.search(resp.url or ""):
        raise NotLoggedIn(
            "Not logged in to Shopify -- the dashboard sent us to the login page.\n"
            f"  landed on : {resp.url}"
        )

    m = ORG_ID_RE.search(resp.url or "") or ORG_ID_RE.search(body)
    if not m:
        raise RuntimeError(
            f"Signed in, but no Shopify Partners organization was found (HTTP "
            f"{resp.status}, landed on {resp.url}).\n"
            "  Check that this account belongs to the partner organization. If it\n"
            "  does, Shopify changed its dashboard -- tell your admin."
        )

    account = extract_account(body)
    if not account:
        print("[Warn] Signed in, but the account email could not be read from the "
              "dashboard. The report will not name which account this run was for.")
    return m.group(1), account


# ============================================================
# REQUEST HEADERS
# ============================================================
async def browser_headers(context, page=None) -> Dict[str, str]:
    """Headers that make context.request look like a real navigation.

    The dashboard is Rails content-negotiated: without an Accept header that
    admits text/html it answers 406 Not Acceptable and renders nothing. The
    User-Agent is read off the live browser so it cannot drift from the
    profile that owns the session. Pass the page that is already open when
    there is one, so a visible window does not flash an extra tab.
    """
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }
    try:
        pg = page or await context.new_page()
        ua = await pg.evaluate("() => navigator.userAgent")
        if page is None:
            await pg.close()
        if ua:
            headers["User-Agent"] = ua
    except Exception as e:
        print(f"[Warn] Could not read the browser User-Agent ({e}); using the default.")
    return headers


# ============================================================
# PHASE 1 -- INVENTORY
# ============================================================
async def fetch_one_page(context, headers: Dict[str, str], org_id: str, page_num: int):
    url = list_url(org_id, page_num)
    resp = await context.request.get(url, headers=headers, timeout=REQUEST_TIMEOUT_MS)
    return page_num, resp.status, await resp.text()


async def fetch_inventory(context, headers: Dict[str, str], org_id: str) -> List[Store]:
    """Walk the paginated list over HTTP and parse every row.

    Pages are fetched INVENTORY_BATCH at a time rather than one after another.
    Page order within a batch is preserved, so the first empty page still ends
    the walk -- every page after it is empty too.
    """
    found: List[Store] = []
    seen: set = set()
    page_num = 1

    while page_num <= MAX_PAGES:
        batch = list(range(page_num, min(page_num + INVENTORY_BATCH, MAX_PAGES + 1)))
        results = await asyncio.gather(
            *[fetch_one_page(context, headers, org_id, p) for p in batch]
        )

        for p, status, body in results:
            if status != 200:
                # Never treat this as "end of list" -- an empty inventory caused
                # by a server refusal would be reported as total success.
                if not found:
                    if status in (401, 403):
                        raise NotLoggedIn(
                            f"The store list answered HTTP {status} -- the Shopify "
                            "session ended. Nothing was opened."
                        )
                    raise RuntimeError(
                        f"The store list (page {p}) answered HTTP {status}. Nothing "
                        "was opened. Try again in a minute; if it keeps happening, "
                        "tell your admin."
                    )
                print(f"  page {p}: HTTP {status} -- stopping with {len(found)} stores")
                return found

            rows = parse_rows(body)
            check_nothing_dropped(body, rows, p)
            if not rows:
                if not found and LOGIN_WALL_RE.search(body):
                    # The session passed the check at startup but died before
                    # the list loaded. Nothing was opened.
                    raise NotLoggedIn(
                        "The Shopify session ended between the login check and "
                        "loading the store list. Nothing was opened."
                    )
                if not found:
                    raise RuntimeError(f"{MARKUP_CHANGED}\n  (no rows on page 1, no login wall.)")
                return found

            fresh = [s for s in rows if s.store_id not in seen]
            for s in fresh:
                seen.add(s.store_id)
            found.extend(fresh)

            # Defensive: a dashboard that ignored ?page= would loop forever.
            if not fresh:
                print(f"  page {p} repeated itself -- stopping")
                return found

        print(f"  pages {batch[0]}-{batch[-1]}: {len(found)} stores so far")
        emit("inventory", found=len(found))
        page_num += INVENTORY_BATCH

    return found


# ============================================================
# PHASE 2 -- OPEN
# ============================================================
async def open_store(context, store: Store, headers: Dict[str, str], sem: asyncio.Semaphore) -> Store:
    async with sem:
        url = DASHBOARD_ROOT.rsplit("/dashboard", 1)[0] + store.login_path

        for attempt in range(1, OPEN_RETRIES + 1):
            try:
                # max_redirects=0: we want the 302 itself, not the Cloudflare-
                # guarded admin page it points at. One request per store.
                resp = await context.request.get(
                    url, headers=headers, max_redirects=0, timeout=REQUEST_TIMEOUT_MS
                )
                location = resp.headers.get("location", "")
                store.http_status = resp.status
                store.final_url = location or resp.url
                store.outcome, store.handle = classify(resp.status, location, store.plan)
            except Exception as e:
                store.outcome = FAILED_UNKNOWN
                store.final_url = f"(request error: {type(e).__name__}: {e})"

            if store.outcome in (OPENED, OPENED_INACTIVE) or attempt == OPEN_RETRIES:
                break
            # No redirect came back. That is what throttling looks like, and it
            # is indistinguishable from a refusal, so back off and retry.
            store.attempts = attempt + 1
            await asyncio.sleep(RETRY_BACKOFF_SEC * attempt)

        await asyncio.sleep(PACING_SEC)
        return store


# ============================================================
# REPORTING
# ============================================================
def describe_types(stores: List[Store]) -> str:
    """'Dev 4, Client transfer 1' -- a renamed Type label shows up here."""
    counts: Dict[str, int] = {}
    for s in stores:
        counts[s.store_type or "(no type)"] = counts.get(s.store_type or "(no type)", 0) + 1
    return ", ".join(f"{t} {n}" for t, n in counts.items())


def write_report(stores: List[Store], org_id: str, account: str,
                 others: Optional[List[Store]] = None) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = report_path(org_id, account)

    buckets: Dict[str, List[Store]] = {}
    for s in stores:
        buckets.setdefault(s.outcome, []).append(s)

    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("SHOPIFY PARTNERS -- OPEN ALL STORES")
    lines.append(f"run: {stamp}")
    lines.append(f"partner org: {org_id}")
    lines.append(f"account    : {account or '(not detected)'}   "
                 "-- 'opened' is granted per account, not per organization")
    lines.append(f"collaborator stores: {len(stores)}")
    if others:
        lines.append(f"org's own stores   : {len(others)} ({describe_types(others)}) -- not "
                     "collaborations, not touched")
    lines.append("=" * 72)

    lines.append("")
    lines.append("SUMMARY")
    lines.append("-" * 72)
    for outcome in (OPENED, OPENED_INACTIVE, SKIPPED_EXPIRED,
                    FAILED_NO_ACCESS, FAILED_UNKNOWN):
        n = len(buckets.get(outcome, []))
        if n:
            lines.append(f"  {outcome:<20} {n:>4}")

    # The reason this task exists: name the broken ones.
    failed = buckets.get(FAILED_NO_ACCESS, []) + buckets.get(FAILED_UNKNOWN, [])
    lines.append("")
    lines.append("NEEDS ATTENTION -- access did not work even after the handoff")
    lines.append("-" * 72)
    if not failed:
        lines.append("  none -- every store opened")
    for s in failed:
        lines.append(f"  {s.domain or s.name}")
        lines.append(f"      partner id : {s.store_id}")
        lines.append(f"      status/plan: {s.status} / {s.plan}")
        lines.append(f"      landed on  : {s.final_url}  (HTTP {s.http_status})")

    # Expired collaborations cannot be opened at all -- the dashboard shows
    # "Re-request access". Surfacing them is the point: this is real access loss.
    expired = buckets.get(SKIPPED_EXPIRED, [])
    lines.append("")
    lines.append("ACCESS REVOKED -- merchant ended the collaboration; re-request needed")
    lines.append("-" * 72)
    if not expired:
        lines.append("  none")
    for s in expired:
        lines.append(f"  {s.domain or s.name:<45} {s.status}")

    inactive = buckets.get(OPENED_INACTIVE, [])
    if inactive:
        lines.append("")
        lines.append("OPENED BUT DORMANT -- reachable, store is on a reactivate/frozen plan")
        lines.append("-" * 72)
        for s in inactive:
            lines.append(f"  {(s.domain or s.name)[:39]:<40} {(s.handle or '?')[:23]:<24} plan={s.plan}")

    lines.append("")
    lines.append("STORE ID MAP -- admin.shopify.com/store/<handle>")
    lines.append("-" * 72)
    lines.append("  The handle is NOT derivable from the domain -- most stores' handles")
    lines.append("  look nothing like their brand name. A guessed handle gives \"There's a")
    lines.append("  technical problem with Shopify\": a wrong URL, NOT lost access.")
    lines.append("  \"Doesn't have permission to view\" is the different one: right handle,")
    lines.append("  store not opened yet. Use the HANDLE column below; it is read from")
    lines.append("  Shopify's own redirect.")
    lines.append("")
    lines.append(f"  {'DOMAIN':<40} {'HANDLE':<24} {'PLAN':<10} OUTCOME")
    for s in sorted(stores, key=lambda x: (x.domain or x.name).lower()):
        lines.append(f"  {(s.domain or s.name)[:39]:<40} {(s.handle or '-')[:23]:<24} "
                     f"{s.plan[:9]:<10} {s.outcome}")

    lines.append("")
    lines.append("=" * 72)
    lines.append("This file is the only record a run leaves; it is overwritten each time.")
    lines.append("=" * 72)

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ============================================================
# MODES
# ============================================================
async def do_check() -> int:
    """Headless: say whether the profile has a live dev.shopify.com session."""
    async with async_playwright() as p:
        context = await open_profile(p, headless=True)
        if context is None:
            return EXIT_PROFILE_BUSY
        try:
            headers = await browser_headers(context)
            try:
                org_id, account = await discover_session(context, headers)
            except NotLoggedIn as e:
                print(e)
                emit("session", state="logged_out")
                return EXIT_LOGGED_OUT
            print(f"signed in as {account or '(account not detected)'}, partner org {org_id}")
            emit("session", state="logged_in", account=account, org=org_id)
            return EXIT_OK
        finally:
            await close_quietly(context)


async def do_login() -> int:
    """Visible Chrome on the dashboard; wait for the VA to sign in, then close it.

    Polls the dashboard over the same cookies every few seconds. The moment it
    answers with an organization the session is real, and Chrome is closed
    for the VA -- they never have to close it themselves. Closing the window
    first means they gave up, and the indicator stays red.
    """
    async with async_playwright() as p:
        context = await open_profile(p, headless=False)
        if context is None:
            return EXIT_PROFILE_BUSY

        closed = asyncio.Event()
        context.on("close", lambda *_: closed.set())
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            # The last tab going away means the window was closed, even when
            # Chrome itself has not quite exited yet.
            page.on("close", lambda *_: closed.set() if not context.pages else None)
            try:
                await page.goto(DASHBOARD_ROOT, wait_until="domcontentloaded")
                await page.bring_to_front()
            except Exception:
                pass
            headers = await browser_headers(context, page)

            print("Chrome is open on the Shopify login page. Sign in there; it closes "
                  "by itself once you are through.")
            emit("login_window_open")

            deadline = time.monotonic() + LOGIN_WAIT_SEC
            while time.monotonic() < deadline:
                try:
                    await asyncio.wait_for(closed.wait(), LOGIN_POLL_SEC)
                except asyncio.TimeoutError:
                    pass
                if closed.is_set():
                    print("The login window was closed before signing in.")
                    emit("session", state="logged_out", reason="closed")
                    return EXIT_LOGIN_ABORTED
                try:
                    org_id, account = await discover_session(context, headers)
                except NotLoggedIn:
                    continue
                except Exception:
                    # Mid-login pages and a closing browser both land here;
                    # neither is a verdict, so just look again.
                    continue

                print(f"Signed in as {account or '(account not detected)'} -- closing Chrome.")
                emit("session", state="logged_in", account=account, org=org_id)
                return EXIT_OK

            print(f"Gave up waiting after {LOGIN_WAIT_SEC // 60} minutes. Click the "
                  "indicator to try again.")
            emit("session", state="logged_out", reason="timeout")
            return EXIT_LOGIN_ABORTED
        finally:
            await close_quietly(context)


async def do_run() -> int:
    """Headless: open every collaborator store and write the report."""
    async with async_playwright() as p:
        context = await open_profile(p, headless=True)
        if context is None:
            return EXIT_PROFILE_BUSY

        try:
            headers = await browser_headers(context)

            # Strict: no session, no run. The window -- not this process --
            # puts a login in front of the VA.
            try:
                org_id, account = await discover_session(context, headers)
            except NotLoggedIn as e:
                print(f"{e}\nNothing was opened. Log in from the indicator at the top "
                      "right, then run again.")
                emit("session", state="logged_out")
                return EXIT_LOGGED_OUT
            emit("session", state="logged_in", account=account, org=org_id)

            print(f"\nsigned in as: {account or '(account not detected)'}")
            print(f"partner org : {org_id}")
            print(f"report      : {report_path(org_id, account).name}")

            print("\n=== PHASE 1: inventory ===")
            emit("phase", name="inventory")
            t0 = time.time()
            listed = await fetch_inventory(context, headers, org_id)
            stores = [s for s in listed if is_collaboration(s)]
            others = [s for s in listed if not is_collaboration(s)]
            print(f"  {len(stores)} collaborator stores in {time.time() - t0:.1f}s")
            if others:
                print(f"  {len(others)} of the org's own stores left alone: {describe_types(others)}")

            # Everything openable gets opened, every run -- see "No cache" above.
            todo: List[Store] = []
            for s in stores:
                # No login link => the dashboard itself offers no way in.
                if not s.login_path or s.status.strip().lower() in SKIP_STATUSES:
                    s.outcome = SKIPPED_EXPIRED
                    continue
                todo.append(s)

            print("\n=== PHASE 2: open ===")
            print(f"  opening        : {len(todo)}")
            print(f"  access revoked : {sum(1 for s in stores if s.outcome == SKIPPED_EXPIRED)}")
            emit("phase", name="open", total=len(todo))

            if todo:
                t0 = time.time()
                sem = asyncio.Semaphore(MAX_PARALLEL)
                done = 0
                for coro in asyncio.as_completed([open_store(context, s, headers, sem) for s in todo]):
                    s = await coro
                    done += 1
                    mark = "ok  " if s.outcome in (OPENED, OPENED_INACTIVE) else "FAIL"
                    print(f"  [{done:>3}/{len(todo)}] {mark} {(s.domain or s.name)[:38]:<40} "
                          f"{s.handle or s.outcome}")
                    emit("progress", done=done, total=len(todo))
                print(f"  opened in {time.time() - t0:.1f}s")

            report = write_report(stores, org_id, account, others)

            failed = [s for s in stores if s.outcome in (FAILED_NO_ACCESS, FAILED_UNKNOWN)]
            expired = [s for s in stores if s.outcome == SKIPPED_EXPIRED]
            print(f"\n[Done] {len(stores)} stores")
            if failed:
                print(f"  {len(failed)} NEED ATTENTION:")
                for s in failed:
                    print(f"    - {s.domain or s.name}  ({s.outcome})")
            else:
                print("  every openable store opened")
            if expired:
                print(f"  {len(expired)} expired collaboration(s) cannot be opened "
                      f"-- see the report, they need re-requesting")
            print(f"  report: {report}")

            emit("result",
                 stores=len(stores),
                 opened=sum(1 for s in stores if s.outcome == OPENED),
                 dormant=sum(1 for s in stores if s.outcome == OPENED_INACTIVE),
                 failed=len(failed),
                 revoked=len(expired),
                 report=str(report))
            return EXIT_SOME_FAILED if failed else EXIT_OK

        finally:
            await close_quietly(context)


MODES = {"check": do_check, "login": do_login, "run": do_run}


def main(argv: Optional[List[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    mode = args[0] if args else "run"
    entry = MODES.get(mode)
    if entry is None:
        print(f"unknown mode {mode!r}; expected one of {', '.join(MODES)}")
        return EXIT_ERROR
    try:
        return asyncio.run(entry())
    except KeyboardInterrupt:
        return 130
    except NotLoggedIn as e:
        print(e)
        emit("session", state="logged_out")
        return EXIT_LOGGED_OUT
    except RuntimeError as e:
        print(f"[Error] {e}")
        emit("error", message=str(e))
        return EXIT_ERROR
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        emit("error", message=f"{type(e).__name__}: {e}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
