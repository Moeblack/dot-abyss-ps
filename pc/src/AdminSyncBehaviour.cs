using Absf;
using Absf.Api;
using Il2CppInterop.Runtime;
using Newtonsoft.Json;
using Project.Api;
using Project.User;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace DotAbyss.Local.Pc;

public sealed class AdminSyncBehaviour : MonoBehaviour
{
    private static readonly HashSet<string> recordNames = typeof(RecordEntity).GetProperties()
        .Where(property => property.CanWrite && property.Name.StartsWith("t_", StringComparison.Ordinal))
        .Select(property => property.Name).ToHashSet(StringComparer.Ordinal);
    private readonly string clientId = Guid.NewGuid().ToString("N");
    private readonly CancellationTokenSource cancellation = new();
    private Task<AdminSyncSnapshot>? poll;
    private Task? acknowledgement;
    private AdminSyncAck? queuedAck;
    private AdminSyncAck? sentAck;
    private long appliedRevision;
    private long rejectedRevision = -1;
    private long capturedActivity;
    private long nextPollAt;
    private long nextAckAt;
    private string diagnostic = "";
    private int failures;
    private string previousScene = "";
    private long sceneChangedAt;

    public AdminSyncBehaviour(IntPtr pointer) : base(pointer) { }

    public void Update()
    {
        if (!Plugin.Active || cancellation.IsCancellationRequested) return;
        try
        {
            var now = Environment.TickCount64;
            var scene = SceneManager.GetActiveScene().name;
            if (scene != previousScene)
            {
                previousScene = scene;
                sceneChangedAt = now;
            }
            CompleteAcknowledgement(now);
            if (poll is { IsCompleted: true }) CompletePoll(now);
            if (poll is null && now >= nextPollAt)
            {
                capturedActivity = TransportPatches.ActivityVersion;
                poll = Plugin.Session.PollAdminSync(appliedRevision, clientId, cancellation.Token);
                nextPollAt = now + 2000;
            }
            if (acknowledgement is null && queuedAck is not null && now >= nextAckAt)
            {
                sentAck = queuedAck;
                acknowledgement = Plugin.Session.AcknowledgeAdminSync(sentAck, cancellation.Token);
            }
        }
        catch (Exception error)
        {
            poll = null;
            nextPollAt = Environment.TickCount64 + 10000;
            Describe("bridge_" + error.GetType().Name);
        }
    }

    private void CompletePoll(long now)
    {
        var task = poll!;
        poll = null;
        AdminSyncSnapshot snapshot;
        try
        {
            snapshot = task.GetAwaiter().GetResult();
            failures = 0;
        }
        catch (Exception error)
        {
            nextPollAt = now + Math.Min(30000, 2000 * ++failures);
            Describe("poll_" + error.GetType().Name);
            return;
        }
        if (snapshot.Revision < appliedRevision)
        {
            Describe("revision_regressed");
            return;
        }
        if (!snapshot.Pending || snapshot.Revision == appliedRevision) return;
        var scene = SceneManager.GetActiveScene().name;
        if (snapshot.Refresh == "relogin" && !AdminSyncNativeState.HasLoaded(snapshot.ResourceVersion))
        {
            Queue(snapshot.Revision, "relogin_required", scene, "master_reload_required");
            return;
        }
        if (snapshot.Revision == rejectedRevision) return;
        var user = Engine.IsBoot ? Engine.Get<UserData>() : null;
        var manager = Engine.IsBoot ? Engine.Get<ApiManager>() : null;
        var idle = manager is not null && (manager._currentTask is null || manager._currentTask.IsFinish)
            && (manager._tasks is null || manager._tasks.Count == 0);
        var ready = user?.UserStatus?.UserId == Plugin.Session.Get().UserId;
        if (!AdminSyncPolicy.CanApply(scene, ready, idle, capturedActivity, TransportPatches.ActivityVersion,
                Math.Min(now - TransportPatches.LastActivityAt, now - sceneChangedAt)))
        {
            Queue(snapshot.Revision, "waiting_safe_point", scene, "waiting_for_idle_home");
            return;
        }
        try
        {
            using var document = System.Text.Json.JsonDocument.Parse(snapshot.RecordsJson);
            if (document.RootElement.EnumerateObject().Any(property => !recordNames.Contains(property.Name)))
                throw new InvalidDataException("Administrator records include a collection unsupported by this client.");
            var settings = new JsonSerializerSettings
            {
                TypeNameHandling = TypeNameHandling.None,
                MetadataPropertyHandling = MetadataPropertyHandling.Ignore
            };
            var parsed = JsonConvert.DeserializeObject(snapshot.RecordsJson, Il2CppType.Of<RecordEntity>(), settings);
            var records = parsed?.Cast<RecordEntity>() ?? throw new InvalidDataException("Administrator records could not be deserialized.");
            user!.Apply(records);
            CharacterLevelPatches.VerifyApplied();
            appliedRevision = snapshot.Revision;
            Queue(snapshot.Revision, "applied", scene, "native_data_applied_page_refresh_may_be_required");
        }
        catch (Exception error)
        {
            rejectedRevision = snapshot.Revision;
            Queue(snapshot.Revision, "failed", scene, "native_apply_" + error.GetType().Name);
        }
    }

    private void CompleteAcknowledgement(long now)
    {
        if (acknowledgement is not { IsCompleted: true }) return;
        try
        {
            acknowledgement.GetAwaiter().GetResult();
            if (queuedAck == sentAck) queuedAck = null;
            nextAckAt = now + 1000;
        }
        catch (Exception error)
        {
            nextAckAt = now + 5000;
            Describe("ack_" + error.GetType().Name);
        }
        acknowledgement = null;
    }

    private void Queue(long revision, string status, string scene, string reason)
    {
        var message = new AdminSyncAck(clientId, revision, status, scene.Length <= 128 ? scene : "unknown", reason);
        if (message != sentAck || queuedAck is not null) queuedAck = message;
        Describe(status + ":" + revision + ":" + reason);
    }

    private void Describe(string value)
    {
        if (diagnostic == value) return;
        diagnostic = value;
        Plugin.Info("Administrator synchronization: " + value);
    }

    public void OnDestroy()
    {
        cancellation.Cancel();
        cancellation.Dispose();
    }
}
