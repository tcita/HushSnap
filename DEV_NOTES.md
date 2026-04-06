# HushSnap Project Documentation & Performance Baselines

## Performance & Startup Diagnostics

### Startup Latency Findings (April 2026)
Detailed performance auditing has pinpointed the root cause of observed startup delays.

- **Initial Execution (First run after install):** ~4.5s to 15s.
  - **Reason:** Windows Defender / Heuristic AV scanning of the newly written `.exe` and its extracted DLLs (Qt6, etc.).
  - **PyInstaller Overhead:** The very first extraction of dependencies can be slowed by disk I/O and security software.
- **Normal Operation (Subsequent launches, including after PC REBOOT):** ~0.7s.
  - **Finding:** Once the application has been "vetted" by the OS and initial extraction is done, startup is near-instant, even from a cold boot.
  - **Metric:** `OS/Import overhead` drops from 4-5s down to <0.3s.

### Baseline Metrics (Reference)
- **Application Logic Init:** ~0.3s - 0.5s (Internal Python initialization, tray, and hotkeys).
- **OS/Import Overhead (First-run):** ~4.0s - 15.0s.
- **OS/Import Overhead (Standard):** ~0.2s - 0.5s.

### Diagnostic Logging
The application logs three core timing metrics at `INFO` level to `hushsnap_capture_debug.log` in `%LOCALAPPDATA%\HushSnap`:
1. `OS/Import overhead`: Time spent before reaching `main()` (OS loading, PyInstaller decompression).
2. `Application logic init`: Time spent inside `main()` setting up UI and hotkeys.
3. `Total wall-clock startup time`: The complete duration from process execution to event loop.

## Architectural Notes
- **Hotkey Management:** Uses a native Windows event filter to intercept `WM_HOTKEY` before Qt processing to ensure low-latency response.
- **OCR Service:** Initialized lazily or asynchronously to avoid blocking the main UI thread during startup.
