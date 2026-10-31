using Absf;
using Absf.Api;
using Project.Api;
using Project.Master;
using Project.User;

namespace DotAbyss.Local.Pc;

internal static class AdminSyncNativeState
{
    private static readonly AdminSyncEvidence evidence = new();
    private static readonly object gate = new();
    private static bool reportedFailure;

    internal static void Install(Plugin.AddHook add)
    {
        add(typeof(UserData), "Apply", new[] { typeof(RecordEntity) }, typeof(AdminSyncNativeState), nameof(Applied), true);
        add(typeof(ApiManager), "OnSuccess", new[] { typeof(CommonEntity), typeof(IApiResponseEntity) },
            typeof(AdminSyncNativeState), nameof(Succeeded), true);
    }

    internal static void Applied(RecordEntity __0)
    {
        try
        {
            if (__0 is null) return;
            lock (gate) evidence.Applied(__0.Pointer.ToInt64(), TransportPatches.ActivityVersion, __0);
        }
        catch (Exception error) { ReportFailure(error); }
    }

    internal static void Succeeded(IApiResponseEntity __1)
    {
        try
        {
            var contents = __1?.GetContentsObject()?.TryCast<UserResponseEntity>();
            var versions = __1?.GetVersions()?.resource;
            if (contents?.records is not { } records || versions is null || versions.Length != 1) return;
            lock (gate) evidence.UserResponse(records.Pointer.ToInt64(), TransportPatches.ActivityVersion, versions[0], records);
        }
        catch (Exception error) { ReportFailure(error); }
    }

    internal static bool HasLoaded(string version)
    {
        var loader = Engine.IsBoot ? Engine.Get<MasterDataStore>()?._masterLoader : null;
        if (loader is null) return false;
        lock (gate) return evidence.HasLoaded(version, loader.Version, loader._isDeserialized);
    }

    private static void ReportFailure(Exception error)
    {
        if (reportedFailure) return;
        reportedFailure = true;
        Plugin.Info("Administrator login evidence unavailable (" + error.GetType().Name + ").");
    }
}
