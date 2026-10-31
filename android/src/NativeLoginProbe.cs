using Il2CppAbsf;
using Il2CppAbsf.Api;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using Il2CppProject.Api;
using System.Text.Json;
using GameCancellationToken = Il2CppSystem.Threading.CancellationToken;
using LoginTuple = Il2CppSystem.ValueTuple<long, string>;

namespace DotAbyss.Local;

internal static class NativeLoginProbe
{
    private static int attempted;
    [ThreadStatic] private static bool running;
    [ThreadStatic] private static GameLoginData? expected;
    [ThreadStatic] private static LoginTuple? sourceTuple;
    [ThreadStatic] private static nint sourceStringPointer;
    [ThreadStatic] private static Dictionary<string, LoginTupleShape>? tupleStages;

    internal static bool IsRunning => running;

    internal static void RecordExpectedResult(GameLoginData result)
    {
        if (running) expected = result;
    }

    internal static void RecordSourceTuple(LoginTuple tuple, nint stringPointer, LoginTupleShape shape)
    {
        if (!running) return;
        sourceTuple = tuple;
        sourceStringPointer = stringPointer;
        RecordShape("tuple_constructed", shape);
    }

    internal static LoginTupleShape RecordTuple(string stage, LoginTuple? tuple)
    {
        if (!running || expected is null) throw new InvalidOperationException("The tuple probe has no expected login result.");
        var shape = NativeTupleInspection.Compare(tuple, expected, sourceStringPointer);
        RecordShape(stage, shape);
        return shape;
    }

    private static void RecordShape(string stage, LoginTupleShape shape)
    {
        tupleStages![stage] = shape;
        LocalMod.Log($"Standalone login tuple stage={stage}: {JsonSerializer.Serialize(shape)}");
    }

