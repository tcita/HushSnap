; HashSnap installer (Inno Setup)
; Build: ISCC installer\HashSnapInstaller.iss

#define MyAppName "HashSnap"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "HashSnap"
#define MyAppExeName "HashSnap.exe"
#define MyConfigName "hashsnap_config.json"
#define MyLogName "hashsnap_capture_error.log"

[Setup]
AppId={{A53D0F97-1664-4740-A22D-4A0A8DE5C30A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
CloseApplications=yes
OutputDir=..\dist-installer
OutputBaseFilename=HashSnap-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
ShowLanguageDialog=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

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
Source: "..\dist\{#MyConfigName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\{#MyLogName}"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{autodesktop}\{cm:UninstallApp}"; Filename: "{uninstallexe}"
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: startup
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallApp}"; Filename: "{uninstallexe}"

[UninstallDelete]
Type: files; Name: "{autodesktop}\卸载 HashSnap.lnk"
Type: files; Name: "{userstartup}\HashSnap.lnk"
Type: files; Name: "{app}\hashsnap_config.json"
Type: files; Name: "{app}\hashsnap_capture_error.log"
Type: dirifempty; Name: "{app}"

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /F /T /IM HashSnap.exe >nul 2>&1"; Flags: runhidden waituntilterminated; RunOnceId: "KillHashSnap"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchNow}"; Flags: nowait postinstall skipifsilent






