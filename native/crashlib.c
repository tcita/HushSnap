#include <windows.h>

// Volatile globals so the compiler cannot prove at compile time that the
// pointer is NULL, and therefore cannot delete the dereference as UB. A plain
// `volatile int *p = NULL; *p = ...;` gets optimized away by MSVC /O2 (it
// treats dereferencing a known-NULL pointer as unreachable). Reading the
// address through a volatile global defeats that: the value is 0 at runtime
// but opaque to the optimizer.
static volatile intptr_t g_null_addr = 0;
static volatile int g_sink = 0;

static DWORD WINAPI crash_thread(LPVOID unused)
{
    (void)unused;
    // Read the (runtime-NULL) address through volatile, then write through it.
    volatile int *p = (volatile int *)(intptr_t)g_null_addr;
    *p = 0xDEAD;
    g_sink = 1;  // keep the function non-trivial
    return 0;
}

__declspec(dllexport) void trigger_crash(void)
{
    HANDLE h = CreateThread(NULL, 0, crash_thread, NULL, 0, NULL);
    if (h) {
        WaitForSingleObject(h, INFINITE);
        CloseHandle(h);
    }
}

BOOL APIENTRY DllMain(HMODULE hMod, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hMod);
    }
    return TRUE;
}
