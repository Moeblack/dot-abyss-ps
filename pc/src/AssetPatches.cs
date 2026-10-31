using Absf;
using Absf.Api;
using Absf.Asset.AddressableAssets;

namespace DotAbyss.Local.Pc;

internal static class AssetPatches
{
    private static int localLinkReported;

    internal static void Install(Plugin.AddHook add)
    {
        add(typeof(RuntimeConfig), "GetAssetBundleUrl", Type.EmptyTypes, typeof(AssetPatches), nameof(ResourceUrl), true);
        add(typeof(RuntimeConfig), "GetMasterUrl", Type.EmptyTypes, typeof(AssetPatches), nameof(ResourceUrl), true);
        add(typeof(AddressablesProfileDefine), "get_ServerUrl", Type.EmptyTypes, typeof(AssetPatches), nameof(ResourceUrl), true);
        add(typeof(AddressablesProfileDefine), "get_RemoteLoadPath", Type.EmptyTypes, typeof(AssetPatches), nameof(RemoteLoadPath), true);
        add(typeof(SecureLinkUtil), "CreateSecureUrl", new[] { typeof(Il2CppSystem.Uri), typeof(string), typeof(int) }, typeof(AssetPatches), nameof(SecureResourceUrl));
    }

    internal static bool SecureResourceUrl(Il2CppSystem.Uri? __0, ref string __result)
    {
        try
        {
            if (__0 is null || !TransportPolicy.TryLocalResourceUrl(__0.AbsoluteUri, Plugin.Origin, out var local)) return true;
            __result = local;
            if (Interlocked.Exchange(ref localLinkReported, 1) == 0)
                Plugin.Info("Preserved the local resource origin before native link signing.");
        }
        catch (Exception error) { Plugin.Stop(error); }
        return false;
    }

    internal static void ResourceUrl(ref string __result)
    {
        try { __result = TransportPolicy.LocalizeOrigin(__result, Plugin.Origin); }
        catch (Exception error) { Plugin.Stop(error); }
    }

    internal static void RemoteLoadPath(ref string __result)
    {
        try
        {
            var local = TransportPolicy.LocalizeOrigin(__result, Plugin.Origin);
            var address = new Uri(local);
            Plugin.Info("Native catalog root path: " + TransportPolicy.ResourcePathDiagnostic(address));
            if (!TransportPolicy.IsCatalogRoot(address, Plugin.Origin))
                throw new InvalidDataException("The native asset namespace is not the prepared PC version.");
            __result = local;
        }
        catch (Exception error) { Plugin.Stop(error); }
    }
}
