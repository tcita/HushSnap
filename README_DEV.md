# HushSnap Developer Toolchain & Packaging Guide

This guide organizes and explains the developer scripts in the HushSnap project to help you compile, package, sign, and test the application efficiently.

---

## 🛠️ Root-Level Batch Entry Points

We have organized your routine development workflows into three clear, double-clickable `.bat` scripts in the repository root:

| Script Name | Purpose | Permissions | Output Location |
| :--- | :--- | :--- | :--- |
| 🟢 **[build_msix.bat](file:///c:/Users/09333/Documents/GitHub/HushSnap/build_msix.bat)** | Compiles the Python application (incrementally) and packages it into an unsigned MSIX. | User (Standard) | `dist-installer\HushSnap.msix` |
| ⚡ **[register_dev_msix.bat](file:///c:/Users/09333/Documents/GitHub/HushSnap/register_dev_msix.bat)** | **(Inner-Loop)** Instantly registers the unpacked `msix_stage` directory into the local Windows environment inside the MSIX container. No packaging, signing, or Admin needed! | User (Standard)<br/>*Requires Windows Developer Mode ON* | Local Windows registration |
| 🛡️ **[sign_for_local_test.bat](file:///c:/Users/09333/Documents/GitHub/HushSnap/sign_for_local_test.bat)** | **(Outer-Loop)** Creates/verifies a trusted self-signed certificate on the machine and signs the MSIX package so it can be installed via a standard user double-click. | **Administrator** | `dist-installer-test\HushSnap_Test_Signed.msix` |

---

## 🔄 Two-Track Packaging & Testing Workflows

Depending on what you are testing, use one of the two workflows below:

### Track A: Daily Development & Rapid Iteration (Recommended 🚀)
Use this track when you are actively writing Python code, tweaking the UI, or modifying manifest settings.

1. **Build layouts**: Double-click `build_msix.bat`.
   * *This compiles your Python code incrementally (only rebuilding changed modules, taking ~3-5 seconds) and outputs files to `build\msix_stage`.*
2. **Register instantly**: Double-click `register_dev_msix.bat`.
   * *This registers the staging directory directly into Windows. The app will immediately show up in your Start Menu as an installed MSIX package.*
3. **Iterate**: When you modify code, just repeat **Step 1**, and the changes will take effect automatically next time you launch the app (or run Step 2 again if you modified manifest assets).

### Track B: Final QA & Store Prep (Outer Loop 📦)
Use this track to verify the actual user-facing double-click installation flow, or to run the Windows App Certification Kit (WACK).

1. **Clean Rebuild**: Run `build_msix.bat -Rebuild` from PowerShell or Command Prompt.
   * *This bypasses PyInstaller incremental caches to perform a 100% clean rebuild.*
2. **Sign the package**: Right-click `sign_for_local_test.bat` and select **Run as Administrator**.
   * *This signs the MSIX and saves it in `dist-installer-test\HushSnap_Test_Signed.msix`.*
3. **Test double-click**: Double-click the signed `.msix` file to open the Windows App Installer UI and test installation.

---

## 📂 Core PowerShell Scripts (`installer/` Directory)

The batch files listed above are simple wrappers that delegate execution to more robust PowerShell scripts located in the `installer/` directory:

* 📄 **[installer/build_msix.ps1](file:///c:/Users/09333/Documents/GitHub/HushSnap/installer/build_msix.ps1)**
  * **Role**: Main packaging script — compiles Python code via PyInstaller and packages into MSIX.
  * **Feature**: Terminates running instances, runs PyInstaller compilation, generates multi-DPI assets from `ico.ico`, updates the `AppxManifest.xml` version based on `hushsnap/__init__.py`, packages into an MSIX via `makeappx`, and supports optional local code signing.
