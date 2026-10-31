using System.Runtime.InteropServices;

namespace DotAbyss.Local;

internal sealed class NativeHookPolicy : IDisposable
{
    private nint library;
    private SetFlag? requireNear;
    private bool changed;

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate void SetFlag(byte enabled);

    internal static void LogMappings()
    {
        try
        {
            if (!File.Exists("/proc/self/maps")) return;
            foreach (var line in File.ReadLines("/proc/self/maps"))
                if (line.Contains("libil2cpp.so", StringComparison.Ordinal) || line.Contains("libmain.so", StringComparison.Ordinal))
                    LocalMod.Log($"Native mapping: {line}");
        }
        catch (Exception exception) { LocalMod.Log($"Native mapping observation unavailable ({exception.GetType().Name})."); }
    }

    internal void AllowFarFallback()
    {
        if (!NativeLibrary.TryLoad("libmain.so", out library))
            throw new DllNotFoundException("The packaged libmain.so could not be opened.");
        if (!NativeLibrary.TryGetExport(library, "dobby_set_near_trampoline", out var setNear)
            || !NativeLibrary.TryGetExport(library, "dobby_set_near_trampoline_required", out var setRequired))
            throw new EntryPointNotFoundException("The packaged Dobby policy exports are unavailable.");
        requireNear = Marshal.GetDelegateForFunctionPointer<SetFlag>(setRequired);
        var preferNear = Marshal.GetDelegateForFunctionPointer<SetFlag>(setNear);
        // The bootstrap's first NativeHookAttach call overwrites this process-wide setting.
        preferNear(1);
        changed = true;
        LocalMod.Log("Experimental far-trampoline fallback enabled; this requires the bootstrap's first NativeHookAttach to have already completed.");
    }

    public void Dispose()
    {
        try
        {
            if (changed)
            {
                requireNear!(1);
                changed = false;
                LocalMod.Log("Restored the packaged loader's required-near trampoline policy.");
            }
        }
        finally
        {
            if (library != 0)
            {
                NativeLibrary.Free(library);
                library = 0;
            }
        }
    }
}
