using System.Security.Cryptography;
using System.Text.Json;
using System.Threading.Channels;

namespace DotAbyss.Observer.Pc;

public sealed class CaptureSink : IAsyncDisposable
{
    private readonly object gate = new();
    private readonly CaptureOptions options;
    private readonly Channel<QueuedCapture> queue;
    private readonly Task writerTask;
    private readonly string runId = Guid.NewGuid().ToString("N");
    private readonly DateTimeOffset startedAt = DateTimeOffset.UtcNow;
    private readonly Dictionary<string, RouteSummary> routes = new(StringComparer.Ordinal);
    private readonly HashSet<string> schemas = new(StringComparer.Ordinal);
    private readonly HashSet<string> acceptedSchemas = new(StringComparer.Ordinal);
    private long sequence, accepted, written, droppedInvalid, droppedLimit, droppedQueue, droppedClosed, droppedWrite, writerErrors, pendingBytes;
    private long reservedOutputBytes, writtenBytes;
    private bool closing, completed, faulted, routesTruncated, outputLimited;
    private string? lastErrorCode;

    public string RunDirectory { get; }

    public CaptureSink(string outputRoot, CaptureOptions? options = null)
    {
        this.options = options ?? new CaptureOptions();
        this.options.Validate();
        var root = Path.GetFullPath(outputRoot);
        Directory.CreateDirectory(root);
        if ((File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0) throw new IOException("Capture root cannot be a link.");
        RunDirectory = Path.Combine(root, startedAt.ToString("yyyyMMdd-HHmmssfff") + "-" + runId);
        if (Directory.Exists(RunDirectory) || File.Exists(RunDirectory)) throw new IOException("Capture directory already exists.");
        Directory.CreateDirectory(RunDirectory);
        Directory.CreateDirectory(Path.Combine(RunDirectory, "records"));
        Directory.CreateDirectory(Path.Combine(RunDirectory, "schemas"));
        queue = Channel.CreateBounded<QueuedCapture>(new BoundedChannelOptions(this.options.MaxQueuedRecords)
        {
            SingleReader = true, SingleWriter = false, FullMode = BoundedChannelFullMode.Wait, AllowSynchronousContinuations = false
        });
        AtomicWriteAsync(Path.Combine(RunDirectory, "manifest.json"), Manifest("recording", null), replace: false).GetAwaiter().GetResult();
        writerTask = Task.Run(WriteLoopAsync);
    }

    public CaptureStatus Snapshot
    {
        get
        {
            lock (gate) return new CaptureStatus(accepted, written, droppedInvalid, droppedLimit, droppedQueue, droppedClosed,
                droppedWrite, writerErrors, pendingBytes, completed, faulted, lastErrorCode, writtenBytes, outputLimited);
        }
    }

    public bool TryCapture(CaptureInput input)
    {
        try
        {
            lock (gate)
            {
                if (outputLimited) { if (!completed) droppedLimit++; return false; }
                if (closing || faulted)
                {
                    if (!completed) droppedClosed++;
                    return false;
                }
            }
            if (input is null || !CapturePolicy.TryNormalizeRoute(input.Route, out var route))
                return Reject(invalid: true);
            var method = CapturePolicy.NormalizeMethod(input.Method);
            if (method == "UNKNOWN") return Reject(invalid: true);
            var response = new CaptureSanitizer(options).Sanitize(input.ResponseJson);
            var request = input.RequestJson is null ? null : new CaptureSanitizer(options).Sanitize(input.RequestJson);
            var item = CreateRecord(input, method, route, response, request);
            lock (gate)
            {
                if (outputLimited) { if (!completed) droppedLimit++; return false; }
                if (closing || faulted)
                {
                    if (!completed) droppedClosed++;
                    return false;
                }
                if (item.BudgetBytes > options.MaxQueuedBytes - pendingBytes)
                {
                    droppedQueue++;
                    return false;
                }
                var newSchemas = new[] { item.ResponseSchemaHash, item.RequestSchemaHash }.Where(hash => hash is not null)
                    .Cast<string>().Distinct(StringComparer.Ordinal).Where(hash => !acceptedSchemas.Contains(hash)).ToArray();
                if (acceptedSchemas.Count + newSchemas.Length > options.MaxSchemas)
                {
                    droppedLimit++;
                    return false;
                }
                var outputBytes = (long)item.Bytes.Length
                    + (newSchemas.Contains(item.ResponseSchemaHash) ? item.ResponseSchema.Length : 0)
                    + (item.RequestSchemaHash is not null && item.RequestSchemaHash != item.ResponseSchemaHash
                        && newSchemas.Contains(item.RequestSchemaHash) ? item.RequestSchema!.Length : 0);
                if (accepted >= options.MaxRecords || outputBytes > options.MaxWrittenBytes - reservedOutputBytes)
                {
                    droppedLimit++;
                    outputLimited = true;
                    closing = true;
                    lastErrorCode = "output_limit";
                    queue.Writer.TryComplete();
                    return false;
                }
                pendingBytes += item.BudgetBytes;
                if (!queue.Writer.TryWrite(item))
                {
                    pendingBytes -= item.BudgetBytes;
                    droppedQueue++;
                    return false;
                }
                accepted++;
                reservedOutputBytes += outputBytes;
                foreach (var hash in newSchemas) acceptedSchemas.Add(hash);
                return true;
            }
        }
        catch (CaptureLimitException) { return Reject(invalid: false); }
        catch { return Reject(invalid: true); }
    }

    public Task CompleteAsync()
    {
        lock (gate)
        {
            closing = true;
            queue.Writer.TryComplete();
        }
        return writerTask;
    }

    public ValueTask DisposeAsync() => new(CompleteAsync());

    private bool Reject(bool invalid)
    {
        lock (gate)
        {
            if (!completed)
            {
                if (invalid) droppedInvalid++;
                else droppedLimit++;
            }
        }
        return false;
    }

    private QueuedCapture CreateRecord(CaptureInput input, string method, string route, SanitizedPayload response, SanitizedPayload? request)
    {
        var id = Interlocked.Increment(ref sequence);
        var capturedAt = DateTimeOffset.UtcNow;
        var status = input.HttpStatus is >= 100 and <= 599 ? input.HttpStatus : null;
        var format = input.Format is "Json" or "MessagePack" ? input.Format : "Unknown";
        var responseHash = Hash(response.Json);
        var responseSchemaHash = Hash(response.Schema);
        var requestHash = request is null ? null : Hash(request.Json);
        var requestSchemaHash = request is null ? null : Hash(request.Schema);
        using var output = new MemoryStream();
        using (var writer = new Utf8JsonWriter(output))
        {
            writer.WriteStartObject();
            writer.WriteNumber("format_version", 1);
            writer.WriteNumber("sequence", id);
            writer.WriteString("captured_at", capturedAt);
            writer.WriteString("method", method);
            writer.WriteString("route", route);
            if (status.HasValue) writer.WriteNumber("http_status", status.Value); else writer.WriteNull("http_status");
            writer.WriteString("source_format", format);
            writer.WriteString("request_snapshot_at", request is null ? null : "response");
            writer.WriteString("response_sha256", responseHash);
            writer.WriteString("request_sha256", requestHash);
            writer.WriteString("response_schema_sha256", responseSchemaHash);
            writer.WriteString("request_schema_sha256", requestSchemaHash);
            writer.WritePropertyName("response");
            writer.WriteRawValue(response.Json, skipInputValidation: true);
            writer.WritePropertyName("request");
            if (request is null) writer.WriteNullValue(); else writer.WriteRawValue(request.Json, skipInputValidation: true);
            writer.WriteStartArray("asset_references");
            foreach (var reference in response.Assets.Concat(request?.Assets ?? Array.Empty<string>()).Distinct(StringComparer.Ordinal))
                writer.WriteStringValue(reference);
            writer.WriteEndArray();
            writer.WriteEndObject();
        }
        var bytes = output.ToArray();
        return new QueuedCapture(id, capturedAt, method, CapturePolicy.RouteTemplate(route), status, format, bytes,
            responseSchemaHash, response.Schema, requestSchemaHash, request?.Schema);
    }

    private async Task WriteLoopAsync()
    {
        try
        {
            await foreach (var item in queue.Reader.ReadAllAsync().ConfigureAwait(false))
            {
                var recordWritten = false;
                try
                {
                    await WriteSchemaAsync(item.ResponseSchemaHash, item.ResponseSchema).ConfigureAwait(false);
                    if (item.RequestSchemaHash is not null && item.RequestSchema is not null)
                        await WriteSchemaAsync(item.RequestSchemaHash, item.RequestSchema).ConfigureAwait(false);
                    await AtomicWriteAsync(Path.Combine(RunDirectory, "records", item.Sequence.ToString("D8") + ".json"), item.Bytes, replace: false).ConfigureAwait(false);
                    recordWritten = true;
                    lock (gate) { written++; writtenBytes += item.Bytes.Length; }
                    AddRoute(item);
                    await WriteCatalogAsync().ConfigureAwait(false);
                    await AtomicWriteAsync(Path.Combine(RunDirectory, "manifest.json"), Manifest("recording", null), replace: true).ConfigureAwait(false);
                }
                catch
                {
                    if (!recordWritten) lock (gate) droppedWrite++;
                    throw;
                }
                finally
                {
                    lock (gate) pendingBytes -= item.BudgetBytes;
                }
            }
            lock (gate) completed = true;
            await WriteCatalogAsync().ConfigureAwait(false);
            await AtomicWriteAsync(Path.Combine(RunDirectory, "manifest.json"), Manifest(outputLimited ? "output_limit" : "complete", DateTimeOffset.UtcNow), replace: true).ConfigureAwait(false);
        }
        catch (Exception error)
        {
            lock (gate)
            {
                faulted = true;
                closing = true;
                writerErrors++;
                lastErrorCode = error is IOException or UnauthorizedAccessException ? "io_failure" : "writer_failure";
                queue.Writer.TryComplete();
            }
            while (queue.Reader.TryRead(out var pending))
                lock (gate) { droppedWrite++; pendingBytes -= pending.BudgetBytes; }
            lock (gate) completed = true;
            try { await AtomicWriteAsync(Path.Combine(RunDirectory, "manifest.json"), Manifest("write_failed", DateTimeOffset.UtcNow), replace: true).ConfigureAwait(false); }
            catch { }
        }
    }

    private async Task WriteSchemaAsync(string hash, byte[] schema)
    {
        if (schemas.Contains(hash)) return;
        await AtomicWriteAsync(Path.Combine(RunDirectory, "schemas", hash + ".json"), schema, replace: false).ConfigureAwait(false);
        lock (gate) writtenBytes += schema.Length;
        schemas.Add(hash);
    }

    private void AddRoute(QueuedCapture item)
    {
        var key = item.Method + " " + item.Route;
        if (!routes.TryGetValue(key, out var route))
        {
            if (routes.Count >= options.MaxRoutes) { routesTruncated = true; return; }
            routes[key] = route = new RouteSummary(item.Method, item.Route);
        }
        route.Count++;
        route.LastSeen = item.CapturedAt;
        route.StatusCodes.Add(item.Status?.ToString() ?? "unknown");
        route.Formats.Add(item.Format);
        route.ResponseSchemas.Add(item.ResponseSchemaHash);
        if (item.RequestSchemaHash is not null) route.RequestSchemas.Add(item.RequestSchemaHash);
    }

    private Task WriteCatalogAsync() => AtomicWriteAsync(Path.Combine(RunDirectory, "route-catalog.json"),
        JsonSerializer.SerializeToUtf8Bytes(new
        {
            format_version = 1, truncated = routesTruncated, routes = routes.OrderBy(pair => pair.Key, StringComparer.Ordinal).Select(pair => new
            {
                method = pair.Value.Method, route_template = pair.Value.Route, count = pair.Value.Count,
                last_seen = pair.Value.LastSeen, status_codes = pair.Value.StatusCodes, formats = pair.Value.Formats,
                response_schemas = pair.Value.ResponseSchemas, request_schemas = pair.Value.RequestSchemas
            })
        }), replace: true);

    private byte[] Manifest(string state, DateTimeOffset? finishedAt)
    {
        var status = Snapshot;
        return JsonSerializer.SerializeToUtf8Bytes(new
        {
            format_version = 1, run_id = runId, started_at = startedAt, finished_at = finishedAt, state,
            identity = options.Identity is null ? null : new
            {
                application_version = options.Identity.ApplicationVersion, metadata_sha256 = options.Identity.MetadataSha256,
                game_assembly_sha256 = options.Identity.GameAssemblySha256, unity_player_sha256 = options.Identity.UnityPlayerSha256,
                observer_version = options.Identity.ObserverVersion
            },
            limits = new
            {
                body_bytes = options.MaxBodyBytes, body_chars = options.MaxBodyChars, depth = options.MaxDepth, nodes = options.MaxNodes,
                queued_records = options.MaxQueuedRecords, queued_bytes = options.MaxQueuedBytes, schema_fields = options.MaxSchemaFields,
                schemas = options.MaxSchemas, routes = options.MaxRoutes, records = options.MaxRecords, written_bytes = options.MaxWrittenBytes,
                written_bytes_scope = "record_and_unique_schema_files"
            },
            counters = new
            {
                accepted = status.Accepted, written = status.Written, dropped_invalid = status.DroppedInvalid, dropped_limit = status.DroppedLimit,
                dropped_queue = status.DroppedQueue, dropped_closed = status.DroppedClosed, dropped_write = status.DroppedWrite,
                writer_errors = status.WriterErrors, pending_bytes = status.PendingBytes, completed = status.Completed, faulted = status.Faulted,
                last_error_code = status.LastErrorCode, written_bytes = status.WrittenBytes, output_limited = status.OutputLimited
            }
        });
    }

    private static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    private static async Task AtomicWriteAsync(string path, byte[] bytes, bool replace)
    {
        var temporary = path + ".partial";
        await using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 65536, FileOptions.Asynchronous))
        {
            await file.WriteAsync(bytes).ConfigureAwait(false);
            await file.FlushAsync().ConfigureAwait(false);
            file.Flush(flushToDisk: true);
        }
        File.Move(temporary, path, overwrite: replace);
    }

    private sealed record QueuedCapture(long Sequence, DateTimeOffset CapturedAt, string Method, string Route, long? Status, string Format,
        byte[] Bytes, string ResponseSchemaHash, byte[] ResponseSchema, string? RequestSchemaHash, byte[]? RequestSchema)
    {
        internal long BudgetBytes => (long)Bytes.Length + ResponseSchema.Length + (RequestSchema?.Length ?? 0);
    }

    private sealed class RouteSummary
    {
        internal RouteSummary(string method, string route) { Method = method; Route = route; }
        internal string Method { get; }
        internal string Route { get; }
        internal long Count { get; set; }
        internal DateTimeOffset LastSeen { get; set; }
        internal SortedSet<string> StatusCodes { get; } = new(StringComparer.Ordinal);
        internal SortedSet<string> Formats { get; } = new(StringComparer.Ordinal);
        internal SortedSet<string> ResponseSchemas { get; } = new(StringComparer.Ordinal);
        internal SortedSet<string> RequestSchemas { get; } = new(StringComparer.Ordinal);
    }
}
