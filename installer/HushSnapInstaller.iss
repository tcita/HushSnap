; Inno Setup 安装器脚本。
; HushSnap installer (Inno Setup)
; Build: ISCC installer\HushSnapInstaller.iss

#define MyAppName "HushSnap"
#ifndef MyAppVersion
#define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "HushSnap"
#define MyAppExeName "HushSnap.exe"
#define MyConfigName "hushsnap_config.json"
#define MyLogName "hushsnap_capture_debug.log"
#define MyInstallerLangHintName "hushsnap_installer_lang.txt"

[Setup]
AppId={{916B0BB3-8426-497F-8C2B-74F342C02536}
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
OutputBaseFilename=HushSnap-Setup
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
Source: "..\dist\HushSnap\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\{#MyConfigName}"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\{#MyLogName}"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist skipifsourcedoesntexist

[Registry]
; Standard Inno Setup CreateUninstallRegKey=yes will handle this in HKCU when PrivilegesRequired=lowest.
; Explicitly add InstallLocation to help Windows 11 "Installed Apps" find the app.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{{916B0BB3-8426-497F-8C2B-74F342C02536}_is1"; ValueType: string; ValueName: "InstallLocation"; ValueData: "{app}"; Flags: uninsdeletevalue
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
Type: files; Name: "{autostartup}\HushSnap.lnk"
Type: files; Name: "{app}\hushsnap_config.json"
Type: files; Name: "{app}\hushsnap_capture_debug.log"
Type: files; Name: "{app}\hushsnap_capture_error.log"
Type: files; Name: "{app}\{#MyInstallerLangHintName}"
Type: dirifempty; Name: "{app}"

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /F /T /IM HushSnap.exe >nul 2>&1"; Flags: runhidden waituntilterminated; RunOnceId: "KillHushSnap"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchNow}"; Flags: nowait postinstall skipifsilent

[Code]
function ForceKillHushSnap(): Boolean;
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
  ForceKillHushSnap();
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    ForceKillHushSnap();
  if CurStep = ssPostInstall then
    WriteUiLangHintFile();
end;



