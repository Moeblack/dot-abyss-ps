using System.Collections.Concurrent;
using System.Diagnostics;
using Absf.Api;
using UnityEngine;

namespace DotAbyss.Observer.Pc;

internal static class CaptureHooks
{
    private readonly record struct TaskKey(IntPtr Manager, IntPtr Task);
    private sealed record PendingResponse(long StartedAt, long Status, RequestIdentity Request);
    private static readonly ConcurrentDictionary<TaskKey, PendingResponse> pending = new();

    internal static void Response(ApiManager __instance, ApiResponseData __0)
    {
        if (!Plugin.CanCapture) return;
        try
        {
            var task = __instance._currentTask;
            if (task is null || __0 is null) return;
            var key = new TaskKey(__instance.Pointer, task.Pointer);
            pending.TryRemove(key, out _);
            var request = RequestIdentity.Read(task);
            if (request is null) return;
            if (pending.Count >= 128) pending.Clear();
            pending.TryAdd(key, new PendingResponse(Stopwatch.GetTimestamp(), __0.ResponseCode, request));
        }
        catch (Exception error) { Plugin.Disable(error); }
    }

    internal static void Result(ApiManager __instance, HttpResult __0)
    {
        if (!Plugin.CanCapture) return;
        try
        {
            var task = __instance._currentTask;
            if (task is null || !pending.TryRemove(new TaskKey(__instance.Pointer, task.Pointer), out var response)) return;
            if ((Stopwatch.GetTimestamp() - response.StartedAt) / (double)Stopwatch.Frequency > 30) return;
            if (__0?.success is not { } success) return;
            var identity = RequestIdentity.Read(task);
            if (identity is null || identity.Method != response.Request.Method || identity.Route != response.Request.Route
                || identity.RequestObject.Pointer != response.Request.RequestObject.Pointer) return;
            var json = NativePayloadDecoder.Decode(success);
            if (json is null) return;
            string? requestJson = null;
            if (Plugin.CaptureRequests)
                requestJson = NativePayloadDecoder.BoundedJson(JsonUtility.ToJson(identity.RequestObject));
            var sink = Plugin.Sink;
            if (!sink.TryCapture(new CaptureInput(identity.Method, identity.Route, response.Status, json,
                    requestJson, success.responseFormat.ToString())) && sink.Snapshot.Faulted)
                Plugin.Disable(new IOException());
        }
        catch (Exception error) { Plugin.Disable(error); }
    }

    internal static void Clear() => pending.Clear();
}
