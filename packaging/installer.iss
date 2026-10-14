; IXM-VAS installer (Inno Setup 6). Built by packaging/build.ps1:
;   ISCC /DAppVersion=1.2.3 packaging\installer.iss
;
; Per-user, no admin prompt. The program goes to %LOCALAPPDATA%\Programs\IXM-VAS
; and is replaced wholesale by every version. The VA's own data -- their
; Chrome profile (Shopify login) and reports -- is in %LOCALAPPDATA%\IXM-VAS,
; which this installer never touches: not on update, not on uninstall.
;
; The app's UPDATE NOW runs this with /SILENT /UPDATE=1 and then exits; see
; InitializeSetup for how the two hand over.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

; Must match INSTALLED_MUTEX in ixm_vas/app.py.
#define AppMutex "Local\IXM-VAS-app"

[Setup]
AppId={{3F8E81DE-11B2-4464-9C17-30785B91D49D}
AppName=IXM-VAS
AppVersion={#AppVersion}
AppVerName=IXM-VAS {#AppVersion}
AppPublisher=IXM
AppPublisherURL=https://github.com/nynglng/IXM-VAS
AppSupportURL=https://github.com/nynglng/IXM-VAS
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\IXM-VAS
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=IXM-VAS-Setup
SetupIconFile=..\assets\ixm-vas.ico
UninstallDisplayIcon={app}\IXM-VAS.exe
UninstallDisplayName=IXM-VAS
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The app is waited for in InitializeSetup instead of Restart Manager prompts.
CloseApplications=no
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; Every version ships a complete program folder. Clear the previous one first
; so nothing from an older version is left behind to be loaded by mistake.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\IXM-VAS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\IXM-VAS"; Filename: "{app}\IXM-VAS.exe"
Name: "{autodesktop}\IXM-VAS"; Filename: "{app}\IXM-VAS.exe"; Tasks: desktopicon

[Run]
; Runs after silent updates too (no skipifsilent): that is what reopens the
; app after UPDATE NOW.
Filename: "{app}\IXM-VAS.exe"; Description: "Start IXM-VAS"; Flags: nowait postinstall

[Code]
const
  AppMutex = '{#AppMutex}';
  ChromePage = 'https://www.google.com/chrome/';

function ChromeInstalled(): Boolean;
var
  P: String;
begin
  Result :=
    RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe', '', P) or
    RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe', '', P) or
    FileExists(ExpandConstant('{commonpf64}\Google\Chrome\Application\chrome.exe')) or
    FileExists(ExpandConstant('{commonpf32}\Google\Chrome\Application\chrome.exe')) or
    FileExists(ExpandConstant('{localappdata}\Google\Chrome\Application\chrome.exe'));
end;

function InitializeSetup(): Boolean;
var
  Waited, ErrorCode: Integer;
begin
  Result := True;

  if ExpandConstant('{param:UPDATE|0}') = '1' then
  begin
    { Started by the app's UPDATE NOW, which closes right after. Its mutex is
      freed by Windows only once the process is gone, so wait for that, then
      a moment more for the program files to be let go. }
    Waited := 0;
    while CheckForMutexes(AppMutex) and (Waited < 60000) do
    begin
      Sleep(250);
      Waited := Waited + 250;
    end;
    Sleep(1500);
  end;

  while CheckForMutexes(AppMutex) do
  begin
    if WizardSilent() then
    begin
      Result := False;
      Exit;
    end;
    if MsgBox('IXM-VAS is open. Close it, then click OK to continue.',
              mbError, MB_OKCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
  end;

  if (not WizardSilent()) and (not ChromeInstalled()) then
  begin
    if MsgBox('IXM-VAS signs in to Shopify through Google Chrome, which is not ' +
              'installed on this PC.' + #13#10#13#10 +
              'Setup will continue. Install Chrome before you use IXM-VAS.' + #13#10#13#10 +
              'Open the Chrome download page now?',
              mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', ChromePage, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  while CheckForMutexes(AppMutex) do
  begin
    if UninstallSilent() then
    begin
      Result := False;
      Exit;
    end;
    if MsgBox('IXM-VAS is open. Close it, then click OK to continue.',
              mbError, MB_OKCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
  end;
end;
