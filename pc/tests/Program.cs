using System.IO.Compression;
using System.Net;
using System.Text;
using System.Text.Json;
using System.Runtime.InteropServices;
using DotAbyss.Local.Pc;

if (args.SequenceEqual(new[] { "--character-levels" }))
{
    CharacterLevelChecks.Run();
    return;
}

if (args.SequenceEqual(new[] { "--admin-sync" }))
{
    await AdminSyncChecks.Run();
    return;
}

var checks = 0;
void Check(bool condition, string name)
{
    if (!condition) throw new InvalidOperationException(name);
    checks++;
}
void Reject(Action action, string name)
{
    try { action(); }
    catch (Exception error) when (error is InvalidDataException or JsonException or HttpRequestException or ArgumentException)
    { checks++; return; }
    throw new InvalidOperationException(name);
}
byte[] Gzip(byte[] bytes)
{
    using var stream = new MemoryStream();
    using (var gzip = new GZipStream(stream, CompressionLevel.Fastest, leaveOpen: true)) gzip.Write(bytes);
    return stream.ToArray();
}
var options = new LocalOptions { ExpectedPersistentPath = Path.GetFullPath("local-profile") };
var operationIds = new OperationIdentities(3);
var requestOwner = new object();
var completedRequest = false;
var firstOperation = operationIds.Get(100, 200, requestOwner, "POST /api/gacha/exec same-body", () => completedRequest);
Check(operationIds.Get(100, 200, requestOwner, "POST /api/gacha/exec same-body", () => completedRequest) == firstOperation, "One request attempt keeps its identity");
Check(operationIds.Get(100, 201, requestOwner, "POST /api/gacha/exec same-body", () => completedRequest) == firstOperation, "A retry using a new task preserves identity");
var secondOperation = operationIds.Get(101, 202, new object(), "POST /api/gacha/exec same-body", () => false);
Check(secondOperation != firstOperation, "A distinct action with the same payload has a new identity");
completedRequest = true;
var nextOperation = operationIds.Get(100, 203, requestOwner, "POST /api/gacha/exec same-body", () => false);
Check(nextOperation != firstOperation, "Reusing a completed request object starts a new operation");
Check(operationIds.Get(100, 203, requestOwner, "POST /api/gacha/exec changed-body", () => false) != nextOperation, "Changed request parameters start a new operation");
var failedInspection = operationIds.Get(102, 204, new object(), "payload", () => throw new InvalidOperationException());
Check(operationIds.Get(102, 205, new object(), "payload", () => false) == failedInspection, "An unavailable completion status does not discard retry identity");
operationIds.Get(103, 206, new object(), "payload", () => false);
Check(operationIds.Get(101, 207, new object(), "POST /api/gacha/exec same-body", () => false) != secondOperation, "Identity cache accepts new actions after bounded eviction");
Reject(() => operationIds.Get(0, 1, new object(), "payload", () => false), "Missing native identities are rejected");
try { throw new InvalidOperationException("private-token-should-not-appear", new IOException("private-inner-token")); }
catch (Exception error)
{
    var frames = string.Join("\n", ExceptionFrames.Describe(error));
    Check(frames.Contains("InvalidOperationException") && frames.Contains("IOException"), "Exception type chain retained");
    Check(!frames.Contains("private-token") && !frames.Contains("private-inner-token"), "Exception messages excluded from diagnostics");
}
Exception nestedError = new MissingMethodException("SyntheticMember");
for (var depth = 0; depth < 12; depth++) nestedError = new TypeInitializationException("SyntheticType" + depth, nestedError);
var detailedFrames = string.Join("\n", ExceptionFrames.Describe(nestedError, reflectionDetails: true));
Check(detailedFrames.Contains("Exception type[12]") && detailedFrames.Contains("SyntheticMember"), "Deep reflection failure is retained");
Check(detailedFrames.Contains("initialized type: SyntheticType11"), "Failed class name is recorded");
Check(!string.Join("\n", ExceptionFrames.Describe(new HttpRequestException("private-network-value"), true)).Contains("private-network-value"), "Network messages remain excluded during reflection diagnostics");
Check(!string.Join("\n", ExceptionFrames.Describe(new MissingMethodException("token=private-value"), true)).Contains("private-value"), "Reflection secrets remain excluded");
Check(ExceptionFrames.NativeCancellationMessage("System.OperationCanceledException: Canceled.\r\nprivate stack values") == "System.OperationCanceledException: Canceled.", "Native cancellation summary excludes stack text");
Check(!ExceptionFrames.NativeCancellationMessage("Authorization: private-value").Contains("private-value"), "Native cancellation summary excludes credentials");
var origin = options.Validate();
LoopbackProxyPolicy.VerifyIdentity(LoopbackProxyPolicy.UnityPlayerHash, Convert.FromHexString(LoopbackProxyPolicy.EntryHex));
checks++;
Reject(() => LoopbackProxyPolicy.VerifyIdentity(new string('0', 64), Convert.FromHexString(LoopbackProxyPolicy.EntryHex)), "Reject unsupported UnityPlayer digest");
Reject(() => LoopbackProxyPolicy.VerifyIdentity(LoopbackProxyPolicy.UnityPlayerHash, new byte[32]), "Reject unexpected native entry bytes");
var preserved = "<local>;127.*;company.example";
var appended = LoopbackProxyPolicy.AppendLoopback(preserved, out var additions);
Check(appended == preserved + ",127.0.0.1,localhost,::1" && additions == 3, "Keep existing bypass rules unchanged and append exact loopback hosts");
Check(LoopbackProxyPolicy.AppendLoopback("", out additions) == "127.0.0.1,localhost,::1" && additions == 3, "Empty explicit bypass gains only loopback hosts");
Check(LoopbackProxyPolicy.AppendLoopback("127.0.0.1,LOCALHOST,::1", out additions) == "127.0.0.1,LOCALHOST,::1" && additions == 0, "Existing loopback rules are not duplicated");
Check(!LoopbackProxyPolicy.AppendLoopback("example.com", out _).Contains('*'), "The adapter does not introduce wildcard bypass");
Reject(() => LoopbackProxyPolicy.AppendLoopback(new string('x', LoopbackProxyPolicy.MaxCStringBytes), out _), "Bypass length is bounded");
Reject(() => LoopbackProxyPolicy.AppendLoopback("example\0hidden", out _), "Embedded string terminators rejected");
var pointerValue = new IntPtr(unchecked((long)0xfedcba9876543210));
var called = 0;
CurlSetOption untouched = (handle, option, value) =>
{
    Check(handle == new IntPtr(123) && option == 10004 && value == pointerValue, "Other curl options retain all argument bits");
    called++;
    return 27;
};
Check(LoopbackProxyPolicy.Forward(untouched, new IntPtr(123), 10004, pointerValue, out var change) == 27 && change is null && called == 1, "Other option return code is unchanged");
CurlSetOption nullOption = (_, option, value) =>
{
    Check(option == LoopbackProxyPolicy.NoProxyOption && value == IntPtr.Zero, "NULL bypass retains environment semantics");
    return 0;
};
Check(LoopbackProxyPolicy.Forward(nullOption, IntPtr.Zero, LoopbackProxyPolicy.NoProxyOption, IntPtr.Zero, out change) == 0 && change is null, "NULL bypass forwarded unchanged");
var nativeBypass = Marshal.StringToCoTaskMemUTF8(preserved);
try
{
    CurlSetOption reader = (_, option, value) =>
    {
        Check(option == LoopbackProxyPolicy.NoProxyOption && LoopbackProxyPolicy.ReadCString(value) == appended, "Native option receives preserved rules and loopback suffix");
        return 0;
    };
    Check(LoopbackProxyPolicy.Forward(reader, new IntPtr(1), LoopbackProxyPolicy.NoProxyOption, nativeBypass, out change) == 0 && change?.AddedHosts == 3, "Temporary native bypass string is passed synchronously");
}
finally { Marshal.FreeCoTaskMem(nativeBypass); }
foreach (var bypass in new[] { "", "127.0.0.1,localhost,::1" })
{
    nativeBypass = Marshal.StringToCoTaskMemUTF8(bypass);
    try
    {
        CurlSetOption reader = (_, _, value) =>
        {
            Check(LoopbackProxyPolicy.ReadCString(value) == "127.0.0.1,localhost,::1", "Native empty and existing loopback options preserve their contract");
            if (bypass.Length != 0) Check(value == nativeBypass, "Complete existing bypass retains its original pointer");
            return 0;
        };
        LoopbackProxyPolicy.Forward(reader, IntPtr.Zero, LoopbackProxyPolicy.NoProxyOption, nativeBypass, out _);
    }
    finally { Marshal.FreeCoTaskMem(nativeBypass); }
}
nativeBypass = Marshal.StringToCoTaskMemUTF8("example.com");
try
{
    var rejectedFailure = false;
    try { LoopbackProxyPolicy.Forward((_, _, _) => 7, IntPtr.Zero, LoopbackProxyPolicy.NoProxyOption, nativeBypass, out _); }
    catch (InvalidOperationException) { rejectedFailure = true; }
    Check(rejectedFailure, "Failed native bypass setup does not silently continue");
}
finally { Marshal.FreeCoTaskMem(nativeBypass); }
var invalidUtf8 = Marshal.AllocHGlobal(3);
try
{
    Marshal.Copy(new byte[] { 0xc3, 0x28, 0 }, 0, invalidUtf8, 3);
    Reject(() => LoopbackProxyPolicy.ReadCString(invalidUtf8), "Invalid UTF8 bypass rejected");
}
finally { Marshal.FreeHGlobal(invalidUtf8); }
var unterminated = Marshal.AllocHGlobal(LoopbackProxyPolicy.MaxCStringBytes);
try
{
    Marshal.Copy(Enumerable.Repeat((byte)'x', LoopbackProxyPolicy.MaxCStringBytes).ToArray(), 0, unterminated, LoopbackProxyPolicy.MaxCStringBytes);
    Reject(() => LoopbackProxyPolicy.ReadCString(unterminated), "Unterminated bypass does not read beyond the bound");
}
finally { Marshal.FreeHGlobal(unterminated); }
unsafe
{
    var observed = IntPtr.Zero;
    CurlSetOption original = (_, _, value) => { observed = value; return 19; };
    CurlSetOption bridge = (handle, option, value) => LoopbackProxyPolicy.Forward(original, handle, option, value, out _);
    var entry = (delegate* unmanaged[Cdecl]<IntPtr, int, IntPtr, int>)(void*)Marshal.GetFunctionPointerForDelegate(bridge);
    Check(entry(new IntPtr(73), 20011, pointerValue) == 19 && observed == pointerValue, "Unmanaged Win64 third argument pointer bits survive forwarding");
    Check(entry(new IntPtr(73), 52, new IntPtr(1)) == 19 && observed == new IntPtr(1), "Unmanaged scalar option bits survive forwarding");
    GC.KeepAlive(bridge);
    GC.KeepAlive(original);
}
Check(origin.Host == "127.0.0.1" && origin.Port == 18083, "Loopback defaults");
var proxySummary = JsonSerializer.Serialize(TransportPolicy.ProxyEnvironmentStatus("127.0.0.1,localhost,::1,private-host"));
Check(proxySummary.Contains("\"ipv4_loopback\":true") && proxySummary.Contains("\"ipv6_loopback\":true"), "Process bypass diagnostics preserve loopback flags");
Check(!proxySummary.Contains("private-host"), "Process bypass diagnostics do not expose configured host values");
foreach (var route in new[] { "maintenance", "login", "terms/fetch", "version", "api/user", "api/home", "api/mypage/login" })
{
    var nativeUrl = new Uri(string.Concat(TransportPolicy.ApiBase(origin), route), UriKind.Absolute);
    Check(TransportPolicy.SameOrigin(nativeUrl, origin) && nativeUrl.AbsolutePath == "/" + route, "Native API concatenation keeps the port and route separate: " + route);
}
foreach (var url in new[] { "http://example.com:18083/", "https://127.0.0.1:18083/", "http://localhost:18083/", "http://127.0.0.1:80/", "http://u:p@127.0.0.1:18083/", "http://127.0.0.1:18083/api", "http://127.0.0.1:18083/?token=x", "http://127.0.0.1:18083/#x" })
    Reject(() => (options with { ServiceOrigin = url }).Validate(), "Reject unsafe origin");
