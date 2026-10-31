namespace DotAbyss.Observer.Pc;

public sealed record CaptureInput(string Method, string Route, long? HttpStatus, string ResponseJson,
    string? RequestJson = null, string Format = "Json");

public sealed record CaptureIdentity(string ApplicationVersion, string MetadataSha256, string GameAssemblySha256,
    string UnityPlayerSha256, string ObserverVersion);

public sealed record CaptureOptions
{
    public const int DefaultMaxBodyBytes = 8 * 1024 * 1024;
    public const int DefaultMaxBodyChars = 4 * 1024 * 1024;
    public int MaxBodyBytes { get; init; } = DefaultMaxBodyBytes;
    public int MaxBodyChars { get; init; } = DefaultMaxBodyChars;
    public int MaxDepth { get; init; } = 48;
    public int MaxNodes { get; init; } = 100000;
    public int MaxStringChars { get; init; } = 4096;
    public int MaxSchemaFields { get; init; } = 2048;
    public int MaxSchemas { get; init; } = 1024;
    public int MaxQueuedRecords { get; init; } = 64;
    public long MaxQueuedBytes { get; init; } = 32 * 1024 * 1024;
    public int MaxRoutes { get; init; } = 512;
    public long MaxRecords { get; init; } = 10000;
    public long MaxWrittenBytes { get; init; } = 512L * 1024 * 1024;
    public CaptureIdentity? Identity { get; init; }

    internal void Validate()
    {
        if (MaxBodyBytes is < 1 or > DefaultMaxBodyBytes || MaxBodyChars is < 1 or > DefaultMaxBodyChars
            || MaxDepth is < 1 or > 64 || MaxNodes is < 1 or > 100000 || MaxStringChars is < 1 or > 16384
            || MaxSchemaFields is < 1 or > 4096 || MaxSchemas is < 1 or > 4096 || MaxQueuedRecords is < 1 or > 1024
            || MaxQueuedBytes is < 1 or > 128 * 1024 * 1024 || MaxRoutes is < 1 or > 2048
            || MaxRecords is < 1 or > 100000 || MaxWrittenBytes is < 1 or > 2L * 1024 * 1024 * 1024)
            throw new ArgumentOutOfRangeException(nameof(CaptureOptions));
        if (Identity is not null && (!Version(Identity.ApplicationVersion) || !Version(Identity.ObserverVersion)
            || !Hash(Identity.MetadataSha256) || !Hash(Identity.GameAssemblySha256) || !Hash(Identity.UnityPlayerSha256)))
            throw new ArgumentException("Invalid capture identity.");
    }

    private static bool Hash(string value) => value.Length == 64 && value.All(Uri.IsHexDigit);
    private static bool Version(string value) => value.Length is > 0 and <= 32
        && value.All(c => c is >= '0' and <= '9' or '.' or '-');
}

public sealed record CaptureStatus(long Accepted, long Written, long DroppedInvalid, long DroppedLimit,
    long DroppedQueue, long DroppedClosed, long DroppedWrite, long WriterErrors, long PendingBytes,
    bool Completed, bool Faulted, string? LastErrorCode, long WrittenBytes, bool OutputLimited);
