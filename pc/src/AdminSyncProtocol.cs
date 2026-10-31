using System.Text.Json;

namespace DotAbyss.Local.Pc;

internal sealed record AdminSyncSnapshot(long Revision, bool Pending, string Refresh, string RecordsJson,
    string ResourceVersion, string Reason)
{
    internal const int MaxPayloadBytes = 16 * 1024 * 1024;

    internal static AdminSyncSnapshot Parse(ReadOnlyMemory<byte> payload, long userId)
    {
        if (payload.Length is 0 or > MaxPayloadBytes || userId <= 0)
            throw new InvalidDataException("Invalid administrator synchronization payload.");
        using var document = JsonDocument.Parse(payload, new JsonDocumentOptions { MaxDepth = 64 });
        var root = document.RootElement;
        UniqueProperties(root);
        if (!root.TryGetProperty("revision", out var revision) || revision.ValueKind != JsonValueKind.Number || !revision.TryGetInt64(out var number) || number < 0
            || !root.TryGetProperty("pending", out var pending) || pending.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
            throw new InvalidDataException("Administrator synchronization has no valid revision.");
        var refresh = String(root, "refresh", "records", 32);
        if (refresh is not ("records" or "relogin"))
            throw new InvalidDataException("Unknown administrator refresh mode.");
        var records = "{}";
        if (root.TryGetProperty("records", out var data))
        {
            UniqueProperties(data);
            var collectionCount = 0;
            var rowCount = 0;
            foreach (var collection in data.EnumerateObject())
            {
                if (++collectionCount > 512 || !collection.Name.StartsWith("t_", StringComparison.Ordinal)
                    || collection.Name.Length > 100 || !collection.Name.All(c => c is >= 'a' and <= 'z' or >= '0' and <= '9' or '_'))
                    throw new InvalidDataException("Invalid administrator record collection.");
                UniqueProperties(collection.Value);
                if (!collection.Value.TryGetProperty("up", out var up) || up.ValueKind != JsonValueKind.Array
                    || !collection.Value.TryGetProperty("down", out var down) || down.ValueKind != JsonValueKind.Array
                    || collection.Value.EnumerateObject().Count() != 2)
                    throw new InvalidDataException("Administrator records require updates and deletions.");
                rowCount += up.GetArrayLength() + down.GetArrayLength();
                if (rowCount > 100000) throw new InvalidDataException("Too many administrator records.");
                foreach (var row in up.EnumerateArray())
                {
                    UniqueProperties(row);
                    if (row.TryGetProperty("user_id", out var owner) && (owner.ValueKind != JsonValueKind.Number || !owner.TryGetInt64(out var id) || id != userId))
                        throw new InvalidDataException("Administrator records belong to another profile.");
                }
                foreach (var id in down.EnumerateArray())
                    if (id.ValueKind != JsonValueKind.Number || !id.TryGetInt64(out var value) || value <= 0)
                        throw new InvalidDataException("Invalid administrator deletion identity.");
            }
            records = data.GetRawText();
        }
        else if (pending.GetBoolean() && refresh == "records")
            throw new InvalidDataException("Administrator synchronization has no record payload.");
        return new AdminSyncSnapshot(number, pending.GetBoolean(), refresh, records,
            String(root, "resource_version", "", 256), String(root, "reason", "", 512));
    }

    private static string String(JsonElement root, string name, string fallback, int limit)
    {
        if (!root.TryGetProperty(name, out var property) || property.ValueKind == JsonValueKind.Null) return fallback;
        if (property.ValueKind != JsonValueKind.String || property.GetString() is not { } value || value.Length > limit)
            throw new InvalidDataException("Invalid administrator synchronization text.");
        return value;
    }

    private static void UniqueProperties(JsonElement value)
    {
        if (value.ValueKind != JsonValueKind.Object) throw new InvalidDataException("Expected an administrator record object.");
        var names = new HashSet<string>(StringComparer.Ordinal);
        foreach (var property in value.EnumerateObject())
            if (!names.Add(property.Name)) throw new InvalidDataException("Duplicate administrator record field.");
    }
}

internal sealed record AdminSyncAck(string client_id, long revision, string status, string scene, string reason);

internal static class AdminSyncPolicy
{
    internal static bool CanApply(string scene, bool userReady, bool apiIdle, long capturedActivity, long currentActivity,
        long quietMilliseconds) => scene == "Home" && userReady && apiIdle && capturedActivity == currentActivity && quietMilliseconds >= 500;

    internal static string ClientId(string value) => Guid.TryParseExact(value, "N", out _) ? value
        : throw new InvalidDataException("Invalid administrator synchronization client identity.");
}

internal sealed class AdminSyncEvidence
{
    private readonly Dictionary<long, (long Activity, object Owner)> applied = new();
    private (long Identity, long Activity, string Version, object Owner)? response;
    private string verifiedVersion = "";

    internal void Applied(long identity, long activity, object owner)
    {
        if (identity == 0) return;
        applied[identity] = (activity, owner);
        if (applied.Count > 8) applied.Remove(applied.Keys.First());
        Match();
    }

    internal void UserResponse(long identity, long activity, string version, object owner)
    {
        if (identity == 0 || string.IsNullOrEmpty(version)) return;
        response = (identity, activity, version, owner);
        Match();
    }

    private void Match()
    {
        if (response is { } candidate && applied.TryGetValue(candidate.Identity, out var row) && row.Activity == candidate.Activity)
        {
            verifiedVersion = candidate.Version;
            response = null;
            applied.Clear();
        }
    }

    internal bool HasLoaded(string expectedVersion, string loadedVersion, bool deserialized) =>
        deserialized && !string.IsNullOrEmpty(expectedVersion) && expectedVersion == loadedVersion && expectedVersion == verifiedVersion;
}