Reject(() => (options with { ExpectedCompany = "EXNOA LLC." }).Validate(), "Reject shared company");
Reject(() => (options with { Profile = "../official" }).Validate(), "Reject profile traversal");
Reject(() => (options with { TimeoutSeconds = 0 }).Validate(), "Reject invalid timeout");
Check(!TransportPolicy.SameOrigin(new Uri("http://127.0.0.1:18084/api/home"), origin), "Port binding");
Check(!TransportPolicy.SameOrigin(new Uri("https://127.0.0.1:18083/api/home"), origin), "Scheme binding");
Check(TransportPolicy.SameOrigin(new Uri(origin, "/api/home"), origin), "Native local origin");
Check(TransportPolicy.IsResourcePath("/resources/windows/r18/aas/0.1.0/aa/catalog_1.bin"), "PC resource route");
Check(TransportPolicy.IsCatalogRoot(new Uri(origin, "/resources/windows/r18/aas/0.1.0/aa"), origin), "Native catalog root includes its final aa component");
Check(!TransportPolicy.IsCatalogRoot(new Uri(origin, "/resources/windows/r18/aas/0.1.0"), origin), "Missing aa component rejected");
Check(!TransportPolicy.IsCatalogRoot(new Uri(origin, "/resources/windows/r18/aas/0.1.0/aa/aa"), origin), "Duplicated aa component rejected");
Check(!TransportPolicy.IsCatalogRoot(new Uri(origin, "/resources/windows/r18/aas/0.1.1/aa"), origin), "Unverified catalog version rejected");
Check(!TransportPolicy.IsCatalogRoot(new Uri(origin, "/resources/android/r18/aas/0.1.0/aa"), origin), "Other platform catalog rejected");
Check(!TransportPolicy.IsCatalogRoot(new Uri(origin, "/resources/windows/r18/aas/0.1.0/aa/"), origin), "Unverified trailing catalog separator rejected");
Check(TransportPolicy.CatalogHashPath == "/resources/windows/r18/aas/0.1.0/aa/catalog_1.hash", "Catalog hash consumer uses exact native root");
Check(TransportPolicy.ResourcePathDiagnostic(new Uri("https://private.example/resources/windows/r18/aas/0.1.0/aa?token=private")) == TransportPolicy.CatalogRoot, "Catalog diagnostics contain only a bounded resource path");
Check(!TransportPolicy.ResourcePathDiagnostic(new Uri("https://private.example/secret-value")).Contains("secret-value"), "Nonresource diagnostic path excluded");
Check(!TransportPolicy.IsResourcePath("/resources/android/r18/aa/catalog_1.bin"), "Reject Android namespace");
Check(!TransportPolicy.IsApiPath("/api-lookalike"), "API path boundary");
Check(!TransportPolicy.AllowsHttp(new Uri("https://reports.example/api/report"), origin), "External report upload rejected");
Check(!TransportPolicy.AllowsHttp(new Uri("http://127.0.0.1:7897/api/report"), origin), "Proxy endpoint cannot receive a local report");
Check(!TransportPolicy.AllowsHttp(new Uri(origin, "/local/blocked-external-request"), origin), "Rejected uploads are not resent to a fallback route");
Check(TransportPolicy.AllowsHttp(new Uri(origin, "/api/home"), origin), "Native local API remains allowed");
Check(TransportPolicy.IsApiPath("/terms/fetch") && TransportPolicy.AllowsHttp(new Uri(origin, "/terms/fetch?platform_type=4"), origin), "Terms bootstrap uses the authenticated local API transport");
foreach (var address in new[] { "https://external.example/terms/fetch", "http://127.0.0.1:18084/terms/fetch", "http://user:pass@127.0.0.1:18083/terms/fetch" })
    Check(!TransportPolicy.AllowsHttp(new Uri(address), origin), "Terms bootstrap retains origin and credential restrictions");
