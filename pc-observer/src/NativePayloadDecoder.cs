using Absf.Api;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using MessagePack;
using System.Text;

namespace DotAbyss.Observer.Pc;

internal static class NativePayloadDecoder
{
    internal const int MaxBodyBytes = 8 * 1024 * 1024;
    internal const int MaxBodyChars = 4 * 1024 * 1024;

    internal static string? Decode(HttpSuccess success)
    {
        if (success.responseFormat == DecryptedResponseFormat.Json)
            return BoundedJson(success.responseAsString);
        if (success.responseFormat != DecryptedResponseFormat.MessagePack) return null;
        var bytes = success.responseAsBytes;
        if (bytes is null || bytes.Length == 0 || bytes.Length > MaxBodyBytes) return null;
        return DecodeMessagePack(bytes);
    }

    internal static string? DecodeMessagePack(Il2CppStructArray<byte> bytes)
    {
        if (bytes.Length == 0 || bytes.Length > MaxBodyBytes) return null;
        var memory = new Il2CppSystem.ReadOnlyMemory<byte>(bytes);
        var json = MessagePackSerializer.ConvertToJson(memory, null!, Il2CppSystem.Threading.CancellationToken.None);
        return BoundedJson(json);
    }

    internal static string? BoundedJson(string? json) => json is not null && json.Length <= MaxBodyChars
        && Encoding.UTF8.GetByteCount(json) <= MaxBodyBytes ? json : null;
}
