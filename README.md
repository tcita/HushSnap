# HushSnap Build Guide

## Prerequisites

- Run commands from the project root.
- Python and PyInstaller are installed.
- Inno Setup 6 is installed.

## 1) One-Command Build (Recommended)

Update `HushSnap/__init__.py` first (the `__version__` value), then run the following command to generate the final installer:

PowerShell

```powershell
powershell -ExecutionPolicy Bypass -File installer/build_installer.ps1
```

**This command will automatically:**

- Read the version from `HushSnap/__init__.py`.
- Build the app via PyInstaller as a `onedir` bundle based on `HushSnap.spec`.
- Package the entire `dist\HushSnap\` folder into the installer with the matching version.

**Output:**

- `dist\HushSnap\HushSnap.exe`
- `dist\HushSnap\_internal\...` (runtime dependencies and bundled resources)
- `dist-installer\HushSnap-Setup.exe`

## 2) Optional: Build EXE Only (Debug)

If you only need to test the packaged app without creating an installer, run:

PowerShell

```powershell
pyinstaller --clean HushSnap.spec
```

*Note: This creates a `onedir` app folder at `dist\HushSnap\` and uses the version already defined in `HushSnap/__init__.py`.*

**Output:**

- `dist\HushSnap\HushSnap.exe`
- `dist\HushSnap\_internal\...`

## 3) Optional: Add App to Startup

To test the application's startup behavior on your local machine:

PowerShell

```powershell
$exe = (Resolve-Path '.\dist\HushSnap\HushSnap.exe').Path
$startup = [Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startup 'HushSnap.lnk'

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($linkPath)
$shortcut.TargetPath = $exe
$shortcut.WorkingDirectory = Split-Path $exe
$shortcut.IconLocation = "$exe,0"
$shortcut.Save()
```


