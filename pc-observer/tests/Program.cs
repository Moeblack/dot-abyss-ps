using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using DotAbyss.Observer.Pc;

var checks = 0;
void Check(bool condition, string name)
{
    if (!condition) throw new InvalidOperationException(name);
    checks++;
}
void Reject(Action action, string name)
{
    try { action(); }
    catch (Exception error) when (error is CaptureLimitException or JsonException or ArgumentException)
    { checks++; return; }
    throw new InvalidOperationException(name);
}
string Clean(string input, CaptureOptions? options = null) => Encoding.UTF8.GetString(new CaptureSanitizer(options ?? new()).Sanitize(input).Json);

var root = Path.Combine(Path.GetTempPath(), "DotAbyssObserverChecks", Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(root);
try
{
    foreach (var path in new[] { "/api/home", "api/user", "/api/character/12345", "/api/mypage/login", "/api/battle-session/status", "/api/mypage/login-bonus/history" })
        Check(CapturePolicy.TryNormalizeRoute(path, out var route) && route == "/" + path.TrimStart('/'), "Business route and numeric game IDs remain available");
    foreach (var path in new[] { "/login", "/api/auth/start", "/api/ACCOUNT/data", "/api/payment/lineup", "/api/payment-subscription/verify", "/api/charge-center/create-token",
        "/api/push-notification/token/store", "/api/user/login", "/api/session/data", "/api/user\r\nsecret", "//secret.example/api/home", "/api/%2fauth", "/api/%2e%2e/home" })
        Check(!CapturePolicy.TryNormalizeRoute(path, out _), "Credential, billing, or ambiguous routes are not captured");
    Check(CapturePolicy.TryNormalizeRoute("https://private-user:private-password@private-host.example/api/character/12345?token=private-query#private-fragment", out var safeRoute)
        && safeRoute == "/api/character/12345", "Route excludes authority, credentials, query, and fragment");
    Check(CapturePolicy.TryNormalizeRoute("/api/item/9635cfdf-ffde-424b-a7f3-982493eeaef4/abcdefghijklmnopqrstuvwxyz012345", out safeRoute)
        && safeRoute == "/api/item/{redacted}/{redacted}", "UUID and opaque route segments are removed");
    Check(CapturePolicy.TryNormalizeRoute("/api/character/condition_resistance", out safeRoute)
        && safeRoute == "/api/character/condition_resistance", "Pinned static API segments retain their contract identity");
    using (var catalogStream = typeof(CapturePolicy).Assembly.GetManifestResourceStream("DotAbyss.Observer.Pc.pc-routes.json"))
    using (var catalog = JsonDocument.Parse(catalogStream!))
    {
        Check(catalog.RootElement.GetProperty("routes").GetArrayLength() == 285, "Route catalog is bound to the reviewed 285-route client");
        foreach (var known in catalog.RootElement.GetProperty("routes").EnumerateArray())
        {
            var original = known.GetString()!;
            if (CapturePolicy.TryNormalizeRoute(original, out var normalized))
                Check(normalized == "/" + original, "Known static business routes are not mistaken for credential segments");
        }
    }
    Check(CapturePolicy.NormalizeMethod("GET\r\nsecret") == "UNKNOWN", "Method cannot inject logging text");

    var payload = JsonSerializer.Serialize(new
    {
        contents = new Dictionary<string, object?>
        {
            ["coins"] = 123, ["character_id"] = 10011, ["token_count"] = 7,
            ["t_gacha_tokens"] = new[] { new { gacha_token_id = 81, token_count = 3, token = "private-table-credential" } },
            ["apiToken"] = "private-api", ["Session-ID"] = "private-session", ["PASSWORD"] = "private-password",
            ["receipt_data"] = "private-receipt", ["signature"] = "private-signature", ["X-Api-Key"] = "private-key",
            ["user_id"] = 900001, ["email"] = "private-email", ["encrypted_uuid"] = "private-uuid",
            ["nested"] = new[] { new { cookie = "private-cookie", amount = 5 } },
            ["serialized"] = "{\"contents\":{\"Authorization\":\"private-nested\",\"amount\":9}}",
            ["url"] = "https://private-user:private-pass@private-host.example/resources/windows/r18/file.bundle?signature=private-url#private-fragment",
            ["relative_url"] = "/data/master.dat?signature=private-relative",
            ["description"] = "Bearer private-bearer",
            ["opaque"] = "abcdefghijklmnopqrstuvwxyz1234567890",
            ["hash"] = new string('a', 64), ["message"] = "private-error-text"
        }
    });
    var cleaned = Clean(payload);
    Check(!cleaned.Contains("private", StringComparison.OrdinalIgnoreCase) && !cleaned.Contains("abcdefghijklmnopqrstuvwxyz", StringComparison.Ordinal), "Nested credentials and opaque tokens are removed");
    using (var parsed = JsonDocument.Parse(cleaned))
    {
        var contents = parsed.RootElement.GetProperty("contents");
        Check(contents.GetProperty("coins").GetInt32() == 123 && contents.GetProperty("character_id").GetInt32() == 10011, "Business values and stable master IDs are preserved");
        Check(contents.GetProperty("token_count").GetInt32() == 7
            && contents.GetProperty("t_gacha_tokens")[0].GetProperty("token_count").GetInt32() == 3, "Gacha currency is not confused with authentication tokens");
        Check(contents.GetProperty("hash").GetString() == new string('a', 64), "Asset digests remain useful");
        Check(contents.GetProperty("url").GetString() == "/resources/windows/r18/file.bundle"
            && contents.GetProperty("relative_url").GetString() == "/data/master.dat", "Signed resource references retain only safe paths");
        using var nested = JsonDocument.Parse(contents.GetProperty("serialized").GetString()!);
        Check(nested.RootElement.GetProperty("contents").GetProperty("amount").GetInt32() == 9, "Nested JSON strings remain parseable after redaction");
    }
    Check(!Clean("{\"token_count\":\"private-token\",\"gacha_token_id\":\"private-id\"}").Contains("private"), "Business token exceptions require numeric or structured values");
    var battleDetail = JsonSerializer.Serialize(new { stage_id = 101, turns = new[] { new { character_id = 83, hp = 900 } }, api_token = "private-battle-token" });
    using (var battle = JsonDocument.Parse(Clean(JsonSerializer.Serialize(new { contents = new { session_detail = battleDetail } }))))
    using (var detail = JsonDocument.Parse(battle.RootElement.GetProperty("contents").GetProperty("session_detail").GetString()!))
        Check(detail.RootElement.GetProperty("stage_id").GetInt32() == 101
            && detail.RootElement.GetProperty("turns")[0].GetProperty("hp").GetInt32() == 900
            && !detail.RootElement.GetRawText().Contains("private"), "Structured battle session strings retain gameplay state while removing inner credentials");
    foreach (var detail in new[] { "private-opaque-session", "12345", "true", "{\"token\":\"private-unclosed", "\"private-string\"" })
        Check(!Clean(JsonSerializer.Serialize(new { session_detail = detail })).Contains("private")
            && Clean(JsonSerializer.Serialize(new { session_detail = detail })).Contains("[redacted]"), "Session detail exceptions reject opaque and scalar values");
    using (var structured = JsonDocument.Parse(Clean("{\"session_detail\":{\"hp\":5,\"session_token\":\"private-inner\"}}")))
        Check(structured.RootElement.GetProperty("session_detail").GetProperty("hp").GetInt32() == 5
            && !structured.RootElement.GetRawText().Contains("private"), "Object session detail is recursively sanitized");
    using (var structured = JsonDocument.Parse(Clean("{\"session_detail\":[{\"hp\":5,\"password\":\"private-inner\"}]}")))
        Check(structured.RootElement.GetProperty("session_detail")[0].GetProperty("hp").GetInt32() == 5
            && !structured.RootElement.GetRawText().Contains("private"), "Array session detail is recursively sanitized");
    Check(Clean("{\"session_detail\":null}") == "{\"session_detail\":null}", "Null session detail retains its type");
    var longBattle = JsonSerializer.Serialize(new { turns = Enumerable.Range(0, 500).Select(i => new { turn = i, hp = 900 }).ToArray(), token = "private-long-battle" });
    using (var battle = JsonDocument.Parse(Clean(JsonSerializer.Serialize(new { session_detail = longBattle }))))
    using (var detail = JsonDocument.Parse(battle.RootElement.GetProperty("session_detail").GetString()!))
        Check(detail.RootElement.GetProperty("turns").GetArrayLength() == 500 && !detail.RootElement.GetRawText().Contains("private"), "Structured battle state uses the bounded body budget instead of the free-text limit");
    Check(!Clean("{\"session_id\":\"private-id\",\"session\":{\"value\":\"private-session\"}}").Contains("private"), "Battle detail exception does not broaden authentication session handling");
    foreach (var text in new[] { "{\"token\":\"private-unclosed", "%7B%22token%22%3A%22private-encoded%22%7D", "https%3A%2F%2Fprivate-host%2F%3Ftoken=private-token", "prefix https://private.example/?secret=private-secret", "aaaaaaaaaaaa.bbbbbbbbbbbb.cccccccccccc",
        "token%3Dprivate-form", "%257B%2522token%2522%253A%2522private-double%2522%257D", JsonSerializer.Serialize("{\"token\":\"private-nested-string\"}") })
        Check(!Clean(JsonSerializer.Serialize(new { payload = text })).Contains("private", StringComparison.OrdinalIgnoreCase)
            && !Clean(JsonSerializer.Serialize(new { payload = text })).Contains("aaaaaaaaaaaa", StringComparison.Ordinal), "Serialized, encoded, embedded URL, and JWT strings cannot leak credentials");
    Reject(() => Clean("{\"token\":1,\"token\":2}"), "Duplicate JSON keys are rejected");
    Reject(() => Clean("{\"bad\\nkey\":1}"), "Control characters in field names are rejected");
    Reject(() => Clean("{\"value\":\"too large\"}", new CaptureOptions { MaxBodyChars = 8 }), "Character bound is enforced before parsing");
    Reject(() => Clean("{\"value\":\"日本語日本語\"}", new CaptureOptions { MaxBodyBytes = 24 }), "UTF8 byte bound is enforced before parsing");
    Reject(() => Clean("[0,1,2,3,4]", new CaptureOptions { MaxNodes = 3 }), "Node budget bounds array traversal");
    Reject(() => Clean("[[[[[0]]]]]", new CaptureOptions { MaxDepth = 3 }), "Nesting depth is bounded");
    Check(!Clean("{\"text\":\"private-long-value\"}", new CaptureOptions { MaxStringChars = 4 }).Contains("private"), "Long text is omitted without storing it");
    var shape = new CaptureSanitizer(new()).Sanitize("{\"items\":[{\"amount\":1},{\"amount\":null}]}");
    using (var schema = JsonDocument.Parse(shape.Schema))
        Check(schema.RootElement.GetProperty("fields").GetProperty("$.items[].amount").GetArrayLength() == 2, "Schema retains nullable type variants across array items");
    var boundedShape = new CaptureSanitizer(new CaptureOptions { MaxSchemaFields = 1 }).Sanitize("{\"one\":1,\"two\":2}");
    using (var schema = JsonDocument.Parse(boundedShape.Schema))
        Check(schema.RootElement.GetProperty("truncated").GetBoolean(), "Schema budget is explicit");

    await using (var sink = new CaptureSink(Path.Combine(root, "complete")))
    {
        for (var i = 0; i < 8; i++)
            Check(sink.TryCapture(new CaptureInput("GET", "/api/item/" + i, 200, payload, "{\"password\":\"private-request\",\"amount\":2}", "MessagePack")), "Capture accepts a sanitized gameplay record");
        await sink.CompleteAsync();
        Check(sink.Snapshot.Written == 8 && sink.Snapshot.PendingBytes == 0 && sink.Snapshot.Completed && !sink.Snapshot.Faulted, "Shutdown flushes every accepted record");
        var files = Directory.GetFiles(Path.Combine(sink.RunDirectory, "records"), "*.json");
        Check(files.Length == 8 && Directory.GetFiles(sink.RunDirectory, "*.partial", SearchOption.AllDirectories).Length == 0, "Capture files are committed atomically");
        using var record = JsonDocument.Parse(File.ReadAllBytes(files[0]));
        var body = record.RootElement;
        var sanitizedBytes = Encoding.UTF8.GetBytes(body.GetProperty("response").GetRawText());
        Check(body.GetProperty("response_sha256").GetString() == Convert.ToHexString(SHA256.HashData(sanitizedBytes)).ToLowerInvariant(), "Payload digest is based only on sanitized response bytes");
        Check(body.GetProperty("request_snapshot_at").GetString() == "response" && body.GetProperty("request").GetProperty("amount").GetInt32() == 2, "Request snapshot phase is explicit and business inputs survive");
        using var catalog = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(sink.RunDirectory, "route-catalog.json")));
        Check(catalog.RootElement.GetProperty("routes").GetArrayLength() == 1 && catalog.RootElement.GetProperty("routes")[0].GetProperty("count").GetInt64() == 8, "Numeric route instances aggregate into a stable route template");
        using var manifest = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(sink.RunDirectory, "manifest.json")));
        Check(manifest.RootElement.GetProperty("state").GetString() == "complete", "Manifest records a completed run");
        foreach (var file in Directory.GetFiles(sink.RunDirectory, "*.json", SearchOption.AllDirectories))
            Check(!File.ReadAllText(file).Contains("private", StringComparison.OrdinalIgnoreCase), "No output file contains the test secrets");
        Check(!sink.TryCapture(new("GET", "/api/home", 200, "{}")), "Captures after completion are rejected safely");
    }
    await using (var sink = new CaptureSink(Path.Combine(root, "byte-budget"), new CaptureOptions { MaxQueuedBytes = 1 }))
    {
        Check(!sink.TryCapture(new("GET", "/api/home", 200, "{}")) && sink.Snapshot.DroppedQueue == 1, "Byte budget rejects oversized queue entries without blocking");
        Check(!sink.TryCapture(new("GET", "/api/auth/start", 200, "{\"token\":\"private\"}")), "Excluded route is rejected before response capture");
        Check(!sink.TryCapture(new("GET", "/api/home", 200, "invalid private body")), "Malformed bodies do not escape to the caller");
        await sink.CompleteAsync();
        Check(sink.Snapshot.DroppedInvalid == 2 && sink.Snapshot.Written == 0, "Dropped records are counted without persistence");
    }
    await using (var sink = new CaptureSink(Path.Combine(root, "battle")))
    {
        Check(sink.TryCapture(new("GET", "/api/battle-session/status", 200, JsonSerializer.Serialize(new { contents = new { session_detail = battleDetail } }))), "Battle status route accepts structured gameplay state");
        await sink.CompleteAsync();
        using var record = JsonDocument.Parse(File.ReadAllBytes(Directory.GetFiles(Path.Combine(sink.RunDirectory, "records"), "*.json").Single()));
        using var detail = JsonDocument.Parse(record.RootElement.GetProperty("response").GetProperty("contents").GetProperty("session_detail").GetString()!);
        Check(detail.RootElement.GetProperty("turns")[0].GetProperty("character_id").GetInt32() == 83
            && !record.RootElement.GetRawText().Contains("private"), "Persisted battle response preserves reconstructable state without credentials");
    }
    await using (var sink = new CaptureSink(Path.Combine(root, "concurrent"), new CaptureOptions { MaxQueuedRecords = 2 }))
    {
        var outcomes = await Task.WhenAll(Enumerable.Range(0, 50).Select(i => Task.Run(() => sink.TryCapture(new("GET", "/api/home", 200, "{\"amount\":1}")))));
        await sink.CompleteAsync();
        Check(sink.Snapshot.Accepted == outcomes.Count(value => value) && sink.Snapshot.Written == sink.Snapshot.Accepted
            && sink.Snapshot.Accepted + sink.Snapshot.DroppedQueue == 50 && sink.Snapshot.PendingBytes == 0, "Concurrent producers preserve queue accounting and accepted records");
    }
    await using (var sink = new CaptureSink(Path.Combine(root, "schema-budget"), new CaptureOptions { MaxSchemas = 1 }))
    {
        Check(sink.TryCapture(new("GET", "/api/home", 200, "{\"amount\":1}")), "First contract schema can be reserved");
        Check(!sink.TryCapture(new("GET", "/api/home", 200, "{\"different\":1}")) && sink.Snapshot.DroppedLimit == 1, "Distinct schemas have a bounded run budget");
        Check(sink.TryCapture(new("GET", "/api/home", 200, "{\"amount\":2}")), "Known schemas remain capturable after the schema budget is reached");
        await sink.CompleteAsync();
        Check(Directory.GetFiles(Path.Combine(sink.RunDirectory, "schemas"), "*.json").Length == 1 && sink.Snapshot.Written == 2, "Schema references stay complete and bounded");
    }
    await using (var sink = new CaptureSink(Path.Combine(root, "record-limit"), new CaptureOptions { MaxRecords = 2 }))
    {
        Check(sink.TryCapture(new("GET", "/api/home", 200, "{}")) && sink.TryCapture(new("GET", "/api/home", 200, "{}")), "Run accepts records within its output budget");
        Check(!sink.TryCapture(new("GET", "/api/home", 200, "{}")), "Run record budget includes records already waiting in the queue");
        await sink.CompleteAsync();
        Check(sink.Snapshot.Accepted == 2 && sink.Snapshot.Written == 2 && sink.Snapshot.DroppedLimit == 1
            && sink.Snapshot.OutputLimited && !sink.Snapshot.Faulted && sink.Snapshot.LastErrorCode == "output_limit", "Output limit drains accepted records without treating it as a game error");
        using var manifest = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(sink.RunDirectory, "manifest.json")));
        Check(manifest.RootElement.GetProperty("state").GetString() == "output_limit"
            && manifest.RootElement.GetProperty("counters").GetProperty("output_limited").GetBoolean(), "Output limit is persisted with consistent manifest field names");
        var fileBytes = Directory.GetFiles(Path.Combine(sink.RunDirectory, "records")).Concat(Directory.GetFiles(Path.Combine(sink.RunDirectory, "schemas")))
            .Sum(file => new FileInfo(file).Length);
        Check(sink.Snapshot.WrittenBytes == fileBytes, "Written byte accounting includes records and unique schema files");
    }
    await using (var sink = new CaptureSink(Path.Combine(root, "disk-budget"), new CaptureOptions { MaxWrittenBytes = 1 }))
    {
        Check(!sink.TryCapture(new("GET", "/api/home", 200, "{}")), "Disk budget is reserved before any record is queued");
        await sink.CompleteAsync();
        Check(sink.Snapshot.WrittenBytes == 0 && sink.Snapshot.OutputLimited && sink.Snapshot.DroppedLimit == 1
            && Directory.GetFiles(Path.Combine(sink.RunDirectory, "records")).Length == 0, "A rejected disk-budget record cannot leave a partial capture");
    }
    await using (var sink = new CaptureSink(Path.Combine(root, "disk-failure")))
    {
        var records = Path.Combine(sink.RunDirectory, "records");
        Directory.Delete(records);
        File.WriteAllText(records, "occupied");
        Check(sink.TryCapture(new("GET", "/api/home", 200, "{}")), "Capture caller is independent from asynchronous disk failure");
        await sink.CompleteAsync();
        Check(sink.Snapshot.Faulted && sink.Snapshot.WriterErrors == 1 && sink.Snapshot.DroppedWrite == 1
            && sink.Snapshot.LastErrorCode == "io_failure" && sink.Snapshot.PendingBytes == 0, "Disk failure is bounded, counted, and does not throw on shutdown");
        Check(!sink.TryCapture(new("GET", "/api/home", 200, "{}")), "Failed writer stops accepting captures without affecting the game");
    }
    await using (var first = new CaptureSink(Path.Combine(root, "unique")))
    await using (var second = new CaptureSink(Path.Combine(root, "unique")))
        Check(first.RunDirectory != second.RunDirectory, "Runs never overwrite an earlier capture directory");
    Console.WriteLine(JsonSerializer.Serialize(new { checks, result = "passed", native_observer_verified = false }));
}
finally
{
    var parent = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "DotAbyssObserverChecks")) + Path.DirectorySeparatorChar;
    if (!Path.GetFullPath(root).StartsWith(parent, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("Invalid test cleanup path.");
    Directory.Delete(root, recursive: true);
}
