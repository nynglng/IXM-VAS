# IXM-VAS setup: installs (or repairs) the app for the current Windows user.
#
#   Double-click SETUP.cmd, or paste into PowerShell:
#   irm https://raw.githubusercontent.com/nynglng/IXM-VAS/main/setup.ps1 | iex
#
# Installs to %LOCALAPPDATA%\IXM-VAS and adds IXM-VAS to the Desktop and Start
# menu. Safe to run again at any time: it refreshes the app files and keeps the
# VA's Chrome profile (their Shopify login) and reports.
#
# Keep this file ASCII: Windows PowerShell 5.1 reads a .ps1 without a BOM as
# ANSI, and one curly quote breaks the parse.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest crawls with its progress bar on

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Yellow
}

function Find-Python {
    # Python 3.10+ with tkinter. The Microsoft Store "python" alias prints
    # nothing useful and fails the probe, so it is skipped naturally.
    # No double quotes in the probe: PowerShell 5.1 does not escape them when
    # passing arguments to a native program, and the probe silently breaks.
    $probe = 'import sys, tkinter; assert sys.version_info >= (3, 10); print(sys.executable)'
    $tries = @(
        @('py', '-3'),
        @('python'),
        @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe')),
        @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'))
    )
    foreach ($t in $tries) {
        try {
            $exe = $t[0]
            $rest = @($t | Select-Object -Skip 1)
            $out = & $exe @rest -c $probe 2>$null
            $path = ($out | Select-Object -Last 1)
            if ($LASTEXITCODE -eq 0 -and $path -and (Test-Path $path.Trim())) {
                return $path.Trim()
            }
        } catch { }
    }
    return $null
}

function Test-Chrome {
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if ($base -and (Test-Path (Join-Path $base 'Google\Chrome\Application\chrome.exe'))) {
            return $true
        }
    }
    return $false
}

function Install-IxmVas {
    $Repo = 'nynglng/IXM-VAS'
    $Branch = 'main'
    $InstallDir = Join-Path $env:LOCALAPPDATA 'IXM-VAS'
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    Write-Step "Installing IXM-VAS to $InstallDir"

    # --- 1. Python ---------------------------------------------------------
    $py = Find-Python
    if (-not $py) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Step 'Python not found. Installing Python 3.12 with winget...'
            winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
            $py = Find-Python
        }
        if (-not $py) {
            throw 'Python 3.10 or newer is required. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run setup again.'
        }
    }
    Write-Host "  Python: $py"

    # --- 2. App files ------------------------------------------------------
    Write-Step 'Downloading the latest version...'
    $tmp = Join-Path $env:TEMP ('ixm-vas-setup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $zip = Join-Path $tmp 'app.zip'
        Invoke-WebRequest -UseBasicParsing -Uri "https://codeload.github.com/$Repo/zip/refs/heads/$Branch" -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp 'x') -Force
        $src = (Get-ChildItem -Path (Join-Path $tmp 'x') -Directory | Select-Object -First 1).FullName
        if (-not (Test-Path (Join-Path $src 'ixm_vas\app.py'))) {
            throw 'The download does not look like IXM-VAS.'
        }

        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
        $manifest = @()
        foreach ($f in (Get-ChildItem -Path $src -Recurse -File)) {
            $rel = $f.FullName.Substring($src.Length + 1)
            $dest = Join-Path $InstallDir $rel
            New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
            Copy-Item -LiteralPath $f.FullName -Destination $dest -Force
            $manifest += $rel.Replace('\', '/')
        }
        # What this version installed: the in-app updater deletes whatever a
        # later version drops from this list.
        $updateDir = Join-Path $InstallDir '.update'
        New-Item -ItemType Directory -Force -Path $updateDir | Out-Null
        [IO.File]::WriteAllLines((Join-Path $updateDir 'manifest.txt'), [string[]]($manifest | Sort-Object))
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }

    # --- 3. The app's own Python environment -------------------------------
    $venvPy = Join-Path $InstallDir '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPy)) {
        Write-Step 'Creating the Python environment...'
        & $py -m venv (Join-Path $InstallDir '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Creating the Python environment failed.' }
    }
    Write-Step 'Installing requirements (a few minutes the first time)...'
    & $venvPy -m pip install --disable-pip-version-check -q -r (Join-Path $InstallDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Installing requirements failed.' }

    # --- 4. Browser --------------------------------------------------------
    if (-not (Test-Chrome)) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Step 'Google Chrome not found. Installing it with winget...'
            winget install -e --id Google.Chrome --silent --accept-package-agreements --accept-source-agreements
        }
        if (-not (Test-Chrome)) {
            Write-Step 'Using the Chromium that comes with Playwright instead...'
            & $venvPy -m playwright install chromium
            if ($LASTEXITCODE -ne 0) { throw 'Installing Chromium failed.' }
        }
    }

    # --- 5. Shortcuts ------------------------------------------------------
    $pyw = Join-Path $InstallDir '.venv\Scripts\pythonw.exe'
    $launcher = Join-Path $InstallDir 'IXM-VAS.pyw'
    $wsh = New-Object -ComObject WScript.Shell
    foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        $lnk = $wsh.CreateShortcut((Join-Path $dir 'IXM-VAS.lnk'))
        $lnk.TargetPath = $pyw
        $lnk.Arguments = '"' + $launcher + '"'
        $lnk.WorkingDirectory = $InstallDir
        $lnk.IconLocation = (Join-Path $InstallDir 'assets\ixm-vas.ico')
        $lnk.Description = 'IXM-VAS'
        $lnk.Save()
    }

    Write-Step 'Done. IXM-VAS is on your Desktop and in the Start menu. Starting it now...'
    Start-Process -FilePath $pyw -ArgumentList ('"' + $launcher + '"') -WorkingDirectory $InstallDir
}

try {
    Install-IxmVas
} catch {
    Write-Host ''
    Write-Host "Setup failed: $($_.Exception.Message)" -ForegroundColor Red
}
