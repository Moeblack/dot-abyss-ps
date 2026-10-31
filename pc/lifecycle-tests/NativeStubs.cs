namespace UnityEngine
{
    internal class Object
    {
        internal bool Alive { get; set; } = true;
        internal Exception? InspectionError { get; set; }

        public static implicit operator bool(Object? value)
        {
            if (value?.InspectionError is { } error) throw error;
            return value?.Alive == true;
        }

        public static bool operator !(Object? value) => !(bool)value;
    }
}

namespace Project.Common
{
    internal sealed class CharacterAttachmentAssetData : UnityEngine.Object;
}

namespace Project.Outgame
{
    internal sealed class CharaContainer : UnityEngine.Object;
}

namespace Project.IdleExploration
{
    internal sealed class IdleExplorationStageModel;
    internal sealed class IdleExplorationStageView : UnityEngine.Object
    {
        internal Il2CppSystem.Threading.CancellationTokenSource? _cts;
    }
}

namespace Il2CppSystem.Threading
{
    internal sealed class CancellationToken
    {
        internal bool IsCancellationRequested { get; set; }
    }

    internal sealed class CancellationTokenSource
    {
        internal bool IsCancellationRequested { get; set; }
        internal int CancelCalls { get; private set; }
        internal Exception? CancelError { get; set; }
        internal Action? OnCancel { get; set; }

        internal void Cancel()
        {
            CancelCalls++;
            IsCancellationRequested = true;
            OnCancel?.Invoke();
            if (CancelError is { } error) throw error;
        }
    }
}

namespace DotAbyss.Local.Pc
{
    internal static class Plugin
    {
        internal static bool Active { get; set; } = true;
        internal static List<string> Messages { get; } = new();
        internal static void Info(string message) => Messages.Add(message);
        internal delegate void AddHook(Type owner, string method, Type[] parameters, Type patchOwner, string patch, bool postfix = false);
    }
}
