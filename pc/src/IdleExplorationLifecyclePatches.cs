using Project.Common;
using Project.IdleExploration;
using Project.Outgame;
using GameCancellationToken = Il2CppSystem.Threading.CancellationToken;

namespace DotAbyss.Local.Pc;

internal static class IdleExplorationLifecyclePatches
{
    private static int skippedCallbacks;
    private static int cancellationFailures;

    internal static void Install(Plugin.AddHook add)
    {
        add(typeof(CharaContainer), "SetAttachment", new[] { typeof(CharacterAttachmentAssetData) },
            typeof(IdleExplorationLifecyclePatches), nameof(SetAttachment));
        add(typeof(IdleExplorationStageView), "UpdateView", new[] { typeof(IdleExplorationStageModel), typeof(GameCancellationToken) },
            typeof(IdleExplorationLifecyclePatches), nameof(UpdateView));
        add(typeof(IdleExplorationStageView), "OnDestroy", Type.EmptyTypes,
            typeof(IdleExplorationLifecyclePatches), nameof(Destroy));
    }

    internal static bool SetAttachment(CharaContainer __instance, CharacterAttachmentAssetData __0)
    {
        if (!Plugin.Active) return true;
        // Unity can destroy a native object while its managed proxy remains non-null.
        if ((__instance is not null && !__instance) || (__0 is not null && !__0))
        {
            LogSkipped("character attachment");
            return false;
        }
        return true;
    }

    internal static bool UpdateView(IdleExplorationStageView __instance, GameCancellationToken __1)
    {
        if (!Plugin.Active) return true;
        if ((__instance is not null && !__instance) || (__1 is not null && __1.IsCancellationRequested))
        {
            LogSkipped("exploration update");
            return false;
        }
        return true;
    }

    internal static void Destroy(IdleExplorationStageView __instance)
    {
        if (!Plugin.Active || __instance is null) return;
        try
        {
            var cancellation = __instance._cts;
            if (cancellation is not null && !cancellation.IsCancellationRequested) cancellation.Cancel();
        }
        catch (Exception error)
        {
            if (Interlocked.Increment(ref cancellationFailures) <= 3)
                Plugin.Info("Exploration cleanup cancellation failed (" + error.GetType().Name + ").");
        }
    }

    private static void LogSkipped(string operation)
    {
        if (Interlocked.Increment(ref skippedCallbacks) <= 8)
            Plugin.Info("Skipped expired " + operation + " callback.");
    }
}
