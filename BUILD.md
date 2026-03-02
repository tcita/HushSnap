# HashSnap Build Instructions

## 1. PyInstaller (Build Single File EXE)

```powershell
# 在项目根目录执行
pyinstaller --noconsole --onefile --clean HashSnap.py
```

## 2. Inno Setup (Build Installer)

确保已安装 [Inno Setup](https://jrsoftware.org/isinfo.php)，并将 `ISCC.exe` 路径添加到环境变量（或手动指定）。

```powershell
# 使用命令行打包安装程序
iscc.exe .\installer\HashSnapInstaller.iss
```

或者运行 `installer/build_installer.ps1` 脚本：

```powershell
.\installer\build_installer.ps1
```

## 3. Post-Build: Add to Startup (Manual/Automatic)

### Automatic via PowerShell (Run once)

```powershell
$exe=(Resolve-Path '.\dist\HashSnap.exe').Path; $startup=[Environment]::GetFolderPath('Startup'); $lnk=Join-Path $startup 'HashSnap.lnk'; $w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut($lnk); $s.TargetPath=$exe; $s.WorkingDirectory=(Split-Path $exe); $s.IconLocation="$exe,0"; $s.Save(); Start-Process $exe
```

### Manual

将 `.\dist\HashSnap.exe` 的快捷方式放入 `shell:startup` 目录。
