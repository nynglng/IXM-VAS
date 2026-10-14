# IXM-VAS

The desktop workplace for IXM's virtual assistants. Version 1 does one job:
**INITIALIZE ALL SHOPIFY STORES**, which opens every Shopify collaborator store
for the VA's own Shopify Partners account, so direct
`admin.shopify.com/store/<handle>` links work for them.

## Install (VAs)

You need Windows 10 or 11. Setup installs Python and Google Chrome with winget
if they're missing.

1. Open PowerShell and paste:

   ```powershell
   irm https://raw.githubusercontent.com/nynglng/IXM-VAS/main/setup.ps1 | iex
   ```

   Or download this repo (**Code → Download ZIP**), extract it, and
   double-click **SETUP.cmd**.

2. Setup installs to `%LOCALAPPDATA%\IXM-VAS`, puts **IXM-VAS** on your Desktop
   and in the Start menu, and opens it.

To repair an install, run setup again. Your Shopify login and reports are kept.

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

IXM-VAS checks this repo for a new version every minute. When one is out, the
window locks until you click **UPDATE NOW**. The update takes about a minute,
restarts the app, and keeps your login. If a run is in progress, the update
waits for it to finish.

## Releasing a new version (admin)

The `VERSION` file on `main` is the version every VA must run.

1. Commit your changes.
2. Bump `VERSION`, for example `1.0.0` → `1.0.1`.
3. Push to `main`.

Within a minute, every open IXM-VAS shows **UPDATE REQUIRED**. Pushes that
don't change `VERSION` don't reach anyone, so you can push work in progress
safely.

If you change `requirements.txt`, the updater reinstalls requirements on each
VA's machine during the update.

**This repo is public.** Don't commit credentials, service-account keys, client
store lists or reports. VA data (`chrome-profile/`, `reports/`) is gitignored,
and the updater never writes into those folders.

## Development

Run it from a clone:

```powershell
pip install -r requirements.txt
pythonw IXM-VAS.pyw
```

A git checkout won't update itself (it shows "dev checkout"). Use `git pull`
instead. Its Chrome profile is `chrome-profile/` in the checkout.

| Path | What it is |
|---|---|
| `IXM-VAS.pyw` | Entry point |
| `ixm_vas/app.py` | The window: login gate, run button, update lock |
| `ixm_vas/ui.py` | Dark theme: palette, fonts, rounded buttons and cards (pure tkinter) |
| `ixm_vas/updater.py` | Version check and self-update from this repo |
| `ixm_vas/tasks/shopify_open_all_stores.py` | The store opener. Runs as a separate worker process (`check` / `login` / `run`) |
| `ixm_vas/winframe.py` | Borderless window that keeps its taskbar button and minimize |
| `setup.ps1`, `SETUP.cmd` | Installer |

`shopify_open_all_stores.py` is a port of `ShopifyPartners_OpenAllStores.py`
from the private IXM repo. If Shopify redesigns the dashboard, fix the original
first (its `--discover` mode records the new endpoints), then port the fix here.
