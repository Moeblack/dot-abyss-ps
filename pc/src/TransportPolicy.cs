using System.IO.Compression;
using System.Text.Json;

namespace DotAbyss.Local.Pc;

internal static class TransportPolicy
{
    internal const int MaxBodyBytes = 2 * 1024 * 1024;
    internal const string ResourceRoot = "/resources/windows/r18/";
    internal const string CatalogPrefix = "resources/windows/r18/aas/0.1.0/aa";
    internal const string CatalogRoot = "/" + CatalogPrefix;
    internal const string CatalogHashPath = CatalogRoot + "/catalog_1.hash";

    internal static string ApiBase(Uri origin) => origin.AbsoluteUri;

    internal static object ProxyEnvironmentStatus(string? value)
    {
        var hosts = (value ?? "").Split(',').Select(host => host.Trim()).ToHashSet(StringComparer.OrdinalIgnoreCase);
        return new
        {
            present = !string.IsNullOrEmpty(value),
            length = value?.Length ?? 0,
            ipv4_loopback = hosts.Contains("127.0.0.1"),
            localhost = hosts.Contains("localhost"),
            ipv6_loopback = hosts.Contains("::1"),
            all_hosts = hosts.Contains("*")
        };
    }

    internal static bool SameOrigin(Uri candidate, Uri origin) => candidate.Scheme == origin.Scheme
        && candidate.Host == origin.Host && candidate.Port == origin.Port && candidate.UserInfo.Length == 0;

    internal static bool IsApiPath(string path) => path is "/maintenance" or "/login" or "/terms/fetch" or "/version" || path.StartsWith("/api/", StringComparison.Ordinal);

    internal static bool IsResourcePath(string path) => path.StartsWith(ResourceRoot, StringComparison.Ordinal)
        || path.StartsWith("/data/", StringComparison.Ordinal) || path.StartsWith("/master/", StringComparison.Ordinal);

    internal static bool AllowsHttp(Uri address, Uri origin) => SameOrigin(address, origin)
        && (IsApiPath(address.AbsolutePath) || IsResourcePath(address.AbsolutePath));

    internal static bool TryLocalResourceUrl(string? url, Uri origin, out string local)
    {
        local = "";
        if (!Uri.TryCreate(url, UriKind.Absolute, out var address) || !SameOrigin(address, origin)
            || !IsResourcePath(address.AbsolutePath) || address.Query.Length != 0 || address.Fragment.Length != 0)
            return false;
        local = url!;
        return true;
    }

    internal static string RejectedRequestDiagnostic(string? method, Uri address, Uri origin)
    {
        var sameOrigin = SameOrigin(address, origin);
        return JsonSerializer.Serialize(new
        {
            method = method is "GET" or "POST" or "PUT" or "PATCH" or "DELETE" or "HEAD" or "OPTIONS" ? method : "<redacted>",
            scheme = address.Scheme is "http" or "https" ? address.Scheme : "<other>",
            is_loopback = address.IsLoopback,
            same_origin = sameOrigin,
            port = address.Port,
            path = RequestPathDiagnostic(address.AbsolutePath),
            reason = sameOrigin ? "unsupported_local_path" : "different_origin"
        });
    }

    private static string RequestPathDiagnostic(string path)
    {
        if (path.Length > 256) return "<redacted>";
        if (path is "/" or "/maintenance" or "/login" or "/version" or "/uuid" or "/uuid/verified"
            or "/terms/fetch" or "/terms/get-view" or "/api/terms/agree" or "/api/terms/fetch-agreements"
            or "/dmm/client/acquisition" or "/dmm/client/regist" or "/api/user" or "/api/home" or "/api/mypage/login"
            or CatalogRoot or CatalogHashPath or CatalogRoot + "/catalog_1.bin") return path;
        foreach (var prefix in new[] { "/terms/get-view/", "/terms/", "/uuid/", "/dmm/client/", "/api/", "/resources/", "/data/", "/master/" })
            if (path.StartsWith(prefix, StringComparison.Ordinal)) return prefix + "<redacted>";
        return "<redacted>";
    }

    internal static string LocalizeOrigin(string url, Uri origin)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var address) || address.Scheme is not ("http" or "https")
            || address.UserInfo.Length != 0 || address.Query.Length != 0 || address.Fragment.Length != 0)
            throw new InvalidDataException("The native resource URL is not an unsigned HTTP origin and path.");
        var local = new UriBuilder(origin) { Path = address.AbsolutePath, Query = "", Fragment = "" }.Uri;
        if (!SameOrigin(local, origin)) throw new InvalidDataException("The resource rewrite changed its loopback origin.");
        return local.AbsoluteUri;
    }

    internal static bool IsCatalogRoot(Uri address, Uri origin) => SameOrigin(address, origin)
        && address.AbsolutePath == CatalogRoot && address.Query.Length == 0 && address.Fragment.Length == 0;

    internal static string ResourcePathDiagnostic(Uri address)
    {
        var path = address.AbsolutePath;
        return path.Length <= 256 && path.StartsWith(ResourceRoot, StringComparison.Ordinal)
            && path.All(character => character is >= 'A' and <= 'Z' or >= 'a' and <= 'z' or >= '0' and <= '9' or '/' or '_' or '.' or '-')
            ? path : "<outside supported resource namespace>";
    }

    internal static bool IsWithin(string path, string root)
    {
        var canonical = Path.GetFullPath(path);
        var directory = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        return canonical.Equals(directory, StringComparison.OrdinalIgnoreCase)
            || canonical.StartsWith(directory + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
    }

    internal static byte[] JsonBody(byte[] bytes)
    {
        if (bytes.Length > MaxBodyBytes) throw new InvalidDataException("Request body exceeds its limit.");
        if (bytes.Length >= 2 && bytes[0] == 0x1f && bytes[1] == 0x8b)
        {
            using var input = new MemoryStream(bytes, writable: false);
            using var gzip = new GZipStream(input, CompressionMode.Decompress);
            using var output = new MemoryStream();
            var block = new byte[8192];
            int read;
            while ((read = gzip.Read(block, 0, block.Length)) > 0)
            {
                if (output.Length + read > MaxBodyBytes) throw new InvalidDataException("Expanded request body exceeds its limit.");
                output.Write(block, 0, read);
            }
            bytes = output.ToArray();
        }
        if (bytes.Length == 0) return bytes;
        using var document = JsonDocument.Parse(bytes, new JsonDocumentOptions { MaxDepth = 64 });
        if (document.RootElement.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("Native API request must contain a JSON object.");
        return bytes;
    }
}
