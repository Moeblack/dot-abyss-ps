using Il2CppAbsf;
using Il2CppProject.Api;

namespace DotAbyss.Local;

internal static class NativeDiagnostics
{
    private static readonly HashSet<string> observed = [];

    internal static void ReadBootstrapConfiguration()
    {
        Read("RootLogin.ApiName", () => Route(new RootLoginRequestEntity().ApiName));
        Read("RuntimeConfig.AppVersionCode", () => Version(RuntimeConfig.GetAppVersionCode()));
        Read("UnityEngine.Application.version", () => Version(UnityEngine.Application.version));
        Read("RuntimeConfig.BundleVersion", () => Version(RuntimeConfig.GetBundleVersion()));
        Read("RuntimeConfig.ApiUrl", () => Route(RuntimeConfig.GetApiUrl()));
        Read("RuntimeConfig.MasterUrl", () => Route(RuntimeConfig.GetMasterUrl()));
        Read("RuntimeConfig.AssetBundleUrl", () => Route(RuntimeConfig.GetAssetBundleUrl()));
    }

    private static void Read(string label, Func<string> reader)
    {
        if (observed.Contains(label)) return;
        try
        {
            LocalMod.Log($"Native bootstrap observation {label}: {reader()}");
            observed.Add(label);
        }
        catch (Exception exception) { LocalMod.Log($"Native bootstrap observation {label}: unavailable ({exception.GetType().Name})"); }
    }

    private static string Route(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) throw new InvalidDataException("No route value is available.");
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) || uri.Scheme is not ("http" or "https"))
        {
            var relative = new Uri(new Uri("http://observed-route.invalid/"), value);
            return ProtocolLog.SafeUrl(new UriBuilder(relative) { Query = "", Fragment = "" }.Uri.AbsoluteUri)
                .Replace("http://observed-route.invalid", "", StringComparison.Ordinal);
        }
        return ProtocolLog.SafeUrl(new UriBuilder(uri) { Query = "", Fragment = "", UserName = "", Password = "" }.Uri.AbsoluteUri);
    }

    private static string Version(string? value)
    {
        if (string.IsNullOrEmpty(value)) throw new InvalidDataException("No version value is available.");
        return value.Length <= 128 && value.All(character => char.IsAsciiLetterOrDigit(character) || character is '.' or '-' or '_' or '+')
            ? value : ProtocolLog.StringShape(value);
    }
}
