#include <windows.h>

static DWORD WINAPI crash_thread(LPVOID unused)
{
    (void)unused;
    volatile int *p = NULL;
    *p = 0xDEAD;
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