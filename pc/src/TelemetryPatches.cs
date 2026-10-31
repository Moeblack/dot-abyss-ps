using Il2CppInterop.Runtime.InteropTypes.Arrays;
using Unity.Cloud.UserReporting.Plugin;
using Unity.Cloud.UserReporting.Plugin.Version2018_3;

namespace DotAbyss.Local.Pc;

internal static class TelemetryPatches
{
    private static int blockedPosts;

    internal static void Install(Plugin.AddHook add)
    {
        foreach (var type in new[] { typeof(UnityUserReportingPlatform), typeof(AsyncUnityUserReportingPlatform) })
        {
            add(type, "Post", new[] { typeof(string), typeof(string), typeof(Il2CppStructArray<byte>),
                typeof(Il2CppSystem.Action<float, float>), typeof(Il2CppSystem.Action<bool, Il2CppStructArray<byte>>) }, typeof(TelemetryPatches), nameof(BlockPost));
            add(type, "SendAnalyticsEvent", new[] { typeof(string), typeof(Il2CppSystem.Collections.Generic.Dictionary<string, Il2CppSystem.Object>) }, typeof(TelemetryPatches), nameof(BlockAnalytics));
        }
    }

    internal static bool BlockPost(Il2CppSystem.Action<bool, Il2CppStructArray<byte>>? __4)
    {
        if (Interlocked.Increment(ref blockedPosts) == 1) Plugin.Info("User report upload disabled for the local client.");
        try { __4?.Invoke(false, new Il2CppStructArray<byte>(0)); }
        catch (Exception error) { Plugin.Stop(error); }
        return false;
    }

    internal static bool BlockAnalytics() => false;
}
