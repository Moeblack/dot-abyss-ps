using Il2CppAbsf.Api;
using Il2CppCysharp.Threading.Tasks;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using Il2CppProject.Api;
using UnityEngine.Networking;
using LoginTuple = Il2CppSystem.ValueTuple<long, string>;

namespace DotAbyss.Local;

internal static class ObservationPatches
{
    internal static void SendRequest(UnityWebRequest __instance) => Observe(() =>
    {
        LocalMod.Log($"Observed request {__instance.method} {ProtocolLog.SafeUrl(__instance.url)}");
        if (LocalMod.Options.LogProtocolShapes && __instance.uploadHandler?.data is { } data)
        {
            if (data.Length <= 65536)
                LocalMod.Log($"Observed request body {ProtocolLog.Shape(System.Text.Encoding.UTF8.GetString(data.ToArray()))}");
            else LocalMod.Log($"Observed request body bytes={data.Length}, omitted");
        }
    });

    internal static void Response(ApiResponseData __0) => Observe(() =>
    {
        if (__0 is null) return;
        LocalMod.Log($"Observed response HTTP={__0.ResponseCode}, bytes={__0.ContentSize}, result={__0.ResultType}");
        if (LocalMod.Options.LogProtocolShapes) LocalMod.Log($"Observed response body {ProtocolLog.Shape(__0.JsonBody)}");
        LocalMod.Capture(__0.JsonBody);
    });

    internal static void RootLogin(RootLoginRequestEntity __instance) => Observe(() =>
    {
        var route = __instance.ApiName;
        var observed = Uri.TryCreate(route, UriKind.Absolute, out var absolute) && absolute.Scheme is "http" or "https"
            ? ProtocolLog.SafeUrl(route)
            : ProtocolLog.SafeUrl(new Uri(new Uri("http://observed-route.invalid/"), route).AbsoluteUri);
        LocalMod.Log($"Observed RootLogin ApiName: {observed}");
    });

    internal static void LoginEntered(Il2CppReferenceArray<TermsAgreeRequestEntity.TermsAgree>? __0) => Observe(() =>
        LocalMod.Log($"Observed LoginDmmAndroidAsync enter; terms_count={__0?.Length ?? 0}, token_values=omitted"));

    internal static void LoginReturned(UniTask<LoginTuple> __result) => Observe(() =>
    {
        if (__result is null)
        {
            LocalMod.Log("Observed LoginDmmAndroidAsync return: null wrapper");
            return;
        }
        var source = __result.source;
        var status = __result.Status;
        LocalMod.Log($"Observed LoginDmmAndroidAsync return: UniTask<ValueTuple<Int64,String>>, status={status}, has_source={source is not null}");
        if (source is null && status == UniTaskStatus.Succeeded)
            LocalMod.Log($"Observed synchronous login tuple: item1=<redacted>, item2={ProtocolLog.StringShape(__result.result.Item2)}");
        else LocalMod.Log("Asynchronous login result was not consumed by the observer.");
    });

    internal static void DescribeStateLayout()
    {
        var type = typeof(Il2CppProject.Login._LoginDmmAndroidAsync_d__11);
        foreach (var name in new[] { "_userData_5__4", "_serverTime_5__5", "_apiManager_5__6" })
        {
            var property = type.GetProperty(name);
            if (property is not null) LocalMod.Log($"Observed state layout: {name} : {property.PropertyType.FullName}; value=omitted");
        }
    }

    private static void Observe(Action action)
    {
        if (!LocalMod.Active || !LocalMod.Options.ObserveOnly) return;
        try { action(); }
        catch (Exception exception) { LocalMod.Log($"Observation unavailable ({exception.GetType().Name}); original operation continues."); }
    }
}
