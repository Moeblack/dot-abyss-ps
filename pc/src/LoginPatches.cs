using Absf;
using Absf.Api;
using Absf.DmmPlatforms;
using Cysharp.Threading.Tasks;
using Il2CppInterop.Runtime;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using Project.Api;
using UnityEngine;
using System.Runtime.CompilerServices;
using GameCancellationToken = Il2CppSystem.Threading.CancellationToken;
using LoginTuple = Il2CppSystem.ValueTuple<long, string>;

namespace DotAbyss.Local.Pc;

internal static class LoginPatches
{
    internal static void Install(Plugin.AddHook add)
    {
        add(typeof(DmmSdk), "Initialize", new[] { typeof(GameCancellationToken), typeof(Il2CppSystem.Action), typeof(Il2CppSystem.Action) }, typeof(LoginPatches), nameof(Initialize));
        add(typeof(DmmSdkUnityIap), "Initialize", new[] { typeof(DmmMarketingIdData), typeof(GameCancellationToken), typeof(Il2CppSystem.Action), typeof(Il2CppSystem.Action) }, typeof(LoginPatches), nameof(InitializeIap));
        VerifyNativeType<GameCancellationToken>();
        VerifyNativeType<Il2CppReferenceArray<TermsAgreeRequestEntity.TermsAgree>>();
        VerifyNativeType<LoginTuple>();
        VerifyNativeType<UniTask<LoginTuple>>();
        VerifyNativeType<UniTask<string>>();
        add(typeof(Project.Login), "LoginDmmGamePlayerAsync", new[] { typeof(Il2CppReferenceArray<TermsAgreeRequestEntity.TermsAgree>), typeof(GameCancellationToken) }, typeof(LoginPatches), nameof(Login));
        add(typeof(Project.Login), "FetchRawUuidFollowingPlatformAsync", new[] { typeof(GameCancellationToken) }, typeof(LoginPatches), nameof(LocalUuid));
    }

    private static void VerifyNativeType<T>()
    {
        var name = typeof(T).FullName ?? typeof(T).Name;
        Plugin.Info("Native type initialization: " + name);
        RuntimeHelpers.RunClassConstructor(typeof(T).TypeHandle);
        var available = Il2CppClassPointerStore<T>.NativeClassPtr != IntPtr.Zero;
        Plugin.Info($"Native type initialized: {name}, class_available={available}");
        if (!available) throw new TypeLoadException("A required local login type has no native class.");
    }

    internal static bool Initialize(GameCancellationToken __0, Il2CppSystem.Action? __1, Il2CppSystem.Action? __2, ref UniTask __result)
        => InitializeCore("DmmSdk.Initialize", __0, __1, __2, ref __result);

    internal static bool InitializeIap(GameCancellationToken __1, Il2CppSystem.Action? __2, Il2CppSystem.Action? __3, ref UniTask __result)
        => InitializeCore("DmmSdkUnityIap.Initialize", __1, __2, __3, ref __result);

    private static bool InitializeCore(string scope, GameCancellationToken cancellation, Il2CppSystem.Action? onSuccess,
        Il2CppSystem.Action? onError, ref UniTask result)
    {
        try
        {
            NativeCallDiagnostics.CheckCancellation(scope, cancellation, onSuccess, onError);
            Plugin.Session.Get();
            Plugin.Session.RegisterRuntime(RuntimeConfig.GetAppVersionCode(), RuntimeConfig.GetBundleVersion(), Application.version);
            result = UniTask.CompletedTask;
            onSuccess?.Invoke();
            Plugin.Report("local_platform_initialized", new { application_version = Application.version });
        }
        catch (Exception error) { Plugin.Stop(error); }
        return false;
    }

    internal static bool LocalUuid(GameCancellationToken __0, ref UniTask<string> __result)
    {
        try
        {
            __0.ThrowIfCancellationRequested();
            __result = new UniTask<string>(Plugin.Session.Get().Uuid);
        }
        catch (Exception error) { Plugin.Stop(error); }
        return false;
    }

    internal static bool Login(GameCancellationToken __1, ref UniTask<LoginTuple> __result)
    {
        var stage = "prepare_login";
        try
        {
            __1.ThrowIfCancellationRequested();
            var session = Plugin.Session.Get();
            var login = Plugin.Session.AuthenticateGameLogin();
            __1.ThrowIfCancellationRequested();
            var manager = Engine.Get<ApiManager>() ?? throw new InvalidOperationException("Native API manager is unavailable.");
            Project.Login._rawUuidCache = login.Uuid;
            Project.Login._isCachedRawUuid = true;
            Project.Login.SaveLocalUuidIfChanged(login.Uuid);
            Project.Login.SetEncryptUuidToken(login.Uuid);
            manager.SetSession(session.Token);
            manager.SetHeader("api-token", session.ApiToken);
            stage = "construct_tuple";
            var tuple = new LoginTuple(login.UserId, login.ServerTime);
            stage = "verify_tuple";
            var tupleUserMatches = tuple.Item1 == login.UserId;
            var tupleTimeMatches = tuple.Item2 == login.ServerTime;
            Plugin.Info($"Native login tuple: user_matches={tupleUserMatches}, time_matches={tupleTimeMatches}");
            stage = "construct_task";
            var task = new UniTask<LoginTuple>(source: null!, token: 0);
            stage = "assign_task_result";
            task.result = tuple;
            stage = "verify_task_result";
            var taskResult = task.result;
            var resultPresent = taskResult is not null;
            var taskUserMatches = resultPresent && taskResult!.Item1 == login.UserId;
            var taskTimeMatches = resultPresent && taskResult!.Item2 == login.ServerTime;
            var sourceNull = task.source is null;
            var tokenZero = task.token == 0;
            Plugin.Info($"Native login task: result_present={resultPresent}, user_matches={taskUserMatches}, time_matches={taskTimeMatches}, source_null={sourceNull}, token_zero={tokenZero}");
            if (!taskUserMatches || !taskTimeMatches || !sourceNull || !tokenZero)
                throw new InvalidDataException("The native login task did not preserve its result.");
            stage = "return_task";
            __result = task;
            Plugin.Report("native_login_returned", new { local_user_id = login.UserId, home_ready = false });
        }
        catch (Exception error)
        {
            Plugin.Info("Native login stopped at " + stage);
            Plugin.Stop(error);
        }
        return false;
    }
}
