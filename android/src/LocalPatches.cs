using Il2CppAbsf;
using Il2CppAbsf.Api;
using Il2CppCysharp.Threading.Tasks;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using Il2CppProject.Api;
using UnityEngine.Networking;
using GameCancellationToken = Il2CppSystem.Threading.CancellationToken;
using LoginTuple = Il2CppSystem.ValueTuple<long, string>;

namespace DotAbyss.Local;

internal static class LocalPatches
{
    private static int apiUrlInvocations;
    private static int loginPrefixInvocations;

    internal static int LoginPrefixInvocations => Volatile.Read(ref loginPrefixInvocations);

    internal static bool ApiUrl(ref string __result)
    {
        if (!LocalMod.Active) return true;
        var invocation = Interlocked.Increment(ref apiUrlInvocations);
        if (invocation <= 4) LocalMod.Log($"Native prefix entered: RuntimeConfig.GetApiUrl, invocation={invocation}");
        __result = LocalMod.Origin.AbsoluteUri;
        return false;
    }

    internal static bool VerifyApiUrlHook()
    {
        var expected = LocalMod.Origin.AbsoluteUri;
        var before = Volatile.Read(ref apiUrlInvocations);
        try
        {
            var actual = RuntimeConfig.GetApiUrl();
            var after = Volatile.Read(ref apiUrlInvocations);
            var active = after > before && string.Equals(actual, expected, StringComparison.Ordinal);
            LocalMod.Log($"Native hook probe: actual={ProtocolLog.SafeUrl(actual ?? "")}, expected={expected}, prefix_before={before}, prefix_after={after}, active={active}");
            if (!active) LocalMod.Log("native_hook_inactive: installing a detour did not prove that the original native call used it.");
            return active;
        }
        catch (Exception exception)
        {
            LocalMod.Log($"native_hook_inactive: API getter probe unavailable ({exception.GetType().Name}), prefix_before={before}, prefix_after={Volatile.Read(ref apiUrlInvocations)}");
            return false;
        }
    }

    internal static void RefreshApi(ref string __0)
    {
        if (LocalMod.Active) __0 = LocalMod.Origin.AbsoluteUri;
    }

    internal static bool PassBytes(Il2CppStructArray<byte> __0, ref Il2CppStructArray<byte> __result)
    {
        if (!LocalMod.Active) return true;
        __result = __0;
        return false;
    }

    internal static bool PassString(string __0, ref string __result)
    {
        if (!LocalMod.Active) return true;
        __result = __0;
        return false;
    }

    internal static void SendRequest(UnityWebRequest __instance)
    {
        if (!LocalMod.Active || !Uri.TryCreate(__instance.url, UriKind.Absolute, out var original)
            || original.Scheme is not ("http" or "https")) return;
        var target = new UriBuilder(LocalMod.Origin)
        {
            Path = original.AbsolutePath,
            Query = original.Query.TrimStart('?'),
            Fragment = ""
        }.Uri;
        __instance.url = target.AbsoluteUri;
        __instance.redirectLimit = 0;
        var session = LocalMod.RequireSession();
        __instance.SetRequestHeader("x-olg-session", session.Token);
        __instance.SetRequestHeader("api-token", session.ApiToken);
        __instance.SetRequestHeader("x-dotabyss-local", "1");
        LocalMod.Log($"Request {__instance.method} {ProtocolLog.SafeUrl(target.AbsoluteUri)}");
        if (LocalMod.Options.LogProtocolShapes && __instance.uploadHandler?.data is { } data)
        {
            var length = data.Length;
            if (length <= 65536)
                LocalMod.Log($"Request body {ProtocolLog.Shape(System.Text.Encoding.UTF8.GetString(data.ToArray()))}");
            else LocalMod.Log($"Request body bytes={length}, omitted");
        }
    }

    internal static void Response(ApiResponseData __0)
    {
        if (!LocalMod.Active || __0 is null) return;
        LocalMod.Log($"Response HTTP={__0.ResponseCode}, bytes={__0.ContentSize}, result={__0.ResultType}");
        if (LocalMod.Options.LogProtocolShapes) LocalMod.Log($"Response body {ProtocolLog.Shape(__0.JsonBody)}");
        LocalMod.Capture(__0.JsonBody);
    }

