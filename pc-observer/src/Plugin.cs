using System.Reflection;
using System.Text.Json;
using Absf.Api;
using BepInEx;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;
using UnityEngine;

namespace DotAbyss.Observer.Pc;

[BepInPlugin(Id, "Dot Abyss PC Observer", ObserverVersion)]
[BepInIncompatibility(ClientIdentity.LocalPluginId)]
public sealed class Plugin : BasePlugin
{
    internal const string Id = "observer.dotabyss.pc";
    internal const string ObserverVersion = "0.1.0";
    private static Plugin? current;
    private readonly Harmony harmony = new(Id);
    private CaptureSink? sink;
    internal static bool Active { get; private set; }
    internal static bool CanCapture => Active && current?.sink?.Snapshot is { Faulted: false, Completed: false, OutputLimited: false };
    internal static bool CaptureRequests { get; private set; }
    internal static CaptureSink Sink => current?.sink ?? throw new InvalidOperationException();

    public override void Load()
    {
        current = this;
        try
        {
            if (Directory.EnumerateFiles(Paths.PluginPath, "DotAbyss.Local.Pc.dll", SearchOption.AllDirectories).Any()
                || AppDomain.CurrentDomain.GetAssemblies().Any(a => a.GetName().Name == "DotAbyss.Local.Pc"))
                throw new InvalidOperationException();
            var clientRoot = ClientIdentity.Verify();
            var configPath = Path.Combine(Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location)!, "observer.json");
            if (new FileInfo(configPath).Length > 16384) throw new InvalidDataException();
            using var config = JsonDocument.Parse(File.ReadAllBytes(configPath));
            var outputRoot = config.RootElement.GetProperty("outputRoot").GetString();
            if (string.IsNullOrWhiteSpace(outputRoot) || !Path.IsPathFullyQualified(outputRoot)
                || ClientIdentity.IsWithin(outputRoot, clientRoot)
                || ClientIdentity.IsWithin(outputRoot, Application.persistentDataPath))
                throw new InvalidDataException();
            CaptureRequests = config.RootElement.TryGetProperty("captureRequests", out var captureRequests) && captureRequests.GetBoolean();
            sink = new CaptureSink(Path.GetFullPath(outputRoot), new CaptureOptions
            {
                Identity = new CaptureIdentity(ClientIdentity.ApplicationVersion, ClientIdentity.MetadataHash,
                    ClientIdentity.NativeHash, ClientIdentity.UnityPlayerHash, ObserverVersion)
            });
            Add("Response", typeof(ApiResponseData), nameof(CaptureHooks.Response));
            Add("Result", typeof(HttpResult), nameof(CaptureHooks.Result));
            AppDomain.CurrentDomain.ProcessExit += OnProcessExit;
            Active = true;
            Log.LogInfo("Passive business-response observer ready; request snapshots are taken at response time.");
        }
        catch (Exception error) { Disable(error); }
    }

    private void Add(string method, Type argument, string hook)
    {
        var target = AccessTools.Method(typeof(ApiManager), method, new[] { argument }) ?? throw new MissingMethodException();
        var prefix = AccessTools.Method(typeof(CaptureHooks), hook) ?? throw new MissingMethodException();
        harmony.Patch(target, prefix: new HarmonyMethod(prefix));
    }

    internal static void Disable(Exception error)
    {
        Active = false;
        CaptureHooks.Clear();
        try { current?.Log.LogWarning("Observer disabled: " + error.GetType().Name); }
        catch { }
        try
        {
            var completion = current?.sink?.CompleteAsync();
            if (completion is not null)
                _ = completion.ContinueWith(task => { _ = task.Exception; }, TaskContinuationOptions.OnlyOnFaulted);
        }
        catch { }
    }

    private void OnProcessExit(object? sender, EventArgs args) => Complete();

    private void Complete()
    {
        Active = false;
        CaptureHooks.Clear();
        try { sink?.CompleteAsync().Wait(TimeSpan.FromSeconds(3)); }
        catch (Exception error)
        {
            try { Log.LogWarning("Observer flush stopped: " + error.GetType().Name); }
            catch { }
        }
    }

    public override bool Unload()
    {
        AppDomain.CurrentDomain.ProcessExit -= OnProcessExit;
        Complete();
        try { harmony.UnpatchSelf(); }
        catch (Exception error) { Disable(error); }
        return true;
    }
}
