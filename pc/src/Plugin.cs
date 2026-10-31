using System.Reflection;
using Absf;
using Absf.Api;
using BepInEx;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;
using UnityEngine;
using UnityEngine.Networking;

namespace DotAbyss.Local.Pc;

[BepInPlugin(Id, "Dot Abyss Local PC", "0.1.0")]
public sealed class Plugin : BasePlugin
{
    internal const string Id = "local.dotabyss.pc";
    private static Plugin? current;
    private readonly Harmony harmony = new(Id);
    private LoopbackProxy? loopbackProxy;
    internal static LocalOptions Options { get; private set; } = null!;
    internal static Uri Origin { get; private set; } = null!;
    internal static LocalSession Session { get; private set; } = null!;
    internal static string ClientRoot { get; private set; } = "";
    internal static bool Active { get; private set; }
    private static string loadStage = "created";

    public override void Load()
    {
        current = this;
        try
        {
            Stage("read_config");
            Options = LocalOptions.Load(Path.Combine(Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location)!, "pc-local.json"));
            Stage("validate_config");
            Origin = Options.Validate();
            Info("Process NO_PROXY: " + System.Text.Json.JsonSerializer.Serialize(TransportPolicy.ProxyEnvironmentStatus(Environment.GetEnvironmentVariable("NO_PROXY"))));
            Stage("unity.dataPath");
            var dataPath = Application.dataPath;
            ClientRoot = Path.GetDirectoryName(dataPath)!;
            VerifyIsolation();
            Stage("client_fingerprints");
            ClientIdentity.VerifyFiles(dataPath);
            Stage("local_proxy_adapter");
            loopbackProxy = LoopbackProxy.Install(ClientRoot);
            Stage("local_service_preflight");
            Session = new LocalSession(Origin, Options);
            Session.VerifyServer();
            Stage("install_hooks");
            InstallPatches();
            Active = true;
            Stage("native_api_getter_probe");
            if (!TransportPatches.VerifyApiHook()) throw new InvalidOperationException("The native API getter did not enter its local hook.");
            Stage("local_session");
            Session.Get();
            Stage("scene_observer");
            AddComponent<LocalObserver>();
            AddComponent<AdminSyncBehaviour>();
            Stage("ready");
            Report("adapter_ready", new { application_version = Application.version, metadata_sha256 = ClientIdentity.MetadataHash, isolated_profile = true });
            Info("PC adapter ready; native login and home remain to be observed.");
        }
        catch (Exception error) { Stop(error); }
    }

    private static void VerifyIsolation()
    {
        Stage("unity.version");
        var version = Application.version;
        Stage("unity.productName");
        var product = Application.productName;
        Stage("unity.persistentDataPath");
        var persistentPath = Application.persistentDataPath;
        Stage("verify_isolation");
        if (version != ClientIdentity.ApplicationVersion || product != Options.ExpectedProduct
            || !Path.GetFullPath(persistentPath).Equals(Path.GetFullPath(Options.ExpectedPersistentPath), StringComparison.OrdinalIgnoreCase)
            || !File.Exists(Path.Combine(ClientRoot, "local-client.json")))
            throw new InvalidDataException("The prepared client identity or persistent path is not isolated.");
    }

    private void InstallPatches()
    {
        Add(typeof(UnityWebRequest), "SendWebRequest", Type.EmptyTypes, typeof(TransportPatches), nameof(TransportPatches.Send));
        TelemetryPatches.Install(Add);
        Add(typeof(RuntimeConfig), "GetApiUrl", Type.EmptyTypes, typeof(TransportPatches), nameof(TransportPatches.ApiUrl));
        Add(typeof(ApiManager), "RefreshConfig", new[] { typeof(string) }, typeof(TransportPatches), nameof(TransportPatches.RefreshApi));
        Add(typeof(ApiCrypt), "EncryptBody", new[] { typeof(Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<byte>) }, typeof(TransportPatches), nameof(TransportPatches.EncryptBody));
        Add(typeof(ApiCrypt), "DecryptLaravel", new[] { typeof(string) }, typeof(TransportPatches), nameof(TransportPatches.DecryptSession));
        Add(typeof(ApiManager), "Response", new[] { typeof(ApiResponseData) }, typeof(TransportPatches), nameof(TransportPatches.Response));
        AssetPatches.Install(Add);
        TermsPatches.Install(Add);
        LoginPatches.Install(Add);
        PaymentPatches.Install(Add);
        CharacterLevelPatches.Install(Add);
        IdleExplorationLifecyclePatches.Install(Add);
        AdminSyncNativeState.Install(Add);
    }

    internal delegate void AddHook(Type owner, string method, Type[] parameters, Type patchOwner, string patch, bool postfix = false);

    private void Add(Type owner, string method, Type[] parameters, Type patchOwner, string patch, bool postfix = false)
    {
        var target = AccessTools.Method(owner, method, parameters) ?? throw new MissingMethodException(owner.FullName, method);
        var callback = new HarmonyMethod(patchOwner, patch);
        harmony.Patch(target, prefix: postfix ? null : callback, postfix: postfix ? callback : null);
        Info($"Hook installed: {owner.FullName}.{method}");
    }

    internal static void Info(string message) => current?.Log.LogInfo(message);

    private static void Stage(string name)
    {
        loadStage = name;
        Info("Load stage: " + name);
    }

    internal static void Report(string name, object details)
    {
        try { Session.Report(name, details); }
        catch (Exception error) { Info($"Diagnostic {name} unavailable ({error.GetType().Name})."); }
    }

    internal static void Stop(Exception error)
    {
        Active = false;
        current?.Log.LogError($"Local PC startup stopped at {loadStage} ({error.GetType().Name}).");
        foreach (var frame in ExceptionFrames.Describe(error, loadStage == "install_hooks")) current?.Log.LogError(frame);
        Environment.Exit(70);
    }

    public override bool Unload()
    {
        if (Active) throw new InvalidOperationException("Restart the local client to unload its transport adapter.");
        loopbackProxy?.Dispose();
        Session?.Dispose();
        return base.Unload();
    }
}
