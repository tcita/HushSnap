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
machines where that's true AND the developer has opted in, **skip
faulthandler entirely** so native AVs flow through WER's unhandled-exception
dispatch straight to WinDbg, which freezes the process at the fault site.
WinDbg then writes the full-memory dump from its own (clean) process — no
corrupted-heap-in-dumper problem.

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

## Opt in per session (the "password")

The faulthandler-skip is gated on an env var so it only activates when you
deliberately enable it — never on a machine that just happens to have WinDbg,
never for other users:

```powershell
# Set once, persistently (writes HKCU\Environment). Applies to every new
# process after you re-sign-in or restart explorer.
setx HUSHSNAP_NATIVE_DEBUG 1
# Then sign out and back in (or restart "Windows Explorer" in Task Manager)
# so the new env block is picked up.
```

To turn it off: `setx HUSHSNAP_NATIVE_DEBUG ""` (and re-sign-in), or delete
the value via System Properties → Environment Variables.

`HUSHSNAP_NATIVE_DEBUG` MUST be a persistent (setx / System Properties) env
var, NOT a transient `$env:` in a shell. MSIX activation via
`shell:AppsFolder` does not inherit the launching shell's transient env, but
it DOES inherit persistent user/machine env vars. (Verified: a MSIX-activated
HushSnap process reads `HUSHSNAP_NATIVE_DEBUG` set via setx after re-sign-in.)

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

# 2. Make sure HUSHSNAP_NATIVE_DEBUG is set and you've re-signed-in (above).

# 3. Run the trigger in a separate Python process. The process will crash
#    with an access violation; WinDbg should pop and freeze at the fault.
python -c "import ctypes; ctypes.WinDLL(r'assets\crashlib.dll').trigger_crash()"
```

Expected in WinDbg:

```
Access violation - code c0000005 (!!! second chance !!!)
crashlib!trigger_crash+0x...:
    mov dword ptr [rax],0DEADh ds:00000000`00000000=????????
```

If WinDbg pops and freezes there, the chain works. A real native crash
(anywhere in Qt6Gui/onnxruntime/etc.) is caught by the identical path.

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

Two conditions together gate the faulthandler skip
(`hushsnap.config.native_debug_deferred_to_jit`):

1. `jit_debugger_configured()` — `HKLM\SOFTWARE\Microsoft\Windows NT\
   CurrentVersion\AeDebug\Debugger` is present and non-empty (WinDbg
   registered as JIT debugger).
2. `HUSHSNAP_NATIVE_DEBUG` env var is non-empty (developer opt-in).

When both are true, `faulthandler.enable()` is skipped in `HushSnap.py` (boot)
and `hushsnap/logging_config.py` (log redirect). Native AVs then reach WER's
unhandled-exception dispatch → JIT debugger (WinDbg) → frozen at fault site.

When either is false (production user machines have no WinDbg; dev machines
without the env var), faulthandler stays enabled and native crashes dump the
Python stack to the log then exit — the existing production behavior, with
WER still reporting to Partner Center. Zero impact on non-opted-in machines.

## Tearing it down

```powershell
# Stop deferring to WinDbg
setx HUSHSNAP_NATIVE_DEBUG ""          # then re-sign-in

# (Optional) unregister WinDbg as JIT debugger — via System Properties or by
# clearing HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AeDebug\Debugger
```
