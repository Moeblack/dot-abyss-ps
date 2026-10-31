using System.Text.Json;
using System.Text.RegularExpressions;

namespace DotAbyss.Local.Pc;

internal sealed record LocalOptions
{
    public string ServiceOrigin { get; init; } = "http://127.0.0.1:18083/";
    public string Profile { get; init; } = "pc-default";
    public string ExpectedCompany { get; init; } = "LocalGames";
    public string ExpectedProduct { get; init; } = "DotAbyssLocalPlayer";
    public string ExpectedPersistentPath { get; init; } = "";
    public int TimeoutSeconds { get; init; } = 20;

    internal static LocalOptions Load(string path)
    {
        if (new FileInfo(path).Length is <= 0 or > 65536)
            throw new InvalidDataException("Invalid local adapter configuration size.");
        using var document = JsonDocument.Parse(File.ReadAllBytes(path));
        if (document.RootElement.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("The local adapter configuration must be an object.");
        var known = typeof(LocalOptions).GetProperties().Select(property => property.Name).ToHashSet(StringComparer.Ordinal);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var property in document.RootElement.EnumerateObject())
            if (!known.Contains(property.Name) || !seen.Add(property.Name))
                throw new InvalidDataException("Unknown or repeated local adapter configuration field.");
        return document.RootElement.Deserialize<LocalOptions>() ?? throw new InvalidDataException("Missing local configuration.");
    }

    internal Uri Validate()
    {
        if (!Uri.TryCreate(ServiceOrigin, UriKind.Absolute, out var origin)
            || origin.Scheme != "http" || origin.Host != "127.0.0.1" || origin.Port is < 1024 or > 65535
            || origin.AbsolutePath != "/" || origin.UserInfo.Length != 0 || origin.Query.Length != 0 || origin.Fragment.Length != 0)
            throw new InvalidDataException("The local service must use a plain IPv4 loopback origin.");
        if (!Regex.IsMatch(Profile, "\\A[A-Za-z0-9_.-]{1,64}\\z") || TimeoutSeconds is < 1 or > 120)
            throw new InvalidDataException("Invalid local profile or timeout.");
        if (ExpectedCompany != "LocalGames" || ExpectedProduct != "DotAbyssLocalPlayer"
            || !Path.IsPathFullyQualified(ExpectedPersistentPath))
            throw new InvalidDataException("The isolated PC identity is not configured.");
        return origin;
    }
}