foreach (var path in new[] { "/terms/fetch-other", "/terms/fetch/", "/terms/fetch/detail", "/terms/get-view/1", "/Terms/fetch", "/terms/fetch%2fprivate" })
    Check(!TransportPolicy.IsApiPath(path) && !TransportPolicy.AllowsHttp(new Uri(origin, path), origin), "Terms bootstrap does not permit lookalikes or nested routes: " + path);
Check(TransportPolicy.IsApiPath("/version") && TransportPolicy.AllowsHttp(new Uri(origin, "/version?app_version=1.15.0"), origin), "Version bootstrap uses the authenticated local API transport");
foreach (var address in new[] { "https://external.example/version", "http://127.0.0.1:18084/version", "http://user:pass@127.0.0.1:18083/version" })
    Check(!TransportPolicy.AllowsHttp(new Uri(address), origin), "Version bootstrap retains origin and credential restrictions");
foreach (var path in new[] { "/version-other", "/version/", "/version/detail", "/Version", "/version%2fprivate" })
    Check(!TransportPolicy.IsApiPath(path) && !TransportPolicy.AllowsHttp(new Uri(origin, path), origin), "Version bootstrap does not permit lookalikes or nested routes: " + path);
foreach (var path in new[] { "/uuid", "/uuid/verified", "/dmm/client/acquisition", "/dmm/client/regist" })
{
    using var diagnostic = JsonDocument.Parse(TransportPolicy.RejectedRequestDiagnostic("GET", new Uri(origin, path), origin));
    var value = diagnostic.RootElement;
    Check(value.GetProperty("path").GetString() == path && value.GetProperty("same_origin").GetBoolean()
        && value.GetProperty("is_loopback").GetBoolean() && value.GetProperty("port").GetInt32() == 18083
        && value.GetProperty("reason").GetString() == "unsupported_local_path", "Local startup denial identifies the fixed route: " + path);
    Check(!TransportPolicy.AllowsHttp(new Uri(origin, path), origin), "Diagnostics do not expand the request whitelist: " + path);
}
foreach (var path in new[] { "/terms/get-view/private-value", "/api/private-value", "/resources/private-value", "/private-value",
    "/terms/get-view/private%2fvalue", "/terms/get-view/private%0d%0avalue", "/terms/private-value/fetch", "/terms/get-view/" + new string('x', 300) })
{
    var diagnostic = TransportPolicy.RejectedRequestDiagnostic("GET", new Uri("https://private-user:private-pass@private-host.example" + path + "?token=private-query#private-fragment"), origin);
    using var document = JsonDocument.Parse(diagnostic);
    Check(!diagnostic.Contains("private", StringComparison.OrdinalIgnoreCase) && diagnostic.Length < 350
        && !diagnostic.Contains('\r') && !diagnostic.Contains('\n'), "Rejected URL diagnostics omit credentials and bounded dynamic path values");
    Check(!document.RootElement.GetProperty("same_origin").GetBoolean()
        && document.RootElement.GetProperty("reason").GetString() == "different_origin", "External rejection is distinct from an unsupported local route");
}
using (var diagnostic = JsonDocument.Parse(TransportPolicy.RejectedRequestDiagnostic("GET\r\nprivate-value", new Uri("private-scheme://127.0.0.1/terms/fetch"), origin)))
{
    Check(diagnostic.RootElement.GetProperty("method").GetString() == "<redacted>"
        && diagnostic.RootElement.GetProperty("scheme").GetString() == "<other>", "Unknown methods and schemes cannot inject diagnostic values");
}
using (var diagnostic = JsonDocument.Parse(TransportPolicy.RejectedRequestDiagnostic("GET", new Uri(origin, "/terms/get-view/12345"), origin)))
    Check(diagnostic.RootElement.GetProperty("path").GetString() == "/terms/get-view/<redacted>", "Parameterized terms route keeps only its fixed prefix");
