using DotAbyss.Local.Pc;
using Project.Common;
using Project.IdleExploration;
using Project.Outgame;
using GameToken = Il2CppSystem.Threading.CancellationToken;
using GameSource = Il2CppSystem.Threading.CancellationTokenSource;

var checks = 0;
void Check(bool condition, string name)
{
    if (!condition) throw new InvalidOperationException(name);
    checks++;
}

var hooks = new List<(Type Owner, string Method, Type[] Parameters, bool Postfix)>();
IdleExplorationLifecyclePatches.Install((owner, method, parameters, patchOwner, patch, postfix) =>
{
    Check(patchOwner == typeof(IdleExplorationLifecyclePatches), "Lifecycle hooks use the production patch class");
    Check(patchOwner.GetMethod(patch, System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic) is not null,
        "Every registered callback exists");
    hooks.Add((owner, method, parameters, postfix));
});
Check(hooks.Count == 3 && hooks.All(hook => !hook.Postfix), "Guard and cancellation hooks run before native methods");
Check(hooks.Any(hook => hook.Owner == typeof(CharaContainer) && hook.Method == "SetAttachment"
    && hook.Parameters.SequenceEqual(new[] { typeof(CharacterAttachmentAssetData) })), "Shared attachment signature is exact");
Check(hooks.Any(hook => hook.Owner == typeof(IdleExplorationStageView) && hook.Method == "UpdateView"
    && hook.Parameters.SequenceEqual(new[] { typeof(IdleExplorationStageModel), typeof(GameToken) })), "Exploration update signature is exact");
Check(hooks.Any(hook => hook.Method == "OnDestroy" && hook.Parameters.Length == 0), "Native cleanup remains registered");

var container = new CharaContainer();
var attachment = new CharacterAttachmentAssetData();
Check(IdleExplorationLifecyclePatches.SetAttachment(container, attachment), "Live character attachments reach the native implementation");
Check(IdleExplorationLifecyclePatches.SetAttachment(container, null!), "Null attachment keeps native validation semantics");
Check(IdleExplorationLifecyclePatches.SetAttachment(null!, attachment), "Null receiver keeps native validation semantics");
attachment.Alive = false;
Check(!IdleExplorationLifecyclePatches.SetAttachment(container, attachment), "Destroyed attachment proxy is rejected");
attachment.Alive = true;
container.Alive = false;
Check(!IdleExplorationLifecyclePatches.SetAttachment(container, attachment), "Destroyed character receiver is rejected");
container.Alive = true;
attachment.InspectionError = new InvalidDataException("native-inspection-failure");
try
{
    IdleExplorationLifecyclePatches.SetAttachment(container, attachment);
    throw new InvalidOperationException("Unexpected native errors must remain visible");
}
catch (InvalidDataException) { checks++; }
attachment.InspectionError = null;

var source = new GameSource();
var view = new IdleExplorationStageView { _cts = source };
var token = new GameToken();
Check(IdleExplorationLifecyclePatches.UpdateView(view, token), "Live exploration updates reach the native implementation");
Check(IdleExplorationLifecyclePatches.UpdateView(view, null!), "Missing token is left to native validation");
token.IsCancellationRequested = true;
Check(!IdleExplorationLifecyclePatches.UpdateView(view, token), "Cancelled update does not create another linked task");
token.IsCancellationRequested = false;
view.Alive = false;
Check(!IdleExplorationLifecyclePatches.UpdateView(view, token), "Destroyed exploration view does not start work");

var continued = false;
source.OnCancel = () => continued = true;
IdleExplorationLifecyclePatches.Destroy(view);
Check(source.CancelCalls == 1 && source.IsCancellationRequested && continued, "Cleanup cancels outstanding work even after Unity invalidates the view");
Check(ReferenceEquals(view._cts, source), "Cleanup preserves the native-owned source");
IdleExplorationLifecyclePatches.Destroy(view);
Check(source.CancelCalls == 1, "Repeated cleanup does not cancel twice");
IdleExplorationLifecyclePatches.Destroy(new IdleExplorationStageView());
IdleExplorationLifecyclePatches.Destroy(null!);
checks += 2;

var failing = new GameSource { CancelError = new InvalidOperationException("callback-failed") };
IdleExplorationLifecyclePatches.Destroy(new IdleExplorationStageView { _cts = failing });
Check(failing.CancelCalls == 1, "Cancellation callback failure does not abort native cleanup");
Check(Plugin.Messages.Any(message => message.Contains("InvalidOperationException")), "Cleanup failure has a bounded diagnostic");

Plugin.Active = false;
var inactiveSource = new GameSource();
var inactiveView = new IdleExplorationStageView { _cts = inactiveSource, Alive = false };
Check(IdleExplorationLifecyclePatches.SetAttachment(new CharaContainer { Alive = false }, attachment), "Inactive plugin leaves attachment calls alone");
Check(IdleExplorationLifecyclePatches.UpdateView(inactiveView, new GameToken { IsCancellationRequested = true }), "Inactive plugin leaves update calls alone");
IdleExplorationLifecyclePatches.Destroy(inactiveView);
Check(inactiveSource.CancelCalls == 0, "Inactive plugin leaves native cancellation alone");
Plugin.Active = true;

for (var index = 0; index < 30; index++)
{
    IdleExplorationLifecyclePatches.SetAttachment(new CharaContainer { Alive = false }, attachment);
    IdleExplorationLifecyclePatches.Destroy(new IdleExplorationStageView { _cts = new GameSource { CancelError = new InvalidOperationException() } });
}
Check(Plugin.Messages.Count(message => message.StartsWith("Skipped expired")) == 8, "Expired callback diagnostics are bounded");
Check(Plugin.Messages.Count(message => message.StartsWith("Exploration cleanup")) == 3, "Cancellation error diagnostics are bounded");
Console.WriteLine($"Lifecycle checks passed: {checks}");
