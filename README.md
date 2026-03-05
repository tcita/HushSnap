# HashSnap Build Guide

## Prerequisites

- Run commands from the project root.
- Python and PyInstaller are installed and callable in your shell.
- Inno Setup 6 is installed.
- `ISCC.exe` is available (either callable in shell or passed via `-IsccPath`).

## 1) One-Command Build (recommended)

Update `version.txt` first, then run:

```powershell
powershell -ExecutionPolicy Bypass -File installer/build_installer.ps1
```

This command will:

- Read version from `version.txt`
- Sync `APP_VERSION` in `HashSnap.py`
- Build EXE via PyInstaller
- Build installer with the same version

Use this as the default release command.

If `ISCC.exe` or `pyinstaller` are not in PATH:

```powershell
powershell -ExecutionPolicy Bypass -File installer/build_installer.ps1 -IsccPath "<full-path-to-ISCC.exe>" -PyInstallerPath "<full-path-to-pyinstaller.exe>"
```

## 2) Optional: Build EXE Only (debug)

```powershell
pyinstaller --noconsole --onefile --clean HashSnap.py
```

This is an optional debug step. It is not required before `powershell -ExecutionPolicy Bypass -File installer/build_installer.ps1`.  
This builds `dist/HashSnap.exe` only.  
It does **not** sync version from `version.txt`.

Output:

- `dist/HashSnap.exe`

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