    internal static void RunOnce(GameCancellationToken cancellation)
    {
        if (!LocalMod.Active || LocalMod.Options.ObserveOnly || !LocalMod.Options.StandaloneNativeLoginProbe
            || Interlocked.CompareExchange(ref attempted, 1, 0) != 0) return;
        var stage = "read_local_session";
        try
        {
            running = true;
            expected = null;
            tupleStages = new Dictionary<string, LoginTupleShape>(StringComparer.Ordinal);
            LocalMod.Log("Starting standalone native login probe; the normal resource and game login flow is not being advanced.");
            var session = LocalMod.RequireSession();
            var preferences = Engine.Get<PrefsDataStore>()
                ?? throw new InvalidOperationException("The native preferences service is not initialized.");
            var previousUserId = preferences.GetUserID();
            var before = LocalPatches.LoginPrefixInvocations;
            stage = "allocate_empty_terms";
            LocalMod.Log($"Standalone native login probe stage={stage}");
            var terms = new Il2CppReferenceArray<TermsAgreeRequestEntity.TermsAgree>(0L);
            stage = "validate_empty_terms";
            LocalMod.Log($"Standalone native login probe stage={stage}, terms_length={terms.Length}");
            if (terms.Length != 0) throw new InvalidOperationException("The probe terms array was not empty.");
            stage = "invoke_native_login";
            LocalMod.Log($"Standalone native login probe stage={stage}, prefix_before={before}");
            var task = Il2CppProject.Login.LoginDmmAndroidAsync(terms, cancellation);
            stage = "validate_native_call_result";
            var after = LocalPatches.LoginPrefixInvocations;
            LocalMod.Log($"Standalone native login probe stage={stage}, prefix_after={after}, task_wrapper_present={task is not null}");
            if (after <= before)
                throw new InvalidOperationException("The native login prefix was not observed.");
            if (task is null) throw new InvalidOperationException("The native login call returned a null task wrapper.");
            RecordTuple("returned_task_result", task.result);
            stage = "create_owned_awaiter";
            LocalMod.Log($"Standalone native login probe stage={stage}");
            var awaiter = task.GetAwaiter();
            if (awaiter is null) throw new InvalidOperationException("The native task returned a null awaiter wrapper.");
            RecordTuple("awaiter_task_result", awaiter.task.result);
            stage = "check_owned_task_status";
            LocalMod.Log($"Standalone native login probe stage={stage}");
            if (!awaiter.IsCompleted) throw new InvalidOperationException("The probe-owned task is unexpectedly pending.");
            stage = "read_owned_task_result";
            LocalMod.Log($"Standalone native login probe stage={stage}");
            var result = awaiter.GetResult();
            if (result is null) throw new InvalidOperationException("The native awaiter returned a null tuple wrapper.");
            stage = "verify_identity_state";
            LocalMod.Log($"Standalone native login probe stage={stage}");
            _ = expected ?? throw new InvalidOperationException("The local identity helper did not complete.");
            var manager = Engine.Get<ApiManager>()
                ?? throw new InvalidOperationException("The native API manager is not initialized.");
            var finalTuple = RecordTuple("awaiter_result", result);
            var tupleMatches = finalTuple.Matches && tupleStages!.Where(pair => pair.Key != "generic_constructor_candidate")
                .All(pair => pair.Value.Matches);
            var uuidCacheMatches = Il2CppProject.Login._isCachedRawUuid && Il2CppProject.Login._rawUuidCache == session.Uuid;
            var uuidPreferencesMatch = preferences.GetUUID() == session.Uuid;
            var encryptedTokenPopulated = !string.IsNullOrEmpty(Il2CppProject.Login.EncryptToken);
            var nativeSessionMatches = manager._crypt?._sessionId == session.Token;
            var headers = manager._headers;
            var apiTokenMatches = headers is not null && headers.ContainsKey("api-token") && headers["api-token"] == session.ApiToken;
            var outerUserIdUnchanged = preferences.GetUserID() == previousUserId;
            var passed = tupleMatches && uuidCacheMatches && uuidPreferencesMatch && encryptedTokenPopulated
                && nativeSessionMatches && apiTokenMatches && outerUserIdUnchanged;
            LocalMod.Report("standalone_native_login_probe", new
            {
                passed,
                prefix_observed = true,
                tuple_matches = tupleMatches,
                long_matches = finalTuple.LongMatches,
                long_is_zero = finalTuple.LongIsZero,
                string_matches = finalTuple.StringMatches,
                string_is_null = finalTuple.StringIsNull,
                string_length = finalTuple.StringLength,
                string_parses_as_datetime = finalTuple.StringParsesAsDateTime,
                string_pointer_matches = finalTuple.StringPointerMatches,
                string_inspected = finalTuple.StringInspected,
                tuple_stages = tupleStages,
                uuid_cache_matches = uuidCacheMatches,
                uuid_preferences_match = uuidPreferencesMatch,
                encrypted_token_populated = encryptedTokenPopulated,
                native_session_matches = nativeSessionMatches,
                api_token_matches = apiTokenMatches,
                outer_user_id_unchanged = outerUserIdUnchanged,
                normal_game_login = false,
                resource_flow_bypassed = false
            });
            LocalMod.Log($"Standalone native login probe completed: passed={passed}; this is not a normal game login or a resource-loading result.");
        }
        catch (Exception exception)
        {
            LocalMod.Report("standalone_native_login_probe", new
            {
                passed = false,
                stage,
                error_type = exception.GetType().Name,
                method_frames = ProtocolLog.ExceptionFrames(exception),
                tuple_stages = tupleStages,
                normal_game_login = false,
                resource_flow_bypassed = false
            });
            LocalMod.Log($"Standalone native login probe failed at {stage} ({exception.GetType().Name}).");
            LocalMod.Log($"Standalone native login probe method frames: {ProtocolLog.ExceptionFrames(exception)}");
        }
        finally
        {
            running = false;
            expected = null;
            GC.KeepAlive(sourceTuple);
            sourceTuple = null;
            sourceStringPointer = 0;
            tupleStages = null;
            LocalMod.Log("Stopping the standalone diagnostic process; disable StandaloneNativeLoginProbe before the next normal start.");
            // Do not remove a native detour while its SDK callback is still executing.
            Environment.Exit(0);
        }
    }
}
