using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using DotAbyss.Local;

var checks = 0;
void Check(bool passed, string label)
{
    if (!passed)
    {
        Console.Error.WriteLine($"FAIL: {label}");
        Environment.Exit(1);
    }
    checks++;
}

if (args.Length == 2 && args[0] is "--server" or "--game-server")
{
    var integrationOptions = new LocalOptions { ServerBaseUrl = args[1], Profile = "adapter-contract-check" };
    using var integration = new LocalSession(integrationOptions.Validate(), integrationOptions);
    Check(integration.Get().UserId > 0, "Local login did not return a user ID");
    if (args[0] == "--server") integration.RegisterRuntime("synthetic-app-code", "synthetic-bundle", "synthetic-application");
    Check(integration.AuthenticateGameLogin().UserId == integration.Get().UserId, "The game login did not retain the local identity");
    integration.Report("managed_contract_verified", new { native_runtime_verified = false });
    using var http = new HttpClient(new SocketsHttpHandler { UseProxy = false, AllowAutoRedirect = false });
    using var diagnostic = JsonDocument.Parse(await http.GetStringAsync(new Uri(integrationOptions.Validate(), "/diagnostics")));
    Check(diagnostic.RootElement.GetProperty("client_events").EnumerateArray()
        .Any(item => item.GetProperty("event").GetString() == "managed_contract_verified"), "Server did not retain the client diagnostic event");
    Console.WriteLine($"PASS {checks} live HTTP contract checks; Android runtime was not executed");
    return;
}

var testOutput = new DirectoryInfo(AppContext.BaseDirectory);
var adapterAssembly = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../../../../bin", testOutput.Parent!.Name, testOutput.Name, "DotAbyss.Local.dll"));
Check(BytecodeChecks.EmptyTermsUsesLengthConstructor(adapterAssembly), "Empty TermsAgree array bound to the native-pointer constructor instead of the length constructor");
Check(BytecodeChecks.LoginTaskUsesNativeFieldCopy(adapterAssembly), "Login task construction returned to the boxed-value generic constructor");

foreach (var url in new[] { "http://127.0.0.1:18081", "http://localhost:18081", "http://10.0.2.2:18081" })
{
    Check(new LocalOptions { ServerBaseUrl = url }.Validate().Port == 18081, "Local origin rejected");
    var origin = new LocalOptions { ServerBaseUrl = url }.Validate();
    foreach (var relative in new[] { "maintenance", "api/home", "api/mypage/login" })
    {
        var concatenated = new Uri(origin.AbsoluteUri + relative);
        Check(concatenated.Port == 18081 && concatenated.AbsolutePath == "/" + relative,
            "The native client's string concatenation lost the API base trailing slash");
    }
}

foreach (var url in new[] { "https://example.org", "http://192.168.1.2", "http://example.org", "http://user:pass@localhost", "http://localhost/api", "http://localhost/?token=secret" })
{
    var rejected = false;
    try { new LocalOptions { ServerBaseUrl = url }.Validate(); }
    catch (InvalidOperationException) { rejected = true; }
    Check(rejected, "Unsafe or ambiguous origin accepted");
}
foreach (var profile in new[] { "", "测试", "two words", "../profile", new string('a', 65) })
{
    var rejected = false;
    try { new LocalOptions { Profile = profile }.Validate(); }
    catch (InvalidOperationException) { rejected = true; }
    Check(rejected, "Profile incompatible with the local server was accepted");
}

