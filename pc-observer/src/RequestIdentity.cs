using Absf.Api;
using Il2CppInterop.Runtime;

namespace DotAbyss.Observer.Pc;

internal sealed record RequestIdentity(string Method, string Route, Il2CppSystem.Object RequestObject)
{
    internal static RequestIdentity? Read(IApiRequestTask task)
    {
        if (task.Pointer == IntPtr.Zero) return null;
        var type = IL2CPP.il2cpp_object_get_class(task.Pointer);
        if (type == IntPtr.Zero) return null;
        var field = IL2CPP.il2cpp_class_get_field_from_name(type, "_request");
        if (field == IntPtr.Zero) return null;
        var pointer = IL2CPP.il2cpp_field_get_value_object(field, task.Pointer);
        if (pointer == IntPtr.Zero) return null;
        var value = new Il2CppSystem.Object(pointer);
        var request = value.TryCast<IApiRequest>();
        if (request is null || !CapturePolicy.TryNormalizeRoute(request.ApiName, out var route)) return null;
        var method = CapturePolicy.NormalizeMethod(request.Method.ToString());
        return method == "UNKNOWN" ? null : new RequestIdentity(method, route, value);
    }
}
