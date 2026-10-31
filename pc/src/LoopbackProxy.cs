using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using BepInEx.Unity.IL2CPP.Hook;

namespace DotAbyss.Local.Pc;

internal sealed class LoopbackProxy : IDisposable
{
    private readonly CurlSetOption replacement;
    private readonly INativeDetour detour;
    private readonly CurlSetOption original;
    private int observedChanges;

    private LoopbackProxy(IntPtr entry)
    {
        replacement = SetOption;
        detour = INativeDetour.Create(entry, replacement);
        original = detour.GenerateTrampoline<CurlSetOption>();
        detour.Apply();
    }

    internal static LoopbackProxy Install(string clientRoot)
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows) || RuntimeInformation.ProcessArchitecture != Architecture.X64)
            throw new PlatformNotSupportedException("The local proxy adapter requires the verified Windows x64 player.");
        using var process = Process.GetCurrentProcess();
        var module = process.Modules.Cast<ProcessModule>().Single(item => string.Equals(item.ModuleName, "UnityPlayer.dll", StringComparison.OrdinalIgnoreCase));
        var modulePath = module.FileName ?? throw new InvalidDataException("UnityPlayer has no verified module path.");
        var expectedPath = Path.GetFullPath(Path.Combine(clientRoot, "UnityPlayer.dll"));
        if (!Path.GetFullPath(modulePath).Equals(expectedPath, StringComparison.OrdinalIgnoreCase)
            || module.ModuleMemorySize < LoopbackProxyPolicy.EntryRva + LoopbackProxyPolicy.EntryHex.Length / 2)
            throw new InvalidDataException("UnityPlayer is not loaded from the prepared client.");
        using var stream = File.OpenRead(expectedPath);
        using var hash = SHA256.Create();
        var digest = Convert.ToHexString(hash.ComputeHash(stream)).ToLowerInvariant();
        var address = IntPtr.Add(module.BaseAddress, LoopbackProxyPolicy.EntryRva);
        var entry = new byte[LoopbackProxyPolicy.EntryHex.Length / 2];
        Marshal.Copy(address, entry, 0, entry.Length);
        LoopbackProxyPolicy.VerifyIdentity(digest, entry);
        var adapter = new LoopbackProxy(address);
        Plugin.Info("Process-local loopback proxy adapter installed for the verified UnityPlayer.");
        return adapter;
    }

    private int SetOption(IntPtr handle, int option, IntPtr value)
    {
        try
        {
            var result = LoopbackProxyPolicy.Forward(original, handle, option, value, out var change);
            if (change is not null && Interlocked.Increment(ref observedChanges) <= 3)
                Plugin.Info($"Process-local loopback bypass configured: source_bytes={change.OriginalBytes}, appended_hosts={change.AddedHosts}, native_result={result}");
            return result;
        }
        catch (Exception error)
        {
            Plugin.Stop(error);
            return 43;
        }
    }

    public void Dispose() => detour.Dispose();
}