Check(TransportPolicy.LocalizeOrigin("https://example.com/data/", origin) == "http://127.0.0.1:18083/data/", "Preserve native master suffix");
Check(TransportPolicy.LocalizeOrigin("https://example.com/", origin) == origin.AbsoluteUri, "Preserve native root suffix");
Check(TransportPolicy.LocalizeOrigin("https://example.com/resources/windows/r18/aas/0.1.0", origin) == "http://127.0.0.1:18083/resources/windows/r18/aas/0.1.0", "Preserve native platform suffix");
Check(TransportPolicy.SameOrigin(new Uri(TransportPolicy.LocalizeOrigin("https://example.com//outside.example/path", origin)), origin), "Double-slash resource paths cannot replace origin");
Check(TransportPolicy.SameOrigin(new Uri(TransportPolicy.LocalizeOrigin("https://example.com/%2f%2foutside.example/path", origin)), origin), "Escaped path separators cannot replace origin");
Reject(() => TransportPolicy.LocalizeOrigin("https://example.com/data/?token=x", origin), "Reject signed resource config");
foreach (var path in new[] { "/data/master.bin", "/master/current.dat", "/resources/windows/r18/aas/0.1.0/aa/file.bundle", "/data/escaped%20name.dat" })
{
    var localUrl = new Uri(origin, path).AbsoluteUri;
    Check(TransportPolicy.TryLocalResourceUrl(localUrl, origin, out var preservedUrl) && preservedUrl == localUrl
        && new Uri(preservedUrl).Scheme == "http" && new Uri(preservedUrl).Port == 18083,
        "Unsigned local resource retains its exact HTTP origin and path during link signing: " + path);
}
foreach (var url in new string?[] { null, "not a URL", "/data/master.bin", "https://127.0.0.1:18083/data/master.bin",
    "http://127.0.0.1/data/master.bin", "http://127.0.0.1:18084/data/master.bin", "http://external.example:18083/data/master.bin",
    "http://user:pass@127.0.0.1:18083/data/master.bin", "http://127.0.0.1:18083/data-other/master.bin",
    "http://127.0.0.1:18083/master-other/current.dat", "http://127.0.0.1:18083/resources/windows/r18-other/file.bundle",
    "http://127.0.0.1:18083/resources/android/r18/file.bundle", "http://127.0.0.1:18083/api/home",
    "http://127.0.0.1:18083/data/master.bin?signature=value", "http://127.0.0.1:18083/data/master.bin#fragment",
    "http://127.0.0.1:18083/data/master.bin?", "http://127.0.0.1:18083/data/master.bin#" })
    Check(!TransportPolicy.TryLocalResourceUrl(url, origin, out var preservedUrl) && preservedUrl == "",
        "Unrecognized, external, signed, or nonresource URLs retain native link handling");
