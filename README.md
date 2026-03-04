# HashSnap Build Guide

## Prerequisites

- Run commands from the project root.
- Python and PyInstaller are installed and callable in your shell.
- Inno Setup 6 is installed.
- `ISCC.exe` is available (either callable in shell or passed via `-IsccPath`).

## 1) Build EXE

```powershell
pyinstaller --noconsole --onefile --clean HashSnap.py
```

Output:

- `dist/HashSnap.exe`

## 2) Build Installer

### Option A (recommended): Project script

```powershell
.\installer\build_installer.ps1
```

If `ISCC.exe` is not callable in your shell:

```powershell
.\installer\build_installer.ps1 -IsccPath "<full-path-to-ISCC.exe>"
```

### Option B: Direct command

```powershell
iscc.exe .\installer\HashSnapInstaller.iss
```

Output:

- `dist-installer/HashSnap-Setup.exe`

## 3) Optional: Add App to Startup

```powershell
$exe = (Resolve-Path '.\dist\HashSnap.exe').Path
$startup = [Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startup 'HashSnap.lnk'

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($linkPath)
$shortcut.TargetPath = $exe
$shortcut.WorkingDirectory = Split-Path $exe
$shortcut.IconLocation = "$exe,0"
$shortcut.Save()
```
