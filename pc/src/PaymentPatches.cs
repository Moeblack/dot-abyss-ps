using Cysharp.Threading.Tasks;
using Project.Common.Payment;
using Project.Api;
using Il2CppInterop.Runtime;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using GameCancellationToken = Il2CppSystem.Threading.CancellationToken;
using PurchaseTuple = Il2CppSystem.ValueTuple<string, Project.Common.Payment.AppPaymentCompleteModel>;

namespace DotAbyss.Local.Pc;

internal static class PaymentPatches
{
    private static int disabledReported;
    private static readonly object purchaseGate = new();
    private static readonly Dictionary<long, UniTask<PurchaseTuple>> pendingPurchases = new();

    internal static void Install(Plugin.AddHook add)
    {
        VerifyCompletionMarshalling();
        add(typeof(AppPaymentManager), "WarmUpAsync", new[] { typeof(GameCancellationToken) }, typeof(PaymentPatches), nameof(WarmUp));
        add(typeof(AppPaymentManager), "RecoveryAsync", new[] { typeof(GameCancellationToken) }, typeof(PaymentPatches), nameof(WarmUp));
        add(typeof(AppPaymentManager), "PurchaseAsync", new[] { typeof(long) }, typeof(PaymentPatches), nameof(Purchase));
    }

    internal static bool WarmUp(GameCancellationToken __0, ref UniTask __result)
    {
        try
        {
            __result = __0.IsCancellationRequested ? UniTask.FromCanceled(__0) : UniTask.CompletedTask;
            if (Interlocked.Exchange(ref disabledReported, 1) == 0)
                Plugin.Info("Local purchases use the isolated server; external billing initialization is disabled.");
        }
        catch (Exception error) { Plugin.Stop(error); }
        return false;
    }

    internal static bool Purchase(AppPaymentManager __instance, long __0, ref UniTask<PurchaseTuple> __result)
    {
        UniTaskCompletionSource<PurchaseTuple>? pending = null;
        try
        {
            var completion = new UniTaskCompletionSource<PurchaseTuple>();
            pending = completion;
            __result = completion.Task;
            lock (purchaseGate)
            {
                if (pendingPurchases.TryGetValue(__0, out var existing))
                {
                    __result = existing;
                    return false;
                }
                pendingPurchases.Add(__0, __result);
                __instance._isPurchasing = true;
            }
            var payment = __instance.GetPayment(__0) ?? throw new InvalidDataException("Local payment definition is unavailable.");
            var awaiter = PaymentApiDataStore.RequestGrantFreeAsync(payment.id, GameCancellationToken.None).GetAwaiter();
            void Finish()
            {
                try
                {
                    var response = awaiter.GetResult() ?? throw new InvalidDataException("Local purchase returned no result.");
                    var items = response.received_items;
                    var converted = new Il2CppReferenceArray<AppPaymentResultModel>(items?.Length ?? 0);
                    for (var index = 0; index < converted.Length; index++)
                    {
                        var item = items![index];
                        converted[index] = new AppPaymentResultModel { ContentType = item.content_type, ContentId = item.content_id, Amount = item.amount };
                    }
                    var model = new AppPaymentCompleteModel { Results = converted, GachaExecResponseEntity = response.exec_response };
                    FinishPurchase(__instance, __0);
                    CompletePurchase(completion, new PurchaseTuple("local-" + Guid.NewGuid().ToString("N"), model));
                }
                catch (Exception error)
                {
                    FinishPurchase(__instance, __0);
                    completion.TrySetException(new Il2CppSystem.InvalidOperationException("Local purchase failed."));
                    Plugin.Info("Local purchase failed (" + error.GetType().Name + ").");
                }
            }
            if (awaiter.IsCompleted) Finish();
            else awaiter.OnCompleted((Il2CppSystem.Action)(Action)Finish);
        }
        catch (Exception error)
        {
            FinishPurchase(__instance, __0);
            pending?.TrySetException(new Il2CppSystem.InvalidOperationException("Local purchase could not start."));
            Plugin.Info("Local purchase could not start (" + error.GetType().Name + ").");
        }
        return false;
    }

    private static void FinishPurchase(AppPaymentManager manager, long paymentId)
    {
        lock (purchaseGate)
        {
            pendingPurchases.Remove(paymentId);
            manager._isPurchasing = pendingPurchases.Count != 0;
        }
    }

    private static unsafe void CompletePurchase(UniTaskCompletionSource<PurchaseTuple> completion, PurchaseTuple result)
    {
        var method = IL2CPP.il2cpp_class_get_method_from_name(
            Il2CppClassPointerStore<UniTaskCompletionSource<PurchaseTuple>>.NativeClassPtr, "TrySetResult", 1);
        if (method == IntPtr.Zero) throw new MissingMethodException("Native purchase completion method is unavailable.");
        // Generic interop treats the boxed tuple as a reference, but native T requires its unboxed value.
        var arguments = stackalloc void*[1];
        arguments[0] = IL2CPP.il2cpp_object_unbox(result.Pointer).ToPointer();
        var exception = IntPtr.Zero;
        var returned = IL2CPP.il2cpp_runtime_invoke(method, completion.Pointer, arguments, ref exception);
        GC.KeepAlive(result);
        Il2CppException.RaiseExceptionIfNecessary(exception);
        if (returned == IntPtr.Zero || !*(bool*)IL2CPP.il2cpp_object_unbox(returned).ToPointer())
            throw new InvalidOperationException("Native purchase completion was already resolved.");
    }

    private static void VerifyCompletionMarshalling()
    {
        var model = new AppPaymentCompleteModel { Results = new Il2CppReferenceArray<AppPaymentResultModel>(0) };
        var completion = new UniTaskCompletionSource<PurchaseTuple>();
        var task = completion.Task;
        CompletePurchase(completion, new PurchaseTuple("local-marshalling-check", model));
        var result = task.GetAwaiter().GetResult();
        if (result is null || result.Item1 != "local-marshalling-check" || result.Item2?.Pointer != model.Pointer)
            throw new InvalidDataException("The native purchase task did not preserve its completion model.");
        Plugin.Info("Native purchase completion verified: receipt_matches=True, model_matches=True.");
    }
}
