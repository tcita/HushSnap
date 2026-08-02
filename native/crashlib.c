/*
 * crashlib.dll — standalone native access-violation trigger.
 *
 * Purpose: verify the WinDbg JIT debugger chain (WER AeDebug → JIT attach)
 * catches a real native crash, without waiting for a rare production fault.
 * Built by native/build_crashlib.bat. NOT bundled by HushSnap.spec and NOT
 * referenced by any app code — it is a dev-only diagnostic. Invoke from a
 * separate Python process (never import HushSnap, so the test is isolated):
 *
 *     python -c "import ctypes; ctypes.WinDLL(r'assets/crashlib.dll').trigger_crash()"
 *
 * Expected: an access violation (0xC0000005) at crashlib!trigger_crash.
 * HushSnap's faulthandler (always on) will log the Python stack to the
 * log file. For a full debugger session, attach WinDbg manually or
 * configure WER LocalDumps via scripts/setup_wer_dumps.ps1.
 */

#include <windows.h>

/*
 * The fault address is a volatile global so the optimizer cannot prove at
 * compile time that the pointer is NULL and delete the dereference as
 * undefined behavior. A plain `volatile int *p = NULL; *p = 0xDEAD;` gets
 * optimized away by MSVC /O2 (it treats dereferencing a known-NULL pointer as
 * unreachable). Reading the address through a volatile global defeats that:
 * the value is 0 at runtime but opaque to the optimizer, so the write
 * survives and faults at address 0.
 */
static volatile intptr_t g_fault_addr = 0;

__declspec(dllexport) void trigger_crash(void)
{
    /* Write 0xDEAD through the runtime-NULL pointer → access violation.
     * Done directly in trigger_crash (no worker thread) so the fault shows up
     * at crashlib!trigger_crash in WinDbg, with no extra thread to reason about. */
    volatile int *p = (volatile int *)(intptr_t)g_fault_addr;
    *p = 0xDEAD;
}

BOOL APIENTRY DllMain(HMODULE hMod, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hMod);
    }
    return TRUE;
}
