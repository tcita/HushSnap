<!-- 项目说明文档 -->
# HashSnap Build Guide

## Prerequisites

- Run commands from the project root.
- Python and PyInstaller are installed.
- Inno Setup 6 is installed.

## 1) One-Command Build (Recommended)

Update `hashsnap/__init__.py` first (the `__version__` value), then run the following command to generate the final installer:

PowerShell

```powershell
powershell -ExecutionPolicy Bypass -File installer/build_installer.ps1
```

**This command will automatically:**

- Read the version from `hashsnap/__init__.py`.
- Build the app via PyInstaller as a `onedir` bundle based on `HashSnap.spec`.
- Package the entire `dist\HashSnap\` folder into the installer with the matching version.

**Output:**

- `dist\HashSnap\HashSnap.exe`
- `dist\HashSnap\_internal\...` (runtime dependencies and bundled resources)
- `dist-installer\HashSnap-Setup.exe`

## 2) Optional: Build EXE Only (Debug)

If you only need to test the packaged app without creating an installer, run:

PowerShell

```powershell
pyinstaller --clean HashSnap.spec
```

*Note: This creates a `onedir` app folder at `dist\HashSnap\` and uses the version already defined in `hashsnap/__init__.py`.*

**Output:**

- `dist\HashSnap\HashSnap.exe`
- `dist\HashSnap\_internal\...`

## 3) Optional: Add App to Startup

To test the application's startup behavior on your local machine:

PowerShell

```powershell
$exe = (Resolve-Path '.\dist\HashSnap\HashSnap.exe').Path
$startup = [Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startup 'HashSnap.lnk'

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($linkPath)
$shortcut.TargetPath = $exe
$shortcut.WorkingDirectory = Split-Path $exe
$shortcut.IconLocation = "$exe,0"
$shortcut.Save()
```