var directory = Path.GetFullPath("prepared");
Check(TransportPolicy.IsWithin(Path.Combine(directory, "data", "settings.json"), directory), "Contained files");
Check(!TransportPolicy.IsWithin(directory + "-official/file", directory), "Path prefix boundary");
Check(!TransportPolicy.IsWithin(Path.Combine(directory, "..", "official"), directory), "Normalized traversal");
var json = Encoding.UTF8.GetBytes("{\"amount\":3}");
Check(TransportPolicy.JsonBody(json).SequenceEqual(json), "JSON passes unchanged");
Check(TransportPolicy.JsonBody(Gzip(json)).SequenceEqual(json), "GZip becomes plaintext JSON");
Reject(() => TransportPolicy.JsonBody(Encoding.UTF8.GetBytes("[]")), "Reject nonobject JSON");
Reject(() => TransportPolicy.JsonBody(new byte[] { 0xff, 0x17, 0x98 }), "Reject encrypted body");
Reject(() => TransportPolicy.JsonBody(new byte[TransportPolicy.MaxBodyBytes + 1]), "Compressed input bound");
Reject(() => TransportPolicy.JsonBody(Gzip(new byte[TransportPolicy.MaxBodyBytes + 1])), "Expanded body bound");
var config = Path.GetTempFileName();
try
{
    File.WriteAllText(config, "{\"Profile\":\"first\",\"Profile\":\"second\"}");
    Reject(() => LocalOptions.Load(config), "Duplicate configuration");
    File.WriteAllText(config, "{\"ServiceOrign\":\"http://other\"}");
    Reject(() => LocalOptions.Load(config), "Unknown configuration");
}
finally { File.Delete(config); }

