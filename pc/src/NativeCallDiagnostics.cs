using System.Runtime.InteropServices;
using Il2CppInterop.Runtime;
using GameCancellationToken = Il2CppSystem.Threading.CancellationToken;

namespace DotAbyss.Local.Pc;

internal static class NativeCallDiagnostics
{
    internal static void CheckCancellation(string scope, GameCancellationToken cancellation,
        Il2CppSystem.Action? onSuccess, Il2CppSystem.Action? onError)
    {
        Plugin.Info($"Platform entry: {scope}, token_proxy={cancellation is not null}, success_callback={onSuccess is not null}, error_callback={onError is not null}");
        if (cancellation is null) throw new InvalidDataException("The native cancellation value was not marshalled.");
        try { cancellation.ThrowIfCancellationRequested(); }
        catch (Exception error)
        {
            Plugin.Info($"Native cancellation failure: {scope}, managed_type={error.GetType().FullName}, native_summary={ExceptionFrames.NativeCancellationMessage(error.Message)}");
            Describe("incoming", cancellation, onSuccess, onError);
            try
            {
                var uncanceled = new GameCancellationToken(false);
                Describe("constructed_uncanceled", uncanceled, null, null);
                uncanceled.ThrowIfCancellationRequested();
                Plugin.Info("Constructed uncanceled token ThrowIfCancellationRequested returned normally.");
            }
            catch (Exception baselineError)
            {
                Plugin.Info($"Constructed token check failed: {baselineError.GetType().FullName}, native_summary={ExceptionFrames.NativeCancellationMessage(baselineError.Message)}");
            }
            throw;
        }
    }

    private static void Describe(string label, GameCancellationToken cancellation,
        Il2CppSystem.Action? onSuccess, Il2CppSystem.Action? onError)
    {
        try
        {
            var value = IL2CPP.il2cpp_object_unbox(cancellation.Pointer);
            var source = value == IntPtr.Zero ? IntPtr.Zero : Marshal.ReadIntPtr(value);
            Plugin.Info($"Cancellation layout: {label}, boxed_value={value != IntPtr.Zero}, source_present={source != IntPtr.Zero}, source_equals_success={source != IntPtr.Zero && onSuccess is not null && source == onSuccess.Pointer}, source_equals_error={source != IntPtr.Zero && onError is not null && source == onError.Pointer}");
        }
        catch (Exception error)
        {
            Plugin.Info($"Cancellation inspection failed: {label}, type={error.GetType().FullName}, native_summary={ExceptionFrames.NativeCancellationMessage(error.Message)}");
        }
    }
}
