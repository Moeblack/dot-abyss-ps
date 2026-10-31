using System.Reflection;
using System.Text.Json;
using HarmonyLib;
using Il2CppAbsf.Api;
using Il2CppProject.Api;
using MelonLoader;
using MelonLoader.Utils;

[assembly: MelonInfo(typeof(DotAbyss.Local.LocalMod), "DotAbyss Local Adapter", "0.1.0", "Local Tools")]

namespace DotAbyss.Local;

public sealed class LocalMod : MelonMod
{
    internal static LocalOptions Options { get; private set; } = new();
    internal static Uri Origin { get; private set; } = new("http://127.0.0.1:18081");
    internal static LocalSession? Session { get; private set; }
    internal static bool Active { get; private set; }
    private string? originalVersionFlag;
    private bool versionFlagCaptured;
    private NativeHookPolicy? nativeHookPolicy;
    private static LocalMod? current;
    private static string? captureDirectory;
    private static int captureSequence;

    public override void OnInitializeMelon()
    {
        current = this;
        try { Initialize(); }
        catch
        {
            Cleanup();
            if (Options.ObserveOnly)
            {
                Log("Observation hooks were removed after initialization failed; the original game flow is unchanged.");
                return;
            }
            try { UnityEngine.Application.Quit(); }
            catch (Exception exception) { Log($"Application.Quit unavailable ({exception.GetType().Name}); the process may still be running and needs an external stop."); }
            throw;
        }
    }

    private void Initialize()
    {
        var directory = Path.Combine(MelonEnvironment.UserDataDirectory, "DotAbyssLocal");
        Directory.CreateDirectory(directory);
        captureDirectory = Path.Combine(directory, "responses");
        var path = Path.Combine(directory, "config.json");
        if (!File.Exists(path))
            File.WriteAllText(path, JsonSerializer.Serialize(Options, new JsonSerializerOptions { WriteIndented = true }));
        Options = JsonSerializer.Deserialize<LocalOptions>(File.ReadAllText(path))
            ?? throw new InvalidDataException("Local adapter configuration is empty.");
        if (!Options.Enabled)
        {
            Log("Disabled. Enable UserData/DotAbyssLocal/config.json and restart to use the local server.");
            return;
        }
        NativeHookPolicy.LogMappings();
        NativeDiagnostics.ReadBootstrapConfiguration();
        if (Options.AllowFarTrampolines)
        {
            nativeHookPolicy = new NativeHookPolicy();
            nativeHookPolicy.AllowFarFallback();
        }
        if (Options.ObserveOnly)
        {
            InstallObservationPatches();
            Active = true;
            Log("ObserveOnly enabled: original URLs, authentication, crypto and version handling remain unchanged.");
            return;
        }
        Origin = Options.Validate();
        Session = new LocalSession(Origin, Options);
        InstallPatches();
        if (Options.IgnoreVersionChecks)
        {
            originalVersionFlag = ApiManager.SkipCheckVersion;
            versionFlagCaptured = true;
            ApiManager.SkipCheckVersion = "1";
        }
        Active = true;
        Log($"Enabled for {Origin.GetLeftPart(UriPartial.Authority)}. Native Android execution remains unverified.");
        VerifyLocalTransport();
    }

    private void VerifyLocalTransport()
    {
        var hookActive = LocalPatches.VerifyApiUrlHook();
        var transportReady = false;
        try
        {
            RequireSession();
            var appCode = Il2CppAbsf.RuntimeConfig.GetAppVersionCode();
            var bundleVersion = Il2CppAbsf.RuntimeConfig.GetBundleVersion();
            var applicationVersion = UnityEngine.Application.version;
            Session!.RegisterRuntime(appCode, bundleVersion, applicationVersion);
            Report("local_transport_ready", new
            {
                native_hook_active = hookActive,
                app_version_code = appCode,
                bundle_version = bundleVersion,
                application_version = applicationVersion,
                version_source = "device_RuntimeConfig_and_Application"
            });
            if (!hookActive) Report("native_hook_inactive", new { method = "RuntimeConfig.GetApiUrl", reason = "prefix_not_observed_or_url_mismatch" });
            transportReady = true;
            Log("Independent local HTTP session and runtime registration succeeded; this does not validate the native hooks.");
        }
        catch (Exception exception)
        {
            Log($"Local HTTP verification failed ({exception.GetType().Name}); the local game flow is not ready.");
        }
        if (!hookActive || !transportReady)
        {
            Log("Local startup verification failed; stopping this process to prevent continuation into the original platform login.");
            Cleanup();
            Environment.Exit(0);
        }
    }

    private void InstallObservationPatches()
    {
        Add(typeof(UnityEngine.Networking.UnityWebRequest), "SendWebRequest", [], nameof(ObservationPatches.SendRequest),
            patchOwner: typeof(ObservationPatches));
        Add(typeof(ApiManager), "Response", [typeof(ApiResponseData)], nameof(ObservationPatches.Response),
            patchOwner: typeof(ObservationPatches));
        Add(typeof(RootLoginRequestEntity), ".ctor", [], nameof(ObservationPatches.RootLogin),
            postfix: true, patchOwner: typeof(ObservationPatches));
        Type[] loginParameters =
        [
            typeof(Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppReferenceArray<Il2CppProject.Api.TermsAgreeRequestEntity.TermsAgree>),
            typeof(Il2CppSystem.Threading.CancellationToken)
        ];
        Add(typeof(Il2CppProject.Login), "LoginDmmAndroidAsync", loginParameters, nameof(ObservationPatches.LoginEntered),
            patchOwner: typeof(ObservationPatches));
        Add(typeof(Il2CppProject.Login), "LoginDmmAndroidAsync", loginParameters, nameof(ObservationPatches.LoginReturned),
            postfix: true, patchOwner: typeof(ObservationPatches));
        ObservationPatches.DescribeStateLayout();
    }

