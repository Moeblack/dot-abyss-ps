using BepInEx;
using BepInEx.Unity.IL2CPP;
using Absf.Api;
using DotAbyss.Observer.Pc;
using HarmonyLib;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using System.Collections.Concurrent;
using System.Text.Json;
using UnityEngine;

[BepInPlugin("observer.dotabyss.nativeprobe", "Dot Abyss Observer Native Probe", "0.1.0")]
public sealed class ProbePlugin : BasePlugin
{
    private sealed record Pending(long Status, RequestIdentity Request);
    private static readonly ConcurrentDictionary<(IntPtr Manager, IntPtr Task), Pending> pending = new();
    private static ProbePlugin? current;
    private static bool active;
    private readonly Harmony harmony = new("observer.dotabyss.nativeprobe");

    public override void Load()
    {
        current = this;
        try
        {
            if (Application.productName != "DotAbyssLocalPlayer" || Application.version != "1.15.0")
                throw new InvalidOperationException();
            var samples = new[]
            {
                "82a8636f6e74656e747382a56974656d739301c0c3a56c6162656ca570726f6265a96170695f746f6b656eb873796e7468657469632d6f627365727665722d70726f6265",
                "81a8636f6e74656e747384a26964cf0020000000000001a86e65676174697665f9a5656d70747990a576616c7565cb3ff4000000000000"
            };
            using var first = Decode(samples[0]);
            using var second = Decode(samples[1]);
            var firstContents = first.RootElement.GetProperty("contents");
            var secondContents = second.RootElement.GetProperty("contents");
            var passed = firstContents.GetProperty("items")[1].ValueKind == JsonValueKind.Null
                && firstContents.GetProperty("items")[2].GetBoolean()
                && firstContents.GetProperty("label").GetString() == "probe"
                && secondContents.GetProperty("id").GetInt64() == 9007199254740993L
                && secondContents.GetProperty("negative").GetInt32() == -7
                && secondContents.GetProperty("empty").GetArrayLength() == 0
                && secondContents.GetProperty("value").GetDouble() == 1.25;
            Log.LogInfo("Observer native MessagePack probe: samples=2, all_checks_passed=" + passed);
            if (!passed) return;
            Add("Response", typeof(ApiResponseData), nameof(Response));
            Add("Result", typeof(HttpResult), nameof(Result));
            active = true;
            Log.LogInfo("Observer native request probe ready for /api/user.");
        }
        catch (Exception error) { Log.LogWarning("Observer native MessagePack probe failed: " + error.GetType().Name); }
    }

    private void Add(string method, Type parameter, string callback)
    {
        var target = AccessTools.Method(typeof(ApiManager), method, new[] { parameter }) ?? throw new MissingMethodException();
        var prefix = AccessTools.Method(typeof(ProbePlugin), callback) ?? throw new MissingMethodException();
        harmony.Patch(target, prefix: new HarmonyMethod(prefix));
    }

    private static void Response(ApiManager __instance, ApiResponseData __0)
    {
        if (!active) return;
        try
        {
            var task = __instance._currentTask;
            if (task is null || __0 is null) return;
            var key = (__instance.Pointer, task.Pointer);
            pending.TryRemove(key, out _);
            var request = RequestIdentity.Read(task);
            if (request?.Route != "/api/user") return;
            if (pending.Count >= 64) pending.Clear();
            pending.TryAdd(key, new Pending(__0.ResponseCode, request));
        }
        catch (Exception error) { StopProbe(error); }
    }

    private static void Result(ApiManager __instance, HttpResult __0)
    {
        if (!active) return;
        try
        {
            var task = __instance._currentTask;
            if (task is null || !pending.TryRemove((__instance.Pointer, task.Pointer), out var response)) return;
            var request = RequestIdentity.Read(task);
            var passed = request is not null && request.Route == "/api/user" && request.Method == "GET"
                && response.Request.Method == request.Method && response.Request.Route == request.Route
                && response.Request.RequestObject.Pointer == request.RequestObject.Pointer
                && response.Status == 200 && __0?.success is not null;
            current?.Log.LogInfo("Observer native request probe: route=/api/user, all_checks_passed=" + passed);
            if (passed)
            {
                active = false;
                pending.Clear();
            }
        }
        catch (Exception error) { StopProbe(error); }
    }

    private static void StopProbe(Exception error)
    {
        active = false;
        pending.Clear();
        try { current?.Log.LogWarning("Observer native request probe failed: " + error.GetType().Name); }
        catch { }
    }

    private static JsonDocument Decode(string hex)
    {
        var bytes = new Il2CppStructArray<byte>(Convert.FromHexString(hex));
        var decoded = NativePayloadDecoder.DecodeMessagePack(bytes) ?? throw new InvalidDataException();
        return JsonDocument.Parse(decoded);
    }
}
