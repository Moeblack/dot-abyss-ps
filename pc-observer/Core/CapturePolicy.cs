using System.Text.RegularExpressions;
using System.Reflection;
using System.Text.Json;

namespace DotAbyss.Observer.Pc;

public static class CapturePolicy
{
    private static readonly HashSet<string> KnownStaticSegments = ReadStaticSegments();
    private static readonly HashSet<string> ExcludedSegments = new(StringComparer.OrdinalIgnoreCase)
    {
        "auth", "account", "accounts", "authentication", "authorization", "oauth", "oauth2", "session", "sessions",
        "token", "tokens", "cookie", "cookies", "password", "credential", "credentials", "receipt", "receipts",
        "payment", "payments", "purchase", "purchases", "dmm", "uuid", "login", "logout", "verify", "verified",
        "charge-center", "create-token", "refresh-token", "access-token", "auth-token"
    };
    private static readonly Regex Identifier = new(@"\A(?:[0-9a-f]{8}-[0-9a-f-]{27,}|[a-z0-9_+=-]{24,})\z",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(20));
    private static readonly Regex Segment = new(@"\A[a-zA-Z0-9_.{}-]{1,96}\z",
        RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(20));

    public static string NormalizeMethod(string? method) => (method?.Length <= 8 ? method.ToUpperInvariant() : "") switch
    {
        "GET" => "GET", "POST" => "POST", "PUT" => "PUT", "PATCH" => "PATCH", "DELETE" => "DELETE",
        "HEAD" => "HEAD", "OPTIONS" => "OPTIONS", _ => "UNKNOWN"
    };

    public static bool TryNormalizeRoute(string? rawRoute, out string route)
    {
        route = "";
        if (!TryPath(rawRoute, out var path)) return false;
        var pieces = path.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (pieces.Length < 2 || pieces[0] != "api") return false;
        if (path != "/api/mypage/login" && pieces.Skip(1).Any(piece => ExcludedSegments.Contains(piece))) return false;
        route = "/" + string.Join('/', pieces.Select(SafeSegment));
        return true;
    }

    internal static string RouteTemplate(string route) => "/" + string.Join('/', route.Split('/', StringSplitOptions.RemoveEmptyEntries)
        .Select(piece => piece.All(IsDigit) ? "{id}" : piece));

    internal static string SafeResourceReference(string value)
    {
        if (!TryPath(value, out var path)) return "[redacted]";
        var pieces = path.Split('/', StringSplitOptions.RemoveEmptyEntries);
        return "/" + string.Join('/', pieces.Select(piece =>
            IsResourceFile(piece) ? piece : SafeSegment(piece)));
    }

    private static bool IsResourceFile(string value) => value.Length <= 200 && value.All(c => IsDigit(c) || c is >= 'A' and <= 'Z' or >= 'a' and <= 'z' or '_' or '-' or '.')
        && (value.EndsWith(".bundle", StringComparison.OrdinalIgnoreCase) || value.EndsWith(".bin", StringComparison.OrdinalIgnoreCase)
            || value.EndsWith(".hash", StringComparison.OrdinalIgnoreCase) || value.EndsWith(".dat", StringComparison.OrdinalIgnoreCase));

    private static bool TryPath(string? value, out string path)
    {
        path = "";
        if (string.IsNullOrWhiteSpace(value) || value.Length > 4096 || value.Any(char.IsControl) || value.Contains('\\')) return false;
        if (value.Contains("://", StringComparison.Ordinal))
        {
            if (!Uri.TryCreate(value, UriKind.Absolute, out var address) || address.Scheme is not ("http" or "https")) return false;
            path = address.AbsolutePath;
        }
        else
        {
            if (value.StartsWith("//", StringComparison.Ordinal)) return false;
            path = "/" + value.Split('?', '#')[0].TrimStart('/');
        }
        if (path.Length > 1024) return false;
        var pieces = path.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (pieces.Length > 32) return false;
        foreach (var piece in pieces)
        {
            var decoded = Uri.UnescapeDataString(piece);
            if (decoded is "." or ".." || decoded.Contains('/') || decoded.Contains('\\') || decoded.Any(char.IsControl)) return false;
        }
        path = "/" + string.Join('/', pieces.Select(Uri.UnescapeDataString));
        return true;
    }

    private static string SafeSegment(string value)
    {
        if (value.Length <= 19 && value.All(IsDigit)) return value;
        if (KnownStaticSegments.Contains(value)) return value;
        return Segment.IsMatch(value) && !Identifier.IsMatch(value) ? value : "{redacted}";
    }

    private static HashSet<string> ReadStaticSegments()
    {
        using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("DotAbyss.Observer.Pc.pc-routes.json")
            ?? throw new InvalidDataException("Missing route catalog.");
        using var document = JsonDocument.Parse(stream);
        if (document.RootElement.GetProperty("metadata_sha256").GetString()
            != "d38f026a769e522b136830785ad2789f834c47127be764f89ab294b4d91f056d")
            throw new InvalidDataException("Route catalog belongs to a different release.");
        return document.RootElement.GetProperty("routes").EnumerateArray().SelectMany(value => value.GetString()!.Split('/'))
            .Where(value => value.Length > 0 && !value.Contains('{') && !value.Contains('}')).ToHashSet(StringComparer.Ordinal);
    }

    private static bool IsDigit(char c) => c is >= '0' and <= '9';
}
