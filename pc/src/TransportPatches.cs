using Absf;
using Absf.Api;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using Il2CppInterop.Runtime;
using System.Security.Cryptography;
using UnityEngine.Networking;

namespace DotAbyss.Local.Pc;

internal static class TransportPatches
{
    private static int getterCalls;
    private static int requestCount;
    private static int rejectedRequestCount;
    private static long activityVersion;
    private static long lastActivityAt = Environment.TickCount64;
    internal static long ActivityVersion => Interlocked.Read(ref activityVersion);
    internal static long LastActivityAt => Interlocked.Read(ref lastActivityAt);
    private static readonly OperationIdentities identities = new();

    private sealed record RequestOwner(Il2CppSystem.Object Request, IApiRequestTask Task);

    private static string OperationIdentity(string method, string path, byte[] body)
    {
        var manager = Engine.Get<ApiManager>() ?? throw new InvalidOperationException("Native API manager is unavailable.");
        var task = manager._currentTask ?? throw new InvalidOperationException("Native API request task is unavailable.");
        var field = IL2CPP.il2cpp_class_get_field_from_name(task.ObjectClass, "_request");
        if (field == IntPtr.Zero) throw new MissingFieldException("Native API task has no request field.");
        var pointer = IL2CPP.il2cpp_field_get_value_object(field, task.Pointer);
        if (pointer == IntPtr.Zero) throw new InvalidOperationException("Native API request is unavailable.");
        var owner = new RequestOwner(new Il2CppSystem.Object(pointer), task);
        var fingerprint = method + "\n" + path + "\n" + Convert.ToHexString(SHA256.HashData(body));
        return identities.Get(pointer.ToInt64(), task.Pointer.ToInt64(), owner, fingerprint,
            () => owner.Task.IsFinish && owner.Task.GetEntityData()?.GetContentsObject() is not null);
    }

    internal static bool ApiUrl(ref string __result)
    {
        Interlocked.Increment(ref getterCalls);
        __result = TransportPolicy.ApiBase(Plugin.Origin);
        return false;
    }

    internal static bool VerifyApiHook()
    {
        var before = getterCalls;
        return RuntimeConfig.GetApiUrl() == TransportPolicy.ApiBase(Plugin.Origin) && getterCalls > before;
    }

    internal static void RefreshApi(ref string __0) => __0 = TransportPolicy.ApiBase(Plugin.Origin);

    internal static bool EncryptBody(Il2CppStructArray<byte> __0, ref Il2CppStructArray<byte> __result)
    {
        __result = __0;
        return false;
    }

    internal static bool DecryptSession(string __0, ref string __result)
    {
        if (__0 != Plugin.Session.Get().Token)
        {
            Plugin.Stop(new InvalidDataException("The response session does not belong to the local bootstrap."));
            return false;
        }
        __result = __0;
        return false;
    }

    internal static bool Send(UnityWebRequest __instance, ref UnityWebRequestAsyncOperation __result)
    {
        try
        {
            if (!Uri.TryCreate(__instance.url, UriKind.Absolute, out var address))
                throw new InvalidDataException("The native request has no absolute URL.");
            if (address.IsFile)
            {
                if (!TransportPolicy.IsWithin(address.LocalPath, Plugin.ClientRoot)
                    && !TransportPolicy.IsWithin(address.LocalPath, Plugin.Options.ExpectedPersistentPath))
                    throw new InvalidDataException("The native file request leaves the prepared client.");
                return true;
            }
            if (!TransportPolicy.AllowsHttp(address, Plugin.Origin))
            {
                __result = null!;
                if (Interlocked.Increment(ref rejectedRequestCount) <= 100)
                    Plugin.Info("Blocked native request: " + TransportPolicy.RejectedRequestDiagnostic(__instance.method, address, Plugin.Origin));
                return false;
            }
            __instance.redirectLimit = 0;
            if (!TransportPolicy.IsApiPath(address.AbsolutePath)) return true;
            RecordActivity();
            var normalizedBody = Array.Empty<byte>();
            if (__instance.uploadHandler?.data is { } nativeBytes)
            {
                var raw = nativeBytes.ToArray();
                var normalized = TransportPolicy.JsonBody(raw);
                normalizedBody = normalized;
                if (!ReferenceEquals(raw, normalized))
                {
                    var previous = __instance.uploadHandler;
                    __instance.uploadHandler = new UploadHandlerRaw(normalized);
                    previous.Dispose();
                }
            }
            var session = Plugin.Session.Get();
            __instance.SetRequestHeader("x-olg-session", session.Token);
            __instance.SetRequestHeader("api-token", session.ApiToken);
            __instance.SetRequestHeader("Content-Type", "application/json");
            __instance.SetRequestHeader("Content-Encoding", "identity");
            __instance.SetRequestHeader("Accept", "application/json");
            __instance.SetRequestHeader("x-dotabyss-local", "pc");
            if (__instance.method == "POST" && address.AbsolutePath.StartsWith("/api/", StringComparison.Ordinal))
                __instance.SetRequestHeader("x-request-id", OperationIdentity(__instance.method, address.AbsolutePath, normalizedBody));
            if (Interlocked.Increment(ref requestCount) <= 100)
                Plugin.Info($"Native local request: {__instance.method} {address.AbsolutePath}");
            return true;
        }
        catch (Exception error) { Plugin.Stop(error); }
        __result = null!;
        return false;
    }

    internal static void Response(ApiResponseData __0)
    {
        if (__0 is null) return;
        RecordActivity();
        Plugin.Info($"Native response: HTTP={__0.ResponseCode}, bytes={__0.ContentSize}, result={__0.ResultType}");
    }

    private static void RecordActivity()
    {
        Interlocked.Increment(ref activityVersion);
        Interlocked.Exchange(ref lastActivityAt, Environment.TickCount64);
    }
}