    internal static bool InitializePlatform(GameCancellationToken __0, Il2CppSystem.Action? __1, ref UniTask __result)
        => InitializePlatformCore(__0, __1, ref __result, "platform_sdk_initialized");

    internal static bool InitializePlatformWithMarketingData(GameCancellationToken __1, Il2CppSystem.Action? __2, ref UniTask __result)
        => InitializePlatformCore(__1, __2, ref __result, "unity_iap_initialized");

    private static bool InitializePlatformCore(GameCancellationToken cancellation, Il2CppSystem.Action? onSuccess,
        ref UniTask result, string eventName)
    {
        if (!LocalMod.Active) return true;
        try
        {
            LocalMod.Log($"Native platform prefix entered: {eventName}");
            cancellation.ThrowIfCancellationRequested();
            LocalMod.RequireSession();
            LocalMod.Report("local_session_ready", new { hooks_installed = true, native_login_verified = false });
            result = UniTask.CompletedTask;
            onSuccess?.Invoke();
            LocalMod.Report(eventName, new { mode = "local", game_login_completed = false });
            LocalMod.Log($"Platform initialization replaced with a local session: {eventName}");
            NativeLoginProbe.RunOnce(cancellation);
        }
        catch (Exception exception)
        {
            result = UniTask.FromException(new Il2CppSystem.InvalidOperationException(
                $"Local initialization failed ({exception.GetType().Name}); platform initialization was not attempted."));
        }
        return false;
    }

    internal static bool LoginAndroid(GameCancellationToken __1, ref UniTask<LoginTuple> __result)
    {
        if (!LocalMod.Active) return true;
        Interlocked.Increment(ref loginPrefixInvocations);
        try
        {
            LocalMod.Log("Native prefix entered: LoginDmmAndroidAsync local session adapter.");
            var login = LocalIdentity.Install(__1);
            NativeLoginProbe.RecordExpectedResult(login);
            __result = LoginTaskFactory.Create(login);
            if (NativeLoginProbe.IsRunning)
                LocalMod.Log("Standalone probe prepared its own login result; normal-flow completion events are suppressed.");
            else
            {
                LocalMod.Report("local_session_installed", new { native_uuid_cache = true, native_api_session = true, platform_integrity_cache_touched = false });
                LocalMod.Report("native_login_completed", new { scope = "LoginDmmAndroidAsync", home_ready = false });
                LocalMod.Log("Local LoginDmmAndroidAsync returned (user_id, server_time); the outer game initialization continues.");
            }
        }
        catch (Exception exception)
        {
            LocalMod.Log($"Local game login failed ({exception.GetType().Name}); official login was not attempted.");
            if (NativeLoginProbe.IsRunning) LocalMod.Log($"Local game login method frames: {ProtocolLog.ExceptionFrames(exception)}");
            __result = UniTask.FromException<LoginTuple>(new Il2CppSystem.InvalidOperationException(
                $"Local login adapter failed ({exception.GetType().Name}); official login was not attempted."));
        }
        return false;
    }

    internal static bool LocalUuid(GameCancellationToken __0, ref UniTask<string> __result)
    {
        if (!LocalMod.Active) return true;
        try
        {
            __0.ThrowIfCancellationRequested();
            __result = new UniTask<string>(LocalMod.RequireSession().Uuid);
        }
        catch (Exception exception)
        {
            __result = UniTask.FromException<string>(new Il2CppSystem.InvalidOperationException(
                $"Local UUID unavailable ({exception.GetType().Name}); platform UUID was not requested."));
        }
        return false;
    }

    internal static void ObserveRootLogin(RootLoginRequestEntity __instance)
    {
        if (!LocalMod.Active) return;
        var path = __instance.ApiName;
        LocalMod.Log($"RootLogin observed route: {ProtocolLog.SafeUrl(new Uri(LocalMod.Origin, path).AbsoluteUri)}");
    }
}
