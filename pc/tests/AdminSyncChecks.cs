using System.Net;
using System.Text;
using System.Text.Json;
using DotAbyss.Local.Pc;

internal static class AdminSyncChecks
{
    internal static async Task Run()
    {
        var count = 0;
        void Check(bool value, string name)
        {
            if (!value) throw new InvalidOperationException(name);
            count++;
        }
        void Reject(string payload, string name)
        {
            try { AdminSyncSnapshot.Parse(Encoding.UTF8.GetBytes(payload), 77); }
            catch (Exception error) when (error is InvalidDataException or JsonException) { count++; return; }
            throw new InvalidOperationException(name);
        }
        var records = "{\"t_characters\":{\"up\":[{\"id\":9223372036854775700,\"user_id\":77,\"m_character_id\":1001,\"level\":30,\"intimacy_point\":500}],\"down\":[42]},\"t_feature_locks\":{\"up\":[{\"id\":9,\"m_feature_lock_id\":9,\"is_unlocked\":true}],\"down\":[]}}";
        var payload = "{\"revision\":5,\"pending\":true,\"refresh\":\"records\",\"records\":" + records + "}";
        var snapshot = AdminSyncSnapshot.Parse(Encoding.UTF8.GetBytes(payload), 77);
        Check(snapshot.Revision == 5 && snapshot.Pending && snapshot.RecordsJson.Contains("9223372036854775700"), "Preserve 64-bit row identities");
        Check(snapshot.RecordsJson.Contains("\"down\":[42]"), "Preserve deletion tombstones");
        Check(snapshot.RecordsJson.Contains("\"is_unlocked\":true"), "Preserve native Boolean fields");
        Check(AdminSyncSnapshot.Parse(Encoding.UTF8.GetBytes("{\"revision\":0,\"pending\":false}"), 77).RecordsJson == "{}", "Empty synchronization state");
        Check(AdminSyncSnapshot.Parse(Encoding.UTF8.GetBytes("{\"revision\":6,\"pending\":true,\"refresh\":\"relogin\",\"resource_version\":\"local-new\"}"), 77).Refresh == "relogin", "Master changes require reload");
        Reject(payload.Replace("\"user_id\":77", "\"user_id\":78"), "Reject another profile");
        Reject(payload.Replace("\"user_id\":77", "\"user_id\":\"77\""), "Reject string ownership");
        Reject(payload.Replace("\"revision\":5", "\"revision\":-1"), "Reject negative revisions");
        Reject(payload.Replace("\"revision\":5", "\"revision\":\"5\""), "Reject string revisions");
        Reject(payload.Replace("\"revision\":5", "\"revision\":5,\"revision\":6"), "Reject duplicate envelope fields");
        Reject(payload.Replace("\"level\":30", "\"level\":30,\"level\":1"), "Reject duplicate row fields");
        Reject(payload.Replace("\"down\":[42]", "\"down\":[-1]"), "Reject invalid deletion IDs");
        Reject(payload.Replace("\"down\":[42]", "\"down\":[true]"), "Reject noninteger deletion IDs");
        Reject(payload.Replace("\"down\":[42]", "\"down\":[9223372036854775808]"), "Reject overflowing deletion IDs");
        Reject(payload.Replace("\"refresh\":\"records\"", "\"refresh\":\"execute\""), "Reject unsupported refresh mode");
        Reject("{\"revision\":1,\"pending\":true}", "Pending record synchronization needs a payload");
        Reject("{\"revision\":1,\"pending\":true,\"records\":[]}", "Reject arrays as record maps");
        Reject(payload.Replace("t_characters", "$type"), "Reject metadata collection keys");
        Check(AdminSyncPolicy.CanApply("Home", true, true, 9, 9, 500), "Apply while idle at home");
        Check(!AdminSyncPolicy.CanApply("Battle", true, true, 9, 9, 500), "Do not alter a battle");
        Check(!AdminSyncPolicy.CanApply("Home", false, true, 9, 9, 500), "Wait for native user data");
        Check(!AdminSyncPolicy.CanApply("Home", true, false, 9, 9, 500), "Wait for in-flight requests");
        Check(!AdminSyncPolicy.CanApply("Home", true, true, 9, 10, 500), "Discard snapshots fetched across native activity");
        Check(!AdminSyncPolicy.CanApply("Home", true, true, 9, 9, 499), "Wait for scene request callbacks to settle");
        var evidence = new AdminSyncEvidence();
        evidence.Applied(100, 1, new object());
        evidence.UserResponse(100, 1, "master-a", new object());
        Check(evidence.HasLoaded("master-a", "master-a", true), "Accept an applied full user snapshot followed by its response");
        Check(!evidence.HasLoaded("master-b", "master-a", true), "Old login cannot acknowledge a new master version");
        Check(!evidence.HasLoaded("master-a", "master-b", true), "Loaded and requested master must match");
        Check(!evidence.HasLoaded("master-a", "master-a", false), "Wait until master data has been deserialized");
        evidence.UserResponse(200, 2, "master-b", new object());
        evidence.Applied(201, 2, new object());
        Check(!evidence.HasLoaded("master-b", "master-b", true), "Another response's records are insufficient");
        evidence.Applied(200, 3, new object());
        Check(!evidence.HasLoaded("master-b", "master-b", true), "Intervening request invalidates login evidence");
        evidence.Applied(200, 2, new object());
        Check(evidence.HasLoaded("master-b", "master-b", true), "Accept response first and record application second");
        Check(!evidence.HasLoaded("", "", true), "Missing versions never prove successful reload");
        var clientId = Guid.NewGuid().ToString("N");
        using var handler = new SyncHandler(payload, clientId);
        var options = new LocalOptions { ExpectedPersistentPath = Path.GetFullPath("isolated-profile") };
        using var session = new LocalSession(options.Validate(), options, handler);
        var received = await session.PollAdminSync(4, clientId);
        Check(received.Revision == 5 && handler.Polls == 1, "Poll with the profile's authenticated identity");
        await session.AcknowledgeAdminSync(new AdminSyncAck(clientId, 5, "applied", "Home", "native_data_applied"));
        Check(handler.Acknowledgements == 1 && handler.Bootstraps == 1, "Acknowledge through the same isolated session");
        handler.Status = HttpStatusCode.Unauthorized;
        try { await session.PollAdminSync(4, clientId); throw new InvalidOperationException("Expected HTTP rejection"); }
        catch (HttpRequestException) { count++; }
        Console.WriteLine(JsonSerializer.Serialize(new { checks = count, result = "passed", native_client_verified = false }));
    }