foreach (var failure in new[] { "", "package", "version", "metadata", "master", "revision", "prefix", "catalog", "state", "asset-client", "schema" })
{
    using var binding = JsonDocument.Parse(JsonSerializer.Serialize(new
    {
        master = new
        {
            client = new { package = failure == "package" ? "com.exnoa.abyss" : "dotabyss_x_cl", version = failure == "version" ? "1.14.4" : ClientIdentity.ApplicationVersion,
                metadata_sha256 = failure == "metadata" ? "changed" : ClientIdentity.MetadataHash },
            sha256 = failure == "master" ? "changed" : ClientIdentity.MasterHash,
            revision = "local-pc-master", schema_sha256 = "schema-hash"
        },
        assets = new
        {
            error = "",
            active = new
            {
                master = new
                {
                    revision = failure == "revision" ? "other-master" : "local-pc-master", sha256 = ClientIdentity.MasterHash,
                    schema_sha256 = failure == "schema" ? "wrong-schema" : "schema-hash",
                    client = new { package = failure == "asset-client" ? "com.exnoa.abyss" : "dotabyss_x_cl", version = ClientIdentity.ApplicationVersion, metadata_sha256 = ClientIdentity.MetadataHash }
                },
                prefix = failure == "prefix" ? "resources/android/r18/aas/0.1.0/aa" : "resources/windows/r18/aas/0.1.0/aa", catalog_served = failure != "catalog"
            }
        },
        local_state = new { enabled = failure != "state", error = "" }
    }));
    if (failure == "") { LocalSession.VerifyBinding(binding.RootElement); checks++; }
    else Reject(() => LocalSession.VerifyBinding(binding.RootElement), "Reject mismatched binding: " + failure);
}

