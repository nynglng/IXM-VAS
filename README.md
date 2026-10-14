# IXM-VAS

The desktop workplace for IXM's virtual assistants. Version 1 does one job:
**INITIALIZE ALL SHOPIFY STORES**, which opens every Shopify collaborator store
for the VA's own Shopify Partners account, so direct
`admin.shopify.com/store/<handle>` links work for them.

## Install (VAs)

You need Windows 10 or 11 and [Google Chrome](https://www.google.com/chrome/).

1. Download **[IXM-VAS-Setup.exe](https://github.com/nynglng/IXM-VAS/releases/latest/download/IXM-VAS-Setup.exe)**.
2. Run it and click through. No admin password is needed. It adds IXM-VAS to
   your Start menu and, if you leave the box ticked, your Desktop.

The installer isn't code-signed yet, so Windows may warn you:

- **Your browser** may say the file "isn't commonly downloaded". Choose
  **Keep**.
- **Windows** may show "Windows protected your PC". Click **More info**, then
  **Run anyway**.

To uninstall, go to **Settings → Apps → IXM-VAS**. Uninstalling keeps your
Shopify login, so a reinstall picks up where you left off.

## Using it

| Shopify pill (top right) | Meaning | Click it to |
|---|---|---|
| **LOGGED OUT** (red) | Your profile isn't signed in to dev.shopify.com | Open Chrome on the Shopify login. Sign in; Chrome closes by itself and the pill turns green. |
| **LOGGED IN** (green) | Signed in, and the Partners dashboard answers | Check the login again |
| **CHECKING…** / **WAITING FOR LOGIN…** (amber) | Busy | (nothing) |
| **UNREACHABLE** / **CHROME IN USE** (red) | No internet, or another Chrome window is using your IXM-VAS profile | Try again |

**INITIALIZE ALL SHOPIFY STORES** works only while the pill is green. A run
takes about two minutes. When it finishes, **Open report** shows the stores
that need attention, the collaborations whose access was revoked, and every
store's admin handle.

**Show log** (bottom right) shows exactly what the last task did. Send it to
your admin when something goes wrong.

### Your Chrome profile

IXM-VAS signs in through a Chrome profile of its own, at
`%LOCALAPPDATA%\IXM-VAS\chrome-profile`. It isn't your everyday Chrome, so
logging in there doesn't affect your normal browsing. Each VA logs in with
their own account. Shopify grants "opened" per person, not per team, so every
VA needs to run it for themselves.

### Updates

IXM-VAS checks for a new version every minute. When one is out, the window
locks until you click **UPDATE NOW**. The app downloads the new version,
closes, installs it, and reopens by itself in about a minute. Your login is
kept. If a run is in progress, the update waits for it to finish.

## Releasing a new version (admin)

1. Commit your changes.
2. Bump `VERSION`, for example `1.0.0` → `1.0.1`.
3. Push to `main`.

The **Release** workflow (`.github/workflows/release.yml`) builds
`IXM-VAS-Setup.exe` on GitHub and publishes it as release `v1.0.1`. That
takes about 5–10 minutes; watch it in the repo's **Actions** tab. Within a
minute of the release appearing, every open IXM-VAS shows **Update
required**.

Pushes that don't change `VERSION` don't build anything or reach anyone, so
you can push work in progress safely. To rebuild without a new version, run
the workflow by hand from the Actions tab. It skips any version that's already
released.

**This repo is public.** Don't commit credentials, service-account keys, client
store lists or reports.

## Development

Run it from a clone:

```powershell
pip install -r requirements.txt
pythonw IXM-VAS.pyw
```

A copy running from source never updates itself (the footer says "running
from source"). Use `git pull`. Its Chrome profile and reports live in the
checkout and are gitignored.

To build the installer locally, install [Inno Setup 6](https://jrsoftware.org/isinfo.php)
(`winget install JRSoftware.InnoSetup`), then run:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The output is `dist\IXM-VAS-Setup.exe`.

| Path | What it is |
|---|---|
| `IXM-VAS.pyw` | Entry point for the window |
| `ixm_vas/app.py` | The window: login gate, run button, update lock |
| `ixm_vas/ui.py` | Dark theme: palette, fonts, rounded buttons and cards (pure tkinter) |
| `ixm_vas/updater.py` | Checks GitHub Releases; downloads and starts the new setup |
| `ixm_vas/worker.py` | Entry point for task processes (`ixm-vas-worker.exe` when installed) |
| `ixm_vas/tasks/shopify_open_all_stores.py` | The store opener: `check` / `login` / `run` |
| `ixm_vas/winframe.py` | Borderless window that keeps its taskbar button and minimize |
| `ixm_vas/paths.py` | Where the program and the VA's data live, installed or from source |
| `packaging/` | PyInstaller spec, Inno Setup script, `build.ps1` |

`shopify_open_all_stores.py` is a port of `ShopifyPartners_OpenAllStores.py`
from the private IXM repo. If Shopify redesigns the dashboard, fix the original
first (its `--discover` mode records the new endpoints), then port the fix here.
