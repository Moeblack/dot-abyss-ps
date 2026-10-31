using System.Net.Http.Json;
using System.Text.Json;
using System.Globalization;

namespace DotAbyss.Local;

internal sealed class LocalSession : IDisposable
{
    private readonly HttpClient client;
    private readonly string profile;
    private readonly object gate = new();
    private SessionData? session;

    internal LocalSession(Uri origin, LocalOptions options)
    {
        client = new HttpClient(new SocketsHttpHandler { AllowAutoRedirect = false, UseProxy = false })
        {
            BaseAddress = origin,
            Timeout = TimeSpan.FromSeconds(options.TimeoutSeconds),
            MaxResponseContentBufferSize = 1024 * 1024
        };
        profile = options.Profile;
    }

    internal SessionData Get()
    {
        lock (gate)
        {
            return session ??= CreateAsync().GetAwaiter().GetResult();
        }
    }

    internal void RegisterRuntime(string appCode, string bundle, string applicationVersion)
    {
        using var response = client.PostAsJsonAsync("/local/runtime", new
        {
            app_version_code = appCode,
            bundle_version = bundle,
            application_version = applicationVersion
        }).ConfigureAwait(false).GetAwaiter().GetResult();
        response.EnsureSuccessStatusCode();
    }

    internal void Report(string eventName, object details)
    {
        var data = Get();
        using var request = new HttpRequestMessage(HttpMethod.Post, "/local/diagnostics")
        {
            Content = JsonContent.Create(new { component = "android-adapter", @event = eventName, details })
        };
        request.Headers.Add("x-olg-session", data.Token);
        request.Headers.Add("api-token", data.ApiToken);
        using var response = client.SendAsync(request).ConfigureAwait(false).GetAwaiter().GetResult();
        response.EnsureSuccessStatusCode();
    }

    internal GameLoginData AuthenticateGameLogin()
        => AuthenticateGameLoginAsync(Get()).GetAwaiter().GetResult();

    private async Task<GameLoginData> AuthenticateGameLoginAsync(SessionData data)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, "/login")
        {
            Content = JsonContent.Create(new { token = data.Token })
        };
        request.Headers.Add("x-olg-session", data.Token);
        request.Headers.Add("api-token", data.ApiToken);
        using var response = await client.SendAsync(request).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync().ConfigureAwait(false));
        var root = document.RootElement;
        ValidateSuccessfulStatus(root);
        if (!root.TryGetProperty("errors", out var errors) || errors.ValueKind != JsonValueKind.Object
            || !errors.TryGetProperty("code", out var code) || !code.TryGetInt32(out var errorCode) || errorCode != 0)
            throw new InvalidDataException("The game login envelope contains an error or an invalid error contract.");
        var contents = root.GetProperty("contents");
        var userId = contents.GetProperty("user_id").GetInt64();
        var uuid = contents.GetProperty("uuid").GetString();
        if (userId != data.UserId || uuid != data.Uuid || contents.GetProperty("token").GetString() != data.Token
            || root.GetProperty("api_token").GetString() != data.ApiToken || contents.GetProperty("token_status").GetInt32() != 0)
            throw new InvalidDataException("The game login response does not match the established local session.");
        var serverTime = root.GetProperty("server_time").GetString();
        if (!DateTimeOffset.TryParseExact(serverTime, new[] { "yyyy-MM-dd'T'HH:mm:sszzz", "yyyy-MM-dd HH:mm:ss" }, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out _))
            throw new InvalidDataException("The game login server_time is not a valid game timestamp.");
        return new GameLoginData(userId, uuid!, serverTime!);
    }

    private async Task<SessionData> CreateAsync()
    {
        using var response = await client.PostAsJsonAsync("/local/login", new { profile }).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync().ConfigureAwait(false));
        var root = document.RootElement;
        ValidateSuccessfulStatus(root);
        var contents = root.GetProperty("contents");
        var data = new SessionData(contents.GetProperty("user_id").GetInt64(),
            contents.GetProperty("uuid").GetString() ?? "",
            contents.GetProperty("token").GetString() ?? "",
            root.GetProperty("api_token").GetString() ?? "");
        if (data.UserId <= 0 || !Guid.TryParse(data.Uuid, out _) || data.Token.Length is < 16 or > 512
            || data.ApiToken.Length is < 16 or > 512)
            throw new InvalidDataException("Local login returned an invalid session contract.");
        return data;
    }

    private static void ValidateSuccessfulStatus(JsonElement root)
    {
        if (!root.TryGetProperty("status", out var status) || status.ValueKind != JsonValueKind.String
            || status.GetString() != "success")
            throw new InvalidDataException("Local login did not return a successful envelope.");
    }

    public void Dispose() => client.Dispose();
}

internal sealed record SessionData(long UserId, string Uuid, string Token, string ApiToken);
internal sealed record GameLoginData(long UserId, string Uuid, string ServerTime);
