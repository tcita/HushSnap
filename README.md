# HushSnap

HushSnap is a global hotkey-driven screenshot tool designed for speed and silence. Capture any screen region or the full screen instantly and have it saved to your clipboard.

## 🛠 Development & Debugging

### Run from Source
To run the application directly from the source code:
```powershell
python HushSnap.py
```

### Debug Mode
Use the `--debug` flag to enable verbose logging and automatically open the log directory in Windows Explorer:
```powershell
# From source
python HushSnap.py --debug

# From built executable
.\dist\HushSnap\HushSnap.exe --debug
```
**Key Features of Debug Mode:**
- **Isolation:** Running from source uses `%LOCALAPPDATA%\HushSnap_Dev`, ensuring your production settings remain untouched.
- **Traceability:** Sets log level to `DEBUG` and opens the log folder immediately upon startup.
- **Live Output:** Real-time logs are streamed to the terminal via the logging console handler (`StreamHandler`).

---

## 🏗 Build Guide

### Prerequisites
- Run commands from the project root.
- Python and PyInstaller are installed.
- Inno Setup 6 is installed.

### 1) One-Command Build (Recommended)
Update `hushsnap/__init__.py` first (the `__version__` value), then run the following command to generate the final installer:

```powershell
powershell -ExecutionPolicy Bypass -File installer/build_installer.ps1
```

**This command will automatically:**
- Read the version from `hushsnap/__init__.py`.
- Build the app via PyInstaller as a `onedir` bundle based on `HushSnap.spec`.
- Package the entire `dist\HushSnap\` folder into the installer.

**Output:**
- `dist\HushSnap\HushSnap.exe`
- `dist-installer\HushSnap-Setup.exe`

### 2) Optional: Build EXE Only
If you only need to test the packaged app without creating an installer, run:
```powershell
pyinstaller --clean HushSnap.spec
```

### 3) Optional: Add App to Startup (Manual Test)
To test the application's startup behavior on your local machine:
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

---