    private sealed class SyncHandler(string payload, string clientId) : HttpMessageHandler
    {
        private const string Token = "local-session-01234567890123456789";
        private const string Api = "local-api-012345678901234567890123";
        internal int Bootstraps;
        internal int Polls;
        internal int Acknowledgements;
        internal HttpStatusCode Status = HttpStatusCode.OK;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var path = request.RequestUri!.AbsolutePath;
            var response = payload;
            if (path == "/local/login")
            {
                Bootstraps++;
                response = JsonSerializer.Serialize(new
                {
                    status = "success", errors = new { code = 0 }, api_token = Api,
                    contents = new { user_id = 77, uuid = "baaa503b-5f0f-46c1-a4ec-f2d684ad3fc3", token = Token, token_status = 0 }
                });
            }
            else
            {
                if (request.Headers.GetValues("x-olg-session").Single() != Token || request.Headers.GetValues("api-token").Single() != Api
                    || request.Headers.Contains("x-admin-token"))
                    throw new InvalidOperationException("Synchronization did not preserve session authentication");
                if (path == "/local/admin-sync")
                {
                    Polls++;
                    if (request.Method != HttpMethod.Get || request.Content is not null
                        || request.RequestUri.Query != "?after=4&client_id=" + clientId)
                        throw new InvalidOperationException("Invalid synchronization query");
                }
                else if (path == "/local/admin-sync/ack")
                {
                    Acknowledgements++;
                    using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellationToken));
                    if (request.Method != HttpMethod.Post || body.RootElement.GetProperty("client_id").GetString() != clientId
                        || body.RootElement.GetProperty("revision").GetInt64() != 5 || body.RootElement.GetProperty("status").GetString() != "applied")
                        throw new InvalidOperationException("Invalid synchronization acknowledgement");
                    response = "{\"status\":\"ok\"}";
                }
                else throw new InvalidOperationException("Unexpected synchronization endpoint");
            }
            return new HttpResponseMessage(Status) { Content = new StringContent(response, Encoding.UTF8, "application/json") };
        }
    }
}
