; HashSnap installer (Inno Setup)
; Build: ISCC installer\HashSnapInstaller.iss

#define MyAppName "HashSnap"
#ifndef MyAppVersion
#define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "HashSnap"
#define MyAppExeName "HashSnap.exe"
#define MyConfigName "hashsnap_config.json"
#define MyLogName "hashsnap_capture_debug.log"
#define MyInstallerLangHintName "hashsnap_installer_lang.txt"

[Setup]
AppId={{A53D0F97-1664-4740-A22D-4A0A8DE5C30A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppName}
UsePreviousAppDir=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=no
DirExistsWarning=no
PrivilegesRequired=lowest
UsePreviousPrivileges=no
; Avoid Restart Manager close flow freezing on tray-only apps.
CloseApplications=no
RestartApplications=no
OutputDir=..\dist-installer
OutputBaseFilename=HashSnap-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; Keep this enabled so the app is listed in Control Panel > Programs and Features
CreateUninstallRegKey=yes
Uninstallable=yes
ShowLanguageDialog=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimp"; MessagesFile: "{#SourcePath}\languages\ChineseSimplified.isl"

[CustomMessages]
english.AdditionalTasks=Additional tasks:
english.CreateDesktopIcon=Create a desktop shortcut
english.LaunchOnStartup=Start with Windows
english.LaunchNow=Launch now
english.UninstallApp=Uninstall {#MyAppName}
chinesesimp.AdditionalTasks=附加任务:
chinesesimp.CreateDesktopIcon=把快捷方式加入桌面
chinesesimp.LaunchOnStartup=开机自启
chinesesimp.LaunchNow=立即启动
chinesesimp.UninstallApp=卸载 {#MyAppName}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalTasks}"; Flags: unchecked
Name: "startup"; Description: "{cm:LaunchOnStartup}"; GroupDescription: "{cm:AdditionalTasks}"

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\{#MyConfigName}"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\{#MyLogName}"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist skipifsourcedoesntexist

[Registry]
; Standard Inno Setup CreateUninstallRegKey=yes will handle this in HKCU when PrivilegesRequired=lowest.
[InstallDelete]
Type: files; Name: "{group}\Uninstall {#MyAppName}.lnk"
Type: files; Name: "{group}\卸载 {#MyAppName}.lnk"
Type: files; Name: "{group}\{#MyAppName} Uninstall Tool (Direct).lnk"

[Icons]
Name: "{app}\{cm:UninstallApp}"; Filename: "{app}\unins000.exe"; WorkingDir: "{app}"; IconFilename: "{app}\unins000.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{autostartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: startup
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallApp}"; Filename: "{app}\unins000.exe"; WorkingDir: "{app}"

[UninstallDelete]
Type: files; Name: "{autostartup}\HashSnap.lnk"
Type: files; Name: "{app}\hashsnap_config.json"
Type: files; Name: "{app}\hashsnap_capture_debug.log"
Type: files; Name: "{app}\hashsnap_capture_error.log"
Type: files; Name: "{app}\{#MyInstallerLangHintName}"
Type: dirifempty; Name: "{app}"

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /F /T /IM HashSnap.exe >nul 2>&1"; Flags: runhidden waituntilterminated; RunOnceId: "KillHashSnap"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchNow}"; Flags: nowait postinstall skipifsilent

[Code]
function ForceKillHashSnap(): Boolean;
var
  ResultCode: Integer;
begin
  Exec(
    ExpandConstant('{cmd}'),
    '/C taskkill /F /T /IM {#MyAppExeName} >nul 2>&1',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  Sleep(300);
  Result := True;
end;

function GetUiLangHintValue(): String;
begin
  if CompareText(ActiveLanguage(), 'chinesesimp') = 0 then
    Result := 'zh'
  else
    Result := 'en';
end;

procedure WriteUiLangHintFile();
var
  HintPath: String;
begin
  HintPath := ExpandConstant('{app}\{#MyInstallerLangHintName}');
  SaveStringToFile(HintPath, GetUiLangHintValue(), False);
end;

function InitializeSetup(): Boolean;
begin
  ForceKillHashSnap();
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    ForceKillHashSnap();
  if CurStep = ssPostInstall then
    WriteUiLangHintFile();
end;