var shape = ProtocolLog.Shape("{\"token\":\"super-secret-token\",\"user\":{\"name\":\"private-name\"},\"list\":[\"hidden\"]}");
Check(!shape.Contains("super-secret") && !shape.Contains("private-name") && !shape.Contains("hidden"), "Body value leaked");
Check(!ProtocolLog.SafeUrl("http://127.0.0.1/api/login?token=secret").Contains("secret"), "Query leaked");
Check(!ProtocolLog.SafeUrl("http://127.0.0.1/api/user/123456789").Contains("123456789"), "User ID leaked");
Check(!ProtocolLog.Shape("not json secret").Contains("secret"), "Invalid body leaked");
var captured = ProtocolLog.RedactResponse("{\"contents\":{\"token\":\"secret-token\",\"uuid\":\"private-id\",\"coins\":10,\"items\":[{\"session_id\":\"private-session\"}],\"url\":\"http://localhost/api?token=secret\"}}")!;
Check(!captured.Contains("secret") && !captured.Contains("private-"), "Captured response leaked secrets");
Check(captured.Contains("10") && captured.Contains("coins"), "Captured response lost non-sensitive data");
Check(!ProtocolLog.RedactResponse("\"http://localhost/api?token=secret\"")!.Contains("secret"), "Root URL leaked query");
Check(!ProtocolLog.StringShape("private-session-token").Contains("private-session-token"), "Observed string value leaked");
try
{
    throw new InvalidOperationException("private-token-value", new ArgumentException("private-session-value"));
}
catch (InvalidOperationException exception)
{
    var frames = ProtocolLog.ExceptionFrames(exception);
    Check(frames.Contains("InvalidOperationException") && frames.Contains("ArgumentException") && !frames.Contains("private-"),
        "Exception diagnostics leaked values or lost exception types");
}
Check(ProtocolLog.StringShape("2026-10-30 12:00:00").Contains("parses_as_datetime=True"), "Observed date shape was not detected");
Check(!new LocalOptions().ObserveOnly, "Observation must be explicitly enabled");
Check(!new LocalOptions().AllowFarTrampolines, "Far trampoline experiments must be explicitly enabled");
Check(!new LocalOptions().StandaloneNativeLoginProbe, "Standalone native login probes must be explicitly enabled");

