# Native crash debugging with WinDbg JIT

How to make native crashes in HushSnap (access violations in Qt6Gui,
onnxruntime, or any C/C++ code outside Python's reach) freeze at the fault
site inside WinDbg, with full heap readable — instead of dying silently or
being swallowed by faulthandler.

This is a **dev-machine only** setup. Production user machines are unaffected
(see "How it works" below).

## Why this exists

The crash under investigation is a use-after-free inside `Qt6Gui.dll`. It
needs heap memory to diagnose (dangling pointer, freed object's vtable), so a
post-mortem **full-memory dump** is required. The obvious approaches all fail
for an MSIX-packaged app:

- **WER LocalDumps** (registry `DumpType=2`) — MSIX apps crash via MoAppCrash
  whose `EtwNonCollectReason=1` skips classic LocalDumps. Unreliable.
- **procdump `-e -ma`** as an attached debugger — procdump v12 crashes itself
  with `0xc0000409` (`__fastfail`) when capturing native AVs, producing 0-byte
  dumps, AND because it attaches as the process debugger it suppresses WER
  reporting. Dead end.
- **faulthandler** (enabled at boot) — installs a fatal-exception handler that
  dumps the Python stack then re-raises. On Windows the re-raise does NOT
  reliably reach a JIT debugger; the process exits before WinDbg can attach.
  So even with WinDbg installed, faulthandler pre-empts it.

The working approach: register WinDbg as the system JIT debugger, and on
machines where that's true, **skip faulthandler entirely** so native AVs flow
through WER's unhandled-exception dispatch straight to WinDbg, which freezes
the process at the fault site. WinDbg then writes the full-memory dump from
its own (clean) process — no corrupted-heap-in-dumper problem.

## One-time setup (per dev machine)

```powershell
# 1. Install WinDbg
winget install Microsoft.WinDbg

# 2. Register it as the system JIT debugger (writes HKLM\...\AeDebug)
& "$env:LOCALAPPDATA\Microsoft\WindowsApps\WinDbgX.exe" -I

# 3. Tell WER to offer "debug" for HushSnap crashes (admin)
New-Item "HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\DebugApplications" -Force | Out-Null
New-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\DebugApplications" -Name HushSnap.exe -Value 1 -PropertyType DWord -Force

# 4. Uninstall any procdump monitoring task if present (it would grab the
#    debugger slot and suppress WER). The setup_procdump_monitoring.ps1
#    script was removed in favor of this WinDbg JIT approach; if a leftover
#    "HushSnapProcdumpCrashMonitor" task exists, unregister it directly:
Get-ScheduledTask -TaskName "HushSnapProcdumpCrashMonitor" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Get-Process procdump64 -ErrorAction SilentlyContinue | Stop-Process -Force
```

That's the whole opt-in: steps 1-2 (install WinDbg, register as JIT) are a
deliberate, admin-only action no ordinary user performs. There is no env var
or config key to set per session — once WinDbg is the registered JIT
debugger, native crashes defer to it automatically on every HushSnap launch.
Production user machines have no JIT debugger registered, so they keep
faulthandler + WER reporting unchanged.

## Verifying the chain with crashlib (standalone diagnostic)

Real native crashes are rare (the UAF didn't reproduce in 2000 stress runs).
To confirm the WinDbg chain works without waiting for a real crash, use the
standalone `crashlib` diagnostic — a tiny DLL that triggers a deterministic
native access violation. It is NOT part of the shipped app (not bundled by
`HushSnap.spec`, not referenced by app code).

```powershell
# 1. Build crashlib.dll (needs MSVC Build Tools; locates them via vswhere)
native\build_crashlib.bat
#   -> assets\crashlib.dll  (symbols: build\crashlib\crashlib.pdb)

# 2. Run the trigger in a separate Python process. The process will crash
#    with an access violation; WinDbg should pop and freeze at the fault.
python -c "import ctypes; ctypes.WinDLL(r'assets\crashlib.dll').trigger_crash()"
```

Expected in WinDbg:

```
Access violation - code c0000005 (!!! second chance !!!)
crashlib!trigger_crash+0x...:
    mov dword ptr [rax],0DEADh ds:00000000`00000000=????????
```

If WinDbg pops and freezes there, the JIT chain is wired correctly on this
machine: WinDbg is registered as the AeDebug debugger and will attach to an
unhandled native exception.

### What this test does — and does not — cover

This unpackaged test is a **prerequisite check**, not a full end-to-end proof.
It confirms the machine-side half (WinDbg registered as JIT). It does **not**
exercise the app-side half, because the `python -c` process never imports
HushSnap and so never runs the code this guide exists to verify:

- **`jit_debugger_configured()` is never called.** The gate that actually
  skips faulthandler on a WinDbg machine isn't invoked, so a bug in it (e.g.
  wrong registry path, always-False) would not be caught here.
- **The faulthandler-skip interaction isn't exercised.** A bare `python`
  process has faulthandler off by default, so "faulthandler is correctly
  skipped when WinDbg is registered" can't be observed.
- **The MSIX `MoAppCrash` pipeline isn't exercised.** A real crash inside the
  packaged app goes through WER's `MoAppCrash` (the same pipeline that skips
  classic LocalDumps); this test crashes a normal unpackaged process, which
  takes the ordinary unhandled-exception path. `MoAppCrash` *can* decline to
  auto-launch the JIT debugger for a packaged app unless it's registered under
  WER `DebugApplications` (setup step 3) — a behavior this test cannot surface.

So: passing this test means "WinDbg will catch native crashes on this machine,"
but **not** "the packaged HushSnap app will definitely hand its native crashes
to WinDbg." The remaining gap (does the packaged app's crash actually reach the
JIT debugger?) is only proven by a real native crash inside the running app, or
by temporarily bundling crashlib into the package and triggering it there.

For the latter (optional, when you need to be sure the packaged path works):
build crashlib, add a one-off `binaries=[(crashlib.dll, '.')]` entry to the
.spec, build the MSIX, and call `trigger_crash()` from inside the running app
via `ctypes` — the dll resolves under `sys._MEIPASS` (PyInstaller's `_internal/`
dir), not beside the exe. If WinDbg pops from inside the MSIX, the full path is
proven. Revert the .spec change afterward so release builds stay clean.

### Capturing a full-memory dump from the frozen WinDbg session

```
.dump /ma C:\tmp\hs_native_crash.dmp      # ~1.2 GB, full heap
```

Then later, offline:

```
windbg -z C:\tmp\hs_native_crash.dmp
!analyze -v
```

### Symbols

crashlib symbols are beside the build (`build\crashlib\crashlib.pdb`). For
HushSnap's own modules and onnxruntime, point WinDbg at the symbol cache and
any local symbol dirs:

```
.sympath srv*C:\symbols*https://msdl.microsoft.com/download/symbols;C:\Users\09333\Documents\GitHub\HushSnap\build\crashlib
.reload /f
```

(Qt release PDBs are not published; `!analyze -v` plus the surrounding Python
call stack usually localizes the fault enough without them.)

## How it works (mechanism)

A single condition gates the faulthandler skip
(`hushsnap.config.jit_debugger_configured`):

`HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AeDebug\Debugger` is
present and non-empty (WinDbg registered as the system JIT debugger).

When true, `faulthandler.enable()` is skipped in `HushSnap.py` (boot) and
`hushsnap/logging_config.py` (log redirect). Native AVs then reach WER's
unhandled-exception dispatch → JIT debugger (WinDbg) → frozen at fault site.

When false (production user machines — no JIT debugger registered),
faulthandler stays enabled and native crashes dump the Python stack to the
log then exit — the existing production behavior, with WER still reporting
to Partner Center. Zero impact on machines without WinDbg.

Installing WinDbg and running `windbg -I` is the deliberate opt-in: it's an
admin action no ordinary user performs, so no separate env var or config key
is needed. Failure is visible rather than silent — if WinDbg is installed but
a crash is still swallowed, the cause is right here (WinDbg not registered as
JIT), not a forgotten opt-in flag.

## Tearing it down

```powershell
# Unregister WinDbg as the JIT debugger — via System Properties, or by
# clearing the registry value:
Remove-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AeDebug" -Name Debugger -ErrorAction SilentlyContinue
```

