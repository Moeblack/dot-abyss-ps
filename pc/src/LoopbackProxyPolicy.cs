using System.Buffers;
using System.Runtime.InteropServices;
using System.Text;

namespace DotAbyss.Local.Pc;

[UnmanagedFunctionPointer(CallingConvention.Cdecl)]
internal delegate int CurlSetOption(IntPtr handle, int option, IntPtr value);

internal sealed record ProxyOptionChange(int OriginalBytes, int AddedHosts);

internal static class LoopbackProxyPolicy
{
    internal const int NoProxyOption = 10177;
    internal const int EntryRva = 0x1aa3750;
    internal const int MaxCStringBytes = 16384;
    internal const string UnityPlayerHash = "d935627d3ac843293f1c51ee9d85538191f0cb98def4271c1a29302b52a021d4";
    internal const string EntryHex = "895424104c894424184c894c24204883ec284885c975088d412b4883c428c34c";
    private static readonly UTF8Encoding Utf8 = new(false, true);
    private static readonly string[] LoopbackHosts = { "127.0.0.1", "localhost", "::1" };

    internal static void VerifyIdentity(string digest, ReadOnlySpan<byte> entry)
    {
        if (!digest.Equals(UnityPlayerHash, StringComparison.OrdinalIgnoreCase) || !entry.SequenceEqual(Convert.FromHexString(EntryHex)))
            throw new InvalidDataException("UnityPlayer does not match the verified local proxy adapter.");
    }

    internal static int Forward(CurlSetOption original, IntPtr handle, int option, IntPtr value, out ProxyOptionChange? change)
    {
        change = null;
        if (option != NoProxyOption || value == IntPtr.Zero) return original(handle, option, value);
        var existing = ReadCString(value);
        var replacement = AppendLoopback(existing, out var added);
        change = new ProxyOptionChange(Utf8.GetByteCount(existing), added);
        if (added == 0)
        {
            var unchanged = original(handle, option, value);
            if (unchanged != 0) throw new InvalidOperationException("Native loopback proxy configuration failed.");
            return unchanged;
        }
        var bytes = Utf8.GetBytes(replacement);
        var temporary = Marshal.AllocHGlobal(bytes.Length + 1);
        try
        {
            Marshal.Copy(bytes, 0, temporary, bytes.Length);
            Marshal.WriteByte(temporary, bytes.Length, 0);
            var result = original(handle, option, temporary);
            if (result != 0) throw new InvalidOperationException("Native loopback proxy configuration failed.");
            return result;
        }
        finally { Marshal.FreeHGlobal(temporary); }
    }

    internal static string AppendLoopback(string existing, out int added)
    {
        if (existing.IndexOf('\0') >= 0 || Utf8.GetByteCount(existing) >= MaxCStringBytes)
            throw new InvalidDataException("The native proxy bypass value exceeds its allowed boundary.");
        var configured = existing.Split(',').Select(item => item.Trim()).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var missing = LoopbackHosts.Where(host => !configured.Contains(host)).ToArray();
        added = missing.Length;
        if (added == 0) return existing;
        return existing + (existing.Length == 0 || existing.EndsWith(',') ? "" : ",") + string.Join(',', missing);
    }

    internal static string ReadCString(IntPtr value)
    {
        if (value == IntPtr.Zero) throw new ArgumentNullException(nameof(value));
        var bytes = ArrayPool<byte>.Shared.Rent(MaxCStringBytes);
        try
        {
            for (var index = 0; index < MaxCStringBytes; index++)
            {
                var next = Marshal.ReadByte(value, index);
                if (next == 0) return Utf8.GetString(bytes, 0, index);
                bytes[index] = next;
            }
            throw new InvalidDataException("The native proxy bypass string has no bounded terminator.");
        }
        finally { ArrayPool<byte>.Shared.Return(bytes, clearArray: true); }
    }
}
