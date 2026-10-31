using System.Globalization;
using System.Net.Http.Json;
using System.Text.Json;

namespace DotAbyss.Local.Pc;

internal sealed class LocalSession : IDisposable
{
    private readonly HttpClient client;
    private readonly string profile;
    private readonly object gate = new();
    private SessionData? session;

    internal LocalSession(Uri origin, LocalOptions options, HttpMessageHandler? handler = null)
    {
        client = new HttpClient(handler ?? new SocketsHttpHandler { AllowAutoRedirect = false, UseProxy = false })
        {
            BaseAddress = origin,
            Timeout = TimeSpan.FromSeconds(options.TimeoutSeconds),
            MaxResponseContentBufferSize = AdminSyncSnapshot.MaxPayloadBytes
        };
        profile = options.Profile;
    }

    internal SessionData Get()
    {
        lock (gate) return session ??= CreateAsync().ConfigureAwait(false).GetAwaiter().GetResult();
    }

    internal void VerifyServer()
    {
        using var response = client.GetAsync("/diagnostics").ConfigureAwait(false).GetAwaiter().GetResult();
        response.EnsureSuccessStatusCode();
        using var document = JsonDocument.Parse(response.Content.ReadAsStringAsync().ConfigureAwait(false).GetAwaiter().GetResult());
        VerifyBinding(document.RootElement);
        using var hashResponse = client.GetAsync(TransportPolicy.CatalogHashPath).ConfigureAwait(false).GetAwaiter().GetResult();
        hashResponse.EnsureSuccessStatusCode();
        if (hashResponse.Content.ReadAsStringAsync().ConfigureAwait(false).GetAwaiter().GetResult().Trim() != ClientIdentity.CatalogVersionHash)
            throw new InvalidDataException("The local catalog does not match this prepared PC client.");
    }

    internal static void VerifyBinding(JsonElement status)
    {
        var master = status.GetProperty("master");
        var client = master.GetProperty("client");
        var assets = status.GetProperty("assets");
        var active = assets.GetProperty("active");
        var assetMaster = active.GetProperty("master");
        if (client.GetProperty("package").GetString() != "dotabyss_x_cl"
            || client.GetProperty("version").GetString() != ClientIdentity.ApplicationVersion
            || client.GetProperty("metadata_sha256").GetString() != ClientIdentity.MetadataHash
            || master.GetProperty("sha256").GetString() != ClientIdentity.MasterHash
            || assets.GetProperty("error").GetString() != ""
            || assetMaster.GetProperty("revision").GetString() != master.GetProperty("revision").GetString()
            || assetMaster.GetProperty("sha256").GetString() != ClientIdentity.MasterHash
            || assetMaster.GetProperty("schema_sha256").GetString() != master.GetProperty("schema_sha256").GetString()
            || assetMaster.GetProperty("client").GetProperty("package").GetString() != "dotabyss_x_cl"
            || assetMaster.GetProperty("client").GetProperty("version").GetString() != ClientIdentity.ApplicationVersion
            || assetMaster.GetProperty("client").GetProperty("metadata_sha256").GetString() != ClientIdentity.MetadataHash
            || active.GetProperty("prefix").GetString() != TransportPolicy.CatalogPrefix
            || !active.GetProperty("catalog_served").GetBoolean()
            || !status.GetProperty("local_state").GetProperty("enabled").GetBoolean()
            || status.GetProperty("local_state").GetProperty("error").GetString() != "")
            throw new InvalidDataException("The local service master, assets and PC profile are not bound together.");
    }

    internal void RegisterRuntime(string appCode, string bundle, string applicationVersion)
    {
        if (applicationVersion != ClientIdentity.ApplicationVersion || string.IsNullOrWhiteSpace(appCode) || bundle != "0.1.0")
            throw new InvalidDataException("The native runtime does not match the prepared client.");
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
        using var request = Authenticated(HttpMethod.Post, "/local/diagnostics", new { component = "pc-adapter", @event = eventName, details });
        using var response = client.SendAsync(request).ConfigureAwait(false).GetAwaiter().GetResult();
        response.EnsureSuccessStatusCode();
    }

