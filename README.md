# HashSnap Build Guide

## Prerequisites

- Run commands from the project root.
- Python and PyInstaller are installed.
- Inno Setup 6 is installed.

## 1) One-Command Build (Recommended)

Update `version.txt` first, then run the following command to generate the final installer:

PowerShell

```
powershell -ExecutionPolicy Bypass -File installer/build_installer.ps1
```

**This command will automatically:**

- Read the version from `version.txt`.
- Sync `APP_VERSION` in `HashSnap.py`.
- Build the executable via PyInstaller.
- Package the installer with the matching version.

**Output:**

- `dist/HashSnap.exe`

- `dist-installer\HashSnap-Setup.exe`

​	

## 2) Optional: Build EXE Only (Debug)

If you only need to test the executable without creating an installer, run:

PowerShell

```
pyinstaller --noconsole --onefile --clean HashSnap.py
```

*Note: This builds `dist/HashSnap.exe` only and does **not** sync the version from `version.txt`.*

**Output:**

- `dist/HashSnap.exe`

## 3) Optional: Add App to Startup

To test the application's startup behavior on your local machine:

PowerShell

```
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

