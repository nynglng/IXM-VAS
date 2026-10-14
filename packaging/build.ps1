# Builds dist\IXM-VAS-Setup.exe from this checkout:
#   1. a private venv in build\venv with the app's and the build's requirements
#   2. PyInstaller  -> dist\IXM-VAS\  (IXM-VAS.exe + ixm-vas-worker.exe)
#   3. Inno Setup 6 -> dist\IXM-VAS-Setup.exe, plus the .sha256 the in-app
#      updater checks every download against
#
# The Release workflow runs this on GitHub; it runs the same on a PC:
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#
# Keep this file ASCII: Windows PowerShell 5.1 reads a .ps1 without a BOM as ANSI.

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$version = (Get-Content VERSION -Raw).Trim()
if ($version -notmatch '^\d+(\.\d+)*$') { throw "VERSION must look like 1.2.3, not '$version'" }
Write-Host "Building IXM-VAS $version"

# --- 1. Build environment ----------------------------------------------------
$python = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
$venv = Join-Path $Root 'build\venv'
$vpy = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $vpy)) {
    & $python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Creating the build venv failed.' }
}
& $vpy -m pip install --disable-pip-version-check -q -r requirements.txt -r packaging\requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Installing build requirements failed.' }

# --- 2. PyInstaller ----------------------------------------------------------
& $vpy -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build\pyinstaller packaging\ixm-vas.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

# --- 3. Inno Setup -----------------------------------------------------------
$iscc = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 is not installed. Get it with: winget install JRSoftware.InnoSetup' }
& $iscc /Q "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed.' }

$setup = Join-Path $Root 'dist\IXM-VAS-Setup.exe'
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
[IO.File]::WriteAllText("$setup.sha256", "$hash  IXM-VAS-Setup.exe`n")
$mb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
Write-Host "Built $setup ($mb MB)"
Write-Host "sha256 $hash"