foreach (var serverTime in new[] { "2026-10-30 19:00:00", "2026-10-31T04:00:00+09:00" })
using (var handler = new LocalHandler { ServerTime = serverTime })
using (var session = new LocalSession(origin, options, handler))
{
    var first = session.Get();
    Check(ReferenceEquals(first, session.Get()) && handler.Bootstraps == 1, "Session reuse");
    var login = session.AuthenticateGameLogin();
    Check(login.UserId == first.UserId && login.Uuid == first.Uuid, "Local login identity");
    Check(login.ServerTime == serverTime, "Native login retains the explicit server time offset");
    Check(handler.AuthenticatedRequests == 1, "Local authenticated request headers");
    session.Report("native_login_returned", new { ready = true });
    Check(handler.AuthenticatedRequests == 2 && handler.ReportComponent == "pc-adapter", "PC diagnostic identity");
    Reject(() => session.RegisterRuntime("1", "0.1.0", "1.14.4"), "Reject runtime version mismatch");
    Reject(() => session.RegisterRuntime("1", "9.9.9", "1.15.0"), "Reject bundle mismatch");
}
foreach (var failure in new[] { "user", "uuid", "session", "api", "status", "time", "error", "http" })
{
    using var handler = new LocalHandler { Failure = failure };
    using var session = new LocalSession(origin, options, handler);
    session.Get();
    Reject(() => session.AuthenticateGameLogin(), "Reject inconsistent local login: " + failure);
}
var liveServiceVerified = false;
var clientFilesVerified = false;
if (args.Length == 2 && args[0] == "--verify-server")
{
    var liveOptions = options with { ServiceOrigin = args[1] };
    using var live = new LocalSession(liveOptions.Validate(), liveOptions);
    live.VerifyServer();
    liveServiceVerified = true;
}
if (args.Length == 2 && args[0] == "--verify-client")
{
    ClientIdentity.VerifyFiles(args[1]);
    checks++;
    var temporary = Path.Combine(Path.GetTempPath(), "dotabyss-pc-settings-" + Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(temporary);
    var settingsPath = Path.Combine(temporary, "globalgamemanagers");
    try
    {
        File.Copy(Path.Combine(args[1], "globalgamemanagers"), settingsPath);
        using (var file = new FileStream(settingsPath, FileMode.Open, FileAccess.ReadWrite))
        {
            var first = file.ReadByte();
            file.Position = 0;
            file.WriteByte((byte)(first ^ 1));
        }
        Reject(() => ClientIdentity.VerifySettings(temporary), "Reject altered isolated PlayerSettings");
    }
    finally { File.Delete(settingsPath); Directory.Delete(temporary); }
    clientFilesVerified = true;
}
Console.WriteLine(JsonSerializer.Serialize(new { checks, result = "passed", live_service_verified = liveServiceVerified, client_files_verified = clientFilesVerified, native_client_verified = false }));

internal sealed class LocalHandler : HttpMessageHandler
{
    private const string Token = "local-session-01234567890123456789";
    private const string ApiToken = "local-api-012345678901234567890123";
    private const string Uuid = "baaa503b-5f0f-46c1-a4ec-f2d684ad3fc3";
    public string Failure { get; init; } = "";
    public string ServerTime { get; init; } = "2026-10-31T04:00:00+09:00";
    public int Bootstraps { get; private set; }
    public int AuthenticatedRequests { get; private set; }
    public string ReportComponent { get; private set; } = "";

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        var path = request.RequestUri!.AbsolutePath;
        var login = path == "/login";
        using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellationToken));
        if (path == "/local/login")
        {
            Bootstraps++;
            if (body.RootElement.TryGetProperty("token", out _)) throw new InvalidDataException("External token in bootstrap");
        }
        else
        {
            if (request.Headers.GetValues("x-olg-session").Single() != Token || request.Headers.GetValues("api-token").Single() != ApiToken)
                throw new InvalidDataException("Missing local authentication");
            AuthenticatedRequests++;
        }
        if (path == "/local/diagnostics") ReportComponent = body.RootElement.GetProperty("component").GetString()!;
        var value = new
        {
            status = "success",
            errors = new { code = login && Failure == "error" ? 10 : 0 },
            api_token = login && Failure == "api" ? "wrong" : ApiToken,
            server_time = login && Failure == "time" ? "not-a-date" : ServerTime,
            contents = new
            {
                user_id = login && Failure == "user" ? 88L : 77L,
                uuid = login && Failure == "uuid" ? Guid.NewGuid().ToString() : Uuid,
                token = login && Failure == "session" ? "wrong" : Token,
                token_status = login && Failure == "status" ? 1 : 0
            }
        };
        return new HttpResponseMessage(login && Failure == "http" ? HttpStatusCode.Unauthorized : HttpStatusCode.OK)
        { Content = new StringContent(JsonSerializer.Serialize(value), Encoding.UTF8, "application/json") };
    }
}