using var socket = new TcpListener(IPAddress.Loopback, 0);
socket.Start();
var port = ((IPEndPoint)socket.LocalEndpoint).Port;
socket.Stop();
using var listener = new HttpListener();
listener.Prefixes.Add($"http://127.0.0.1:{port}/");
listener.Start();
var requests = new List<string>();
var diagnosticAuthenticated = false;
var loginBodyContainsOnlyProfile = false;
var gameLoginAuthenticated = false;
var runtimeVersionSourcesDistinct = false;
var serve = Task.Run(async () =>
{
    for (var index = 0; index < 4; index++)
    {
        var context = await listener.GetContextAsync();
        requests.Add(context.Request.Url!.AbsolutePath);
        using (var bodyReader = new StreamReader(context.Request.InputStream))
        {
            using var requestDocument = JsonDocument.Parse(await bodyReader.ReadToEndAsync());
            if (index == 0)
                loginBodyContainsOnlyProfile = requestDocument.RootElement.EnumerateObject().Select(p => p.Name).SequenceEqual(new[] { "profile" });
            if (index == 3)
                gameLoginAuthenticated = requestDocument.RootElement.GetProperty("token").GetString() == "local-session-token-123456"
                    && context.Request.Headers["x-olg-session"] == "local-session-token-123456"
                    && context.Request.Headers["api-token"] == "local-api-token-123456";
            if (index == 1)
                runtimeVersionSourcesDistinct = requestDocument.RootElement.GetProperty("app_version_code").GetString() == "synthetic-app-code"
                    && requestDocument.RootElement.GetProperty("bundle_version").GetString() == "synthetic-bundle"
                    && requestDocument.RootElement.GetProperty("application_version").GetString() == "synthetic-application";
        }
        if (index == 2)
            diagnosticAuthenticated = context.Request.Headers["x-olg-session"] == "local-session-token-123456"
                && context.Request.Headers["api-token"] == "local-api-token-123456";
        var json = index is 0 or 3
            ? GameLoginEnvelope()
            : "{\"status\":\"ok\"}";
        var body = Encoding.UTF8.GetBytes(json);
        context.Response.ContentType = "application/json";
        context.Response.ContentLength64 = body.Length;
        await context.Response.OutputStream.WriteAsync(body);
        context.Response.Close();
    }
});
var options = new LocalOptions { ServerBaseUrl = $"http://127.0.0.1:{port}" };
using var session = new LocalSession(options.Validate(), options);
Check(session.Get().UserId == 1, "Local login contract failed");
Check(ReferenceEquals(session.Get(), session.Get()), "Session was unnecessarily regenerated");
session.RegisterRuntime("synthetic-app-code", "synthetic-bundle", "synthetic-application");
session.Report("managed_contract_verified", new { native_runtime_verified = false });
var gameLogin = session.AuthenticateGameLogin();
Check(gameLogin.UserId == 1 && gameLogin.ServerTime == "2026-10-30 12:00:00", "Game login result lost its identity or server time");
await serve.WaitAsync(TimeSpan.FromSeconds(5));
Check(requests.SequenceEqual(new[] { "/local/login", "/local/runtime", "/local/diagnostics", "/login" }), "Unexpected local protocol paths");
Check(diagnosticAuthenticated, "Diagnostic event lacked local authentication");
Check(loginBodyContainsOnlyProfile, "Local login sent unexpected account fields");
Check(gameLoginAuthenticated, "Game login did not use only the established local token and headers");
Check(runtimeVersionSourcesDistinct, "Runtime app code and Unity Application.version were conflated");
var rejectServe = Task.Run(async () =>
{
    var context = await listener.GetContextAsync();
    var body = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new
    {
        status = "error",
        api_token = "local-api-token-123456",
        contents = new { user_id = 1L, uuid = "b16f3f97-2d8f-4599-83df-715af45be8d3", token = "local-session-token-123456" }
    }));
    context.Response.ContentType = "application/json";
    context.Response.ContentLength64 = body.Length;
    await context.Response.OutputStream.WriteAsync(body);
    context.Response.Close();
});
using var rejectedSession = new LocalSession(options.Validate(), options);
var rejectedEnvelope = false;
try { rejectedSession.Get(); }
catch (InvalidDataException) { rejectedEnvelope = true; }
await rejectServe.WaitAsync(TimeSpan.FromSeconds(5));
Check(rejectedEnvelope, "HTTP 200 error envelope was accepted as a local session");
foreach (var explicitTime in new[] { "2026-10-30T21:00:00+09:00", "2026-10-30 12:00:00" })
{
    var timeServe = Task.Run(async () =>
    {
        var context = await listener.GetContextAsync();
        var body = Encoding.UTF8.GetBytes(GameLoginEnvelope(serverTime: explicitTime));
        context.Response.ContentType = "application/json";
        context.Response.ContentLength64 = body.Length;
        await context.Response.OutputStream.WriteAsync(body);
        context.Response.Close();
    });
    Check(session.AuthenticateGameLogin().ServerTime == explicitTime, "Server time offset was not preserved");
    await timeServe.WaitAsync(TimeSpan.FromSeconds(5));
}
foreach (var invalidLogin in new[]
{
    GameLoginEnvelope(status: "error"),
    GameLoginEnvelope(errorCode: 1),
    GameLoginEnvelope(userId: 2),
    GameLoginEnvelope(uuid: "00000000-0000-0000-0000-000000000000"),
    GameLoginEnvelope(token: "different-local-token"),
    GameLoginEnvelope(apiToken: "different-api-token"),
    GameLoginEnvelope(tokenStatus: 1),
    GameLoginEnvelope(serverTime: "not-a-date")
})
{
    var invalidServe = Task.Run(async () =>
    {
        var context = await listener.GetContextAsync();
        var body = Encoding.UTF8.GetBytes(invalidLogin);
        context.Response.ContentType = "application/json";
        context.Response.ContentLength64 = body.Length;
        await context.Response.OutputStream.WriteAsync(body);
        context.Response.Close();
    });
    var rejected = false;
    try { session.AuthenticateGameLogin(); }
    catch (InvalidDataException) { rejected = true; }
    await invalidServe.WaitAsync(TimeSpan.FromSeconds(5));
    Check(rejected, "A mismatched or failed game login envelope was accepted");
}
Console.WriteLine($"PASS {checks} checks");

static string GameLoginEnvelope(string status = "success", int errorCode = 0, long userId = 1,
    string uuid = "b16f3f97-2d8f-4599-83df-715af45be8d3", string token = "local-session-token-123456",
    string apiToken = "local-api-token-123456", int tokenStatus = 0, string serverTime = "2026-10-30 12:00:00")
    => JsonSerializer.Serialize(new
    {
        status,
        server_time = serverTime,
        errors = new { code = errorCode, message = "", severity = "", app_transition = "" },
        api_token = apiToken,
        contents = new { user_id = userId, uuid, token, token_status = tokenStatus }
    });
