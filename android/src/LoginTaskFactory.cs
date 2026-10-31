using Il2CppCysharp.Threading.Tasks;
using LoginTuple = Il2CppSystem.ValueTuple<long, string>;

namespace DotAbyss.Local;

internal static class LoginTaskFactory
{
    internal static UniTask<LoginTuple> Create(GameLoginData login)
    {
        var tuple = new LoginTuple(login.UserId, login.ServerTime);
        var stringPointer = NativeTupleInspection.StringPointer(tuple);
        var initial = NativeTupleInspection.Compare(tuple, login, stringPointer);
        NativeLoginProbe.RecordSourceTuple(tuple, stringPointer, initial);
        if (!initial.Matches) throw new InvalidDataException("The constructed native login tuple does not match its source values.");
        if (NativeLoginProbe.IsRunning) CaptureGenericConstructorBaseline(tuple);

        var task = new UniTask<LoginTuple>(source: null!, token: 0);
        // The generated T constructor passes boxed value-type proxies without unboxing them.
        task.result = tuple;
        var copied = NativeTupleInspection.Compare(task.result, login, stringPointer);
        if (NativeLoginProbe.IsRunning) NativeLoginProbe.RecordTuple("field_copy_task_result", task.result);
        if (!copied.Matches || task.source is not null || task.token != 0)
            throw new InvalidDataException("The completed native task did not preserve its login result.");
        return task;
    }

    private static void CaptureGenericConstructorBaseline(LoginTuple tuple)
    {
        var candidate = new UniTask<LoginTuple>(tuple);
        NativeLoginProbe.RecordTuple("generic_constructor_candidate", candidate.result);
    }
}