    private void InstallPatches()
    {
        Add(typeof(Il2CppAbsf.RuntimeConfig), "GetApiUrl", [], nameof(LocalPatches.ApiUrl));
        Add(typeof(ApiManager), "RefreshConfig", [typeof(string)], nameof(LocalPatches.RefreshApi));
        Add(typeof(ApiCrypt), "EncryptBody", [typeof(Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<byte>)], nameof(LocalPatches.PassBytes));
        Add(typeof(ApiCrypt), "DecryptBody", [typeof(Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<byte>)], nameof(LocalPatches.PassBytes));
        Add(typeof(ApiCrypt), "EncryptLaravel", [typeof(string)], nameof(LocalPatches.PassString));
        Add(typeof(ApiCrypt), "DecryptLaravel", [typeof(string)], nameof(LocalPatches.PassString));
        Add(typeof(ApiManager), "Response", [typeof(ApiResponseData)], nameof(LocalPatches.Response));
        Add(typeof(UnityEngine.Networking.UnityWebRequest), "SendWebRequest", [], nameof(LocalPatches.SendRequest));
        Add(typeof(Il2CppAbsf.DmmPlatforms.DmmSdk), "Initialize",
            [typeof(Il2CppSystem.Threading.CancellationToken), typeof(Il2CppSystem.Action), typeof(Il2CppSystem.Action)], nameof(LocalPatches.InitializePlatform));
        Add(typeof(Il2CppAbsf.DmmPlatforms.DmmSdkUnityIap), "Initialize",
            [typeof(Il2CppAbsf.DmmPlatforms.DmmMarketingIdData), typeof(Il2CppSystem.Threading.CancellationToken),
                typeof(Il2CppSystem.Action), typeof(Il2CppSystem.Action)], nameof(LocalPatches.InitializePlatformWithMarketingData));
        Add(typeof(Il2CppProject.Login), "LoginDmmAndroidAsync",
            [typeof(Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppReferenceArray<Il2CppProject.Api.TermsAgreeRequestEntity.TermsAgree>),
                typeof(Il2CppSystem.Threading.CancellationToken)], nameof(LocalPatches.LoginAndroid));
        Add(typeof(Il2CppProject.Login), "FetchRawUuidFollowingPlatformAsync",
            [typeof(Il2CppSystem.Threading.CancellationToken)], nameof(LocalPatches.LocalUuid));
        Add(typeof(Il2CppProject.Api.RootLoginRequestEntity), ".ctor", [], nameof(LocalPatches.ObserveRootLogin), postfix: true);
    }

    private void Add(Type type, string method, Type[] parameters, string patch, bool postfix = false, Type? patchOwner = null)
    {
        MethodBase? target = method == ".ctor" ? AccessTools.Constructor(type, parameters) : AccessTools.Method(type, method, parameters);
        if (target is null) throw new MissingMethodException(type.FullName, method);
        var callback = new HarmonyMethod(patchOwner ?? typeof(LocalPatches), patch);
        HarmonyInstance.Patch(target, prefix: postfix ? null : callback, postfix: postfix ? callback : null);
        Log($"Hook installed: {type.FullName}.{method}");
    }

    public override void OnDeinitializeMelon()
        => Cleanup();

    public override void OnSceneWasLoaded(int buildIndex, string sceneName)
    {
        if (Options.Enabled && Options.ObserveOnly) NativeDiagnostics.ReadBootstrapConfiguration();
    }

    private void Cleanup()
    {
        Active = false;
        try { HarmonyInstance.UnpatchSelf(); }
        catch (Exception exception) { Log($"Hook removal failed ({exception.GetType().Name}); restart is required."); }
        try { RestoreVersionFlag(); }
        catch (Exception exception) { Log($"Version flag restoration failed ({exception.GetType().Name}); restart is required."); }
        try { nativeHookPolicy?.Dispose(); }
        catch (Exception exception) { Log($"Native hook policy restoration failed ({exception.GetType().Name}); restart is required."); }
        nativeHookPolicy = null;
        Session?.Dispose();
        Session = null;
    }

    private void RestoreVersionFlag()
    {
        if (!versionFlagCaptured) return;
        ApiManager.SkipCheckVersion = originalVersionFlag!;
        versionFlagCaptured = false;
    }

    internal static void Log(string message) => current?.LoggerInstance.Msg(message);

    internal static void Report(string eventName, object details)
    {
        try { Session?.Report(eventName, details); }
        catch (Exception exception) { Log($"Diagnostic report unavailable ({exception.GetType().Name}); event={eventName}"); }
    }

    internal static void Capture(string? body)
    {
        if (!Options.CaptureRedactedResponses || captureDirectory is null) return;
        var sanitized = ProtocolLog.RedactResponse(body);
        if (sanitized is null) return;
        try
        {
            Directory.CreateDirectory(captureDirectory);
            var file = $"response-{DateTime.UtcNow:yyyyMMdd-HHmmssfff}-{Interlocked.Increment(ref captureSequence)}.json";
            File.WriteAllText(Path.Combine(captureDirectory, file), sanitized);
            Log($"Redacted response saved: {file}");
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            Log($"Response capture failed ({exception.GetType().Name}).");
        }
    }

    internal static SessionData RequireSession()
    {
        try { return Session?.Get() ?? throw new InvalidOperationException("Local adapter is not initialized."); }
        catch (Exception exception)
        {
            Log($"Local session unavailable ({exception.GetType().Name}). Check /healthz and adb reverse; no official login fallback is attempted.");
            throw;
        }
    }
}