    internal async Task<AdminSyncSnapshot> PollAdminSync(long after, string clientId, CancellationToken cancellation = default)
    {
        if (after < 0) throw new ArgumentOutOfRangeException(nameof(after));
        var identity = Get();
        var path = "/local/admin-sync?after=" + after.ToString(CultureInfo.InvariantCulture)
            + "&client_id=" + AdminSyncPolicy.ClientId(clientId);
        using var request = Authenticated(HttpMethod.Get, path);
        using var response = await client.SendAsync(request, cancellation).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadAsByteArrayAsync(cancellation).ConfigureAwait(false);
        return AdminSyncSnapshot.Parse(payload, identity.UserId);
    }

    internal async Task AcknowledgeAdminSync(AdminSyncAck acknowledgement, CancellationToken cancellation = default)
    {
        AdminSyncPolicy.ClientId(acknowledgement.client_id);
        if (acknowledgement.revision < 0 || acknowledgement.status is not ("applied" or "waiting_safe_point" or "relogin_required" or "failed"))
            throw new InvalidDataException("Invalid administrator synchronization acknowledgement.");
        using var request = Authenticated(HttpMethod.Post, "/local/admin-sync/ack", acknowledgement);
        using var response = await client.SendAsync(request, cancellation).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
    }

    internal GameLoginData AuthenticateGameLogin()
    {
        var identity = Get();
        using var request = Authenticated(HttpMethod.Post, "/login", new { token = identity.Token });
        using var response = client.SendAsync(request).ConfigureAwait(false).GetAwaiter().GetResult();
        response.EnsureSuccessStatusCode();
        using var document = JsonDocument.Parse(response.Content.ReadAsStringAsync().ConfigureAwait(false).GetAwaiter().GetResult());
        var root = document.RootElement;
        ValidateEnvelope(root);
        var contents = root.GetProperty("contents");
        if (contents.GetProperty("user_id").GetInt64() != identity.UserId
            || contents.GetProperty("uuid").GetString() != identity.Uuid
            || contents.GetProperty("token").GetString() != identity.Token
            || root.GetProperty("api_token").GetString() != identity.ApiToken
            || contents.GetProperty("token_status").GetInt32() != 0)
            throw new InvalidDataException("Game login did not preserve the established local identity.");
        var serverTime = root.GetProperty("server_time").GetString();
        if (!DateTimeOffset.TryParseExact(serverTime, new[] { "yyyy-MM-dd'T'HH:mm:sszzz", "yyyy-MM-dd HH:mm:ss" }, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out _))
            throw new InvalidDataException("Game login returned an invalid server time.");
        return new GameLoginData(identity.UserId, identity.Uuid, serverTime!);
    }

    private HttpRequestMessage Authenticated(HttpMethod method, string path, object? payload = null)
    {
        var identity = Get();
        var request = new HttpRequestMessage(method, path);
        if (payload is not null) request.Content = JsonContent.Create(payload);
        request.Headers.Add("x-olg-session", identity.Token);
        request.Headers.Add("api-token", identity.ApiToken);
        return request;
    }

    private async Task<SessionData> CreateAsync()
    {
        using var response = await client.PostAsJsonAsync("/local/login", new { profile }).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync().ConfigureAwait(false));
        var root = document.RootElement;
        ValidateEnvelope(root);
        var contents = root.GetProperty("contents");
        var identity = new SessionData(contents.GetProperty("user_id").GetInt64(),
            contents.GetProperty("uuid").GetString() ?? "", contents.GetProperty("token").GetString() ?? "",
            root.GetProperty("api_token").GetString() ?? "");
        if (identity.UserId <= 0 || !Guid.TryParse(identity.Uuid, out _)
            || identity.Token.Length is < 16 or > 512 || identity.ApiToken.Length is < 16 or > 512
            || contents.GetProperty("token_status").GetInt32() != 0)
            throw new InvalidDataException("Local bootstrap returned an invalid identity.");
        return identity;
    }

    private static void ValidateEnvelope(JsonElement root)
    {
        if (root.GetProperty("status").GetString() != "success" || root.GetProperty("errors").GetProperty("code").GetInt32() != 0)
            throw new InvalidDataException("Local server returned an unsuccessful envelope.");
    }

    public void Dispose() => client.Dispose();
}

internal sealed record SessionData(long UserId, string Uuid, string Token, string ApiToken);
internal sealed record GameLoginData(long UserId, string Uuid, string ServerTime);
